#!/usr/bin/env python3
"""Phase 2: digest a jstack thread dump.

Groups threads by state and by top frame, resolves monitor contention (who holds what,
who waits for it), and summarises Ignite thread pool occupancy.

A thread dump is ONE INSTANT. It cannot show duration, and it cannot show a stall that
had already ended when the dump was taken. Check its timestamp against the incident
window before drawing anything from it; the digest prints the timestamp first for that
reason.

Stdlib only.
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

RE_HEADER = re.compile(r'^"(?P<name>.*)"\s+(?P<attrs>.*)$')
RE_STATE = re.compile(r"java\.lang\.Thread\.State:\s*(?P<state>[A-Z_]+)"
                      r"(?:\s*\((?P<detail>[^)]*)\))?")
RE_FRAME = re.compile(r"^\s+at\s+(?P<frame>.+?)\s*$")   # keep "(Native Method)" intact
RE_LOCKED = re.compile(r"^\s+-\s+locked\s+<(?P<id>0x[0-9a-fA-F]+)>\s*\(a (?P<cls>[^)]+)\)")
RE_WAITING = re.compile(r"^\s+-\s+waiting to lock\s+<(?P<id>0x[0-9a-fA-F]+)>\s*\(a (?P<cls>[^)]+)\)")
RE_PARKING = re.compile(r"^\s+-\s+parking to wait for\s+<(?P<id>0x[0-9a-fA-F]+)>")
RE_DUMPTIME = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
RE_VM = re.compile(r"^Full thread dump (.+):")
RE_CPU = re.compile(r"cpu=([\d.]+)ms")
RE_ELAPSED = re.compile(r"elapsed=([\d.]+)s")
RE_NID = re.compile(r"nid=(\S+)")

# Ignite pools worth counting separately.
POOLS = [
    ("sys-stripe", re.compile(r"^sys-stripe-\d+")),
    ("sys", re.compile(r"^sys-#?\d*")),
    ("pub", re.compile(r"^pub-#?\d*")),
    ("data-streamer", re.compile(r"^data-streamer")),
    ("grid-nio-worker", re.compile(r"^grid-nio-worker")),
    ("tcp-comm-worker", re.compile(r"^tcp-comm-worker")),
    ("exchange-worker", re.compile(r"^exchange-worker")),
    ("db-checkpoint-thread", re.compile(r"^db-checkpoint-thread")),
    ("wal", re.compile(r"^wal-")),
    ("tcp-disco", re.compile(r"^tcp-disco")),
    ("rebalance", re.compile(r"^rebalance-")),
    ("query", re.compile(r"^query-#?\d*")),
    ("callback", re.compile(r"^callback-#?\d*")),
    ("utility", re.compile(r"^utility-#?\d*")),
]

# Frames that mean "waiting for someone else" rather than "doing the work".
IDLE_FRAMES = re.compile(
    r"(?:Unsafe\.park|Object\.wait|Thread\.sleep|epollWait|EPoll\.wait|Net\.poll|"
    r"LockSupport\.park|takeTask|SynchronousQueue|LinkedBlockingQueue\.take)")


class Thread(object):
    def __init__(self, name, attrs):
        self.name = name
        self.attrs = attrs
        self.state = None
        self.detail = None
        self.frames = []
        self.locked = []      # (id, cls)
        self.waiting = []     # (id, cls)
        self.parking = []
        self.line = 0

    @property
    def top(self):
        return self.frames[0] if self.frames else "(no frames)"

    @property
    def cpu_ms(self):
        m = RE_CPU.search(self.attrs)
        return float(m.group(1)) if m else None

    @property
    def idle(self):
        return bool(self.frames) and bool(IDLE_FRAMES.search(self.frames[0]))


def parse_dump(path):
    threads = []
    dump_time = None
    vm = None
    cur = None
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            if dump_time is None:
                m = RE_DUMPTIME.match(line)
                if m:
                    dump_time = m.group(1)
            if vm is None:
                m = RE_VM.match(line)
                if m:
                    vm = m.group(1)
            m = RE_HEADER.match(line)
            if m:
                cur = Thread(m.group("name"), m.group("attrs"))
                cur.line = lineno
                threads.append(cur)
                continue
            if cur is None:
                continue
            m = RE_STATE.search(line)
            if m:
                cur.state = m.group("state")
                cur.detail = m.group("detail")
                continue
            m = RE_FRAME.match(line)
            if m:
                cur.frames.append(m.group("frame"))
                continue
            m = RE_LOCKED.match(line)
            if m:
                cur.locked.append((m.group("id"), m.group("cls")))
                continue
            m = RE_WAITING.match(line)
            if m:
                cur.waiting.append((m.group("id"), m.group("cls")))
                continue
            m = RE_PARKING.match(line)
            if m:
                cur.parking.append(m.group("id"))
    return threads, dump_time, vm


def short(frame, n=90):
    frame = frame.replace("java.base@", "").replace("(", " (")
    return frame if len(frame) <= n else frame[:n - 1] + "..."


def pool_of(name):
    for label, rx in POOLS:
        if rx.match(name):
            return label
    return None


def render(threads, dump_time, vm, path, args, out):
    w = out.write
    w("# 20 - Thread dump digest (Phase 2)\n\n")
    w("| | |\n|---|---|\n")
    w("| File | `%s` |\n" % path.name)
    w("| Dump taken at | **%s** |\n" % (dump_time or "NOT STATED IN FILE"))
    w("| VM | %s |\n" % (vm or "?"))
    w("| Threads | %d |\n\n" % len(threads))

    w("> **This is one instant.** Compare the dump time above against your incident\n"
      "> window before using anything below. A dump taken after the JVM resumed will look\n"
      "> healthy, and that appearance is not evidence that nothing happened.\n\n")

    # ---- states ---------------------------------------------------------- #
    states = Counter(t.state or "(unparsed)" for t in threads)
    w("## Thread states\n\n| state | count |\n|---|---|\n")
    for s, n in states.most_common():
        w("| %s | %d |\n" % (s, n))
    w("\n")

    # ---- monitor contention ---------------------------------------------- #
    owner_of = {}
    for t in threads:
        for lock_id, _cls in t.locked:
            owner_of[lock_id] = t
    waiters = defaultdict(list)
    for t in threads:
        for lock_id, _cls in t.waiting:
            waiters[lock_id].append(t)

    w("## Monitor contention\n\n")
    if not waiters:
        w("No thread is `waiting to lock` a monitor. Note that this does NOT rule out\n"
          "contention on `java.util.concurrent` locks - those show as `parking to wait for`\n"
          "and are listed separately below.\n\n")
    else:
        w("| monitor | class | waiters | owner | owner's top frame | owner state |\n"
          "|---|---|---|---|---|---|\n")
        for lock_id, ws in sorted(waiters.items(), key=lambda x: -len(x[1])):
            owner = owner_of.get(lock_id)
            cls = ws[0].waiting[0][1] if ws[0].waiting else "?"
            w("| `%s` | %s | **%d** | %s | %s | %s |\n" % (
                lock_id, cls, len(ws),
                "`%s`" % owner.name if owner else "**NOT IN DUMP**",
                short(owner.top, 60) if owner else "-",
                owner.state if owner else "-"))
        w("\n**The owner's stack is the bottleneck**, not the waiters'. If the owner is not\n"
          "in the dump, the lock was released between frames being captured, or the owner\n"
          "is a VM-internal thread.\n\n")
        for lock_id, ws in sorted(waiters.items(), key=lambda x: -len(x[1]))[:3]:
            owner = owner_of.get(lock_id)
            if not owner:
                continue
            w("### Owner of `%s`: `%s`\n\n" % (lock_id, owner.name))
            w("State: %s%s\n\n```\n" % (owner.state, " (%s)" % owner.detail if owner.detail else ""))
            for f in owner.frames[:12]:
                w("    at %s\n" % f)
            w("```\n\n")
            w("Waiting on it: %s\n\n" % ", ".join("`%s`" % t.name for t in ws[:12]))

    parked = defaultdict(list)
    for t in threads:
        for pid in t.parking:
            parked[pid].append(t)
    multi = {k: v for k, v in parked.items() if len(v) > 1}
    if multi:
        w("## j.u.c. locks with multiple waiters\n\n| lock | waiters |\n|---|---|\n")
        for lock_id, ws in sorted(multi.items(), key=lambda x: -len(x[1]))[:10]:
            w("| `%s` | %d (%s) |\n" % (lock_id, len(ws),
                                        ", ".join(t.name for t in ws[:5])))
        w("\nThese have no recorded owner in a thread dump - JUC locks do not report one.\n"
          "Identify the holder from what the waiters are trying to do.\n\n")

    # ---- top frames ------------------------------------------------------ #
    w("## Threads grouped by top frame\n\n")
    groups = defaultdict(list)
    for t in threads:
        groups[t.top].append(t)
    rows = sorted(groups.items(), key=lambda x: -len(x[1]))
    w("| count | state(s) | top frame | example thread |\n|---|---|---|---|\n")
    for frame, ts in rows[:args.top]:
        sts = ",".join(sorted({t.state or "?" for t in ts}))
        w("| %d | %s | `%s` | %s |\n" % (len(ts), sts, short(frame), ts[0].name))
    w("\n")

    # ---- ignite pools ---------------------------------------------------- #
    pools = defaultdict(lambda: Counter())
    pool_busy = defaultdict(list)
    for t in threads:
        p = pool_of(t.name)
        if not p:
            continue
        pools[p][t.state or "?"] += 1
        if not t.idle:
            pool_busy[p].append(t)
    if pools:
        w("## Ignite pool occupancy\n\n")
        w("| pool | threads | busy (not parked/waiting for work) | states |\n|---|---|---|---|\n")
        for p in sorted(pools, key=lambda x: -sum(pools[x].values())):
            total = sum(pools[p].values())
            busy = len(pool_busy.get(p, []))
            sts = ", ".join("%s=%d" % (s, n) for s, n in pools[p].most_common())
            marker = " **saturated**" if total and busy == total and total > 1 else ""
            w("| %s | %d | %d%s | %s |\n" % (p, total, busy, marker, sts))
        w("\nA pool where every thread is busy or blocked is a pool that is queueing work.\n"
          "Follow what those threads are blocked on rather than reporting the pool itself.\n\n")

    # ---- discovery threads ----------------------------------------------- #
    disco = [t for t in threads if t.name.startswith("tcp-disco")]
    w("## Discovery threads\n\n")
    if not disco:
        w("**No `tcp-disco-*` threads in the dump.** For a server node that is abnormal -\n"
          "either the node was already shutting down, or the dump is partial.\n\n")
    else:
        w("| thread | state | top frame |\n|---|---|---|\n")
        for t in disco:
            w("| `%s` | %s | `%s` |\n" % (t.name, t.state, short(t.top, 70)))
        w("\nA `tcp-disco-msg-worker` sitting in `Net.poll` / `epollWait` is idle and normal.\n"
          "One that is BLOCKED, or missing entirely, is directly relevant to segmentation.\n\n")

    # ---- cpu hogs -------------------------------------------------------- #
    withcpu = [t for t in threads if t.cpu_ms is not None]
    if withcpu:
        w("## Highest accumulated CPU\n\n")
        w("| thread | cpu | state | top frame |\n|---|---|---|---|\n")
        for t in sorted(withcpu, key=lambda x: -x.cpu_ms)[:10]:
            w("| `%s` | %.1f s | %s | `%s` |\n"
              % (t.name, t.cpu_ms / 1000.0, t.state, short(t.top, 60)))
        w("\nThis is CPU accumulated since the thread started, not during the incident.\n"
          "Use it to spot a thread doing far more work than its peers, not as a rate.\n\n")

    w("## Phase 2 gate (thread dump portion)\n\n")
    w("Record in `20-resource-findings.md`:\n\n")
    w("1. The dump timestamp relative to the incident window - before, during, or after.\n")
    w("2. If threads are blocked: the owner thread and what IT was doing.\n")
    w("3. Whether the discovery threads were healthy at that instant.\n")
    w("4. What the dump does NOT show (duration, anything outside this instant).\n\n")


EXPLAIN = """\
threaddump_digest.py - Phase 2 digest of a jstack thread dump.

WHAT IT DOES
  Parses threads, groups them by state and top frame, resolves monitor contention
  (which thread holds the monitor that others wait for, and prints that owner's stack),
  lists j.u.c. locks with multiple waiters, summarises Ignite pool occupancy, checks the
  discovery threads specifically, and ranks accumulated CPU.

WHY IT EXISTS
  The useful content of a 400-thread dump is: who is blocked, on what, and who holds it.
  Everything else is repetition of idle pool threads.

THE LIMIT YOU MUST RESPECT
  A dump is ONE INSTANT. It shows no durations and cannot show a stall that had already
  ended. The digest prints the dump timestamp first so you can check it against the
  incident window before using anything else.

ASSUMPTIONS
  - HotSpot jstack format ("name" #id ... / java.lang.Thread.State: ... / at frames /
    - locked <0x..> / - waiting to lock <0x..>).
  - 'Busy' means the top frame is not a park/wait/poll idle frame; it is a heuristic.

USAGE
  python threaddump_digest.py --inventory inventory.json --out 20-threads.md
  python threaddump_digest.py threaddump.txt --top 25
"""


def collect_inputs(args):
    paths = []
    if args.inventory:
        data = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
        base = Path(data["root"])
        for f in data["files"]:
            if f["kind"] == "thread_dump":
                if args.node and f.get("node") != args.node:
                    continue
                paths.append(base / f["path"])
    for t in args.targets:
        p = Path(t)
        if p.is_dir():
            paths += [q for q in sorted(p.rglob("*")) if q.is_file()]
        elif p.is_file():
            paths.append(p)
    return paths


def main():
    ap = argparse.ArgumentParser(description="Phase 2: thread dump digest.")
    ap.add_argument("targets", nargs="*", default=[])
    ap.add_argument("--inventory")
    ap.add_argument("--node")
    ap.add_argument("--out")
    ap.add_argument("--top", type=int, default=20, help="rows in the top-frame table")
    ap.add_argument("--explain", action="store_true")
    args = ap.parse_args()

    if args.explain:
        print(EXPLAIN)
        return 0

    paths = collect_inputs(args)
    if not paths:
        print("error: no thread dumps. Run identify.py and pass --inventory.", file=sys.stderr)
        return 1

    fh_out = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
    try:
        for p in paths:
            threads, dump_time, vm = parse_dump(p)
            if not threads:
                print("warning: %s contained no parseable threads" % p.name, file=sys.stderr)
                continue
            render(threads, dump_time, vm, p, args, fh_out)
    finally:
        if args.out:
            fh_out.close()
            print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
