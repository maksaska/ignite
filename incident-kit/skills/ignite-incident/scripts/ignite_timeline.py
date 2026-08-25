#!/usr/bin/env python3
"""Phase 1: build a cross-node timeline from Ignite node logs.

Reads every file classified as ignite_log and emits a BOUNDED digest: the events that
matter for an incident, merged across nodes in chronological order, plus log gaps,
topology history, checkpoint timings and first-occurrence exceptions.

The point is that you never read the raw logs. If something in the digest needs
following up, it carries file:line so you can read exactly that.

IMPORTANT: run this over ALL nodes, not just the node that failed. A segmented or
killed node's own log records the CONSEQUENCE; the DECISION is almost always visible
in the coordinator's and the ring-neighbour's logs.

Stdlib only.
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# --------------------------------------------------------------------------- #
# Line grammar. Site log4j patterns vary; we degrade gracefully.
# --------------------------------------------------------------------------- #

# Ignite thread names routinely contain their own brackets -- tcp-disco-msg-worker-[crd]-#2,
# tcp-disco-sock-reader-[]-#4 -- so the thread group must tolerate one nesting level.
RE_FULL = re.compile(
    r"^\[(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}:\d{2})[,.](?P<ms>\d{3})\]"
    r"\[\s*(?P<level>[A-Z]+)\s*\]"
    r"\[(?P<thread>(?:[^\]\[]|\[[^\]\[]*\])*)\]"
    r"\[(?P<cat>[^\]\[]*)\]\s*(?P<msg>.*)$")

RE_TS_ONLY = re.compile(
    r"^\[?(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}:\d{2})[,.](?P<ms>\d{3})\]?"
    r"\s*(?P<rest>.*)$")

RE_LEVEL_LOOSE = re.compile(r"\b(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL)\b")

# --------------------------------------------------------------------------- #
# Event catalogue. (category, severity, regex, short-label extractor)
# severity: 3 = decisive, 2 = strong signal, 1 = context
# --------------------------------------------------------------------------- #

EVENTS = [
    ("segmentation", 3, re.compile(r"Local node SEGMENTED")),
    ("segmentation", 3, re.compile(r"Node is out of topology")),
    ("failure", 3, re.compile(r"Critical system error detected")),
    ("failure", 3, re.compile(r"JVM will be halted immediately")),
    ("failure", 3, re.compile(r"Blocked system-critical thread")),
    ("failure", 2, re.compile(r"Possible failure suppressed")),
    ("failure", 3, re.compile(r"Stopping local node")),
    ("topology", 3, re.compile(r"Node FAILED")),
    ("topology", 2, re.compile(r"Node LEFT|Node left topology")),
    ("topology", 2, re.compile(r"Added new node to topology|Node JOINED")),
    ("topology", 1, re.compile(r"Topology snapshot \[")),
    ("topology", 2, re.compile(r"Coordinator changed")),
    ("discovery", 3, re.compile(r"Local node has detected failed nodes")),
    ("discovery", 2, re.compile(r"Failed to send message to next node")),
    ("discovery", 2, re.compile(r"Socket write has timed out")),
    ("discovery", 2, re.compile(r"Failed to ping node|Failed to connect to any address")),
    ("discovery", 2, re.compile(r"Ring message worker|message worker blocked")),
    ("jvm_pause", 3, re.compile(r"Possible too long JVM pause")),
    ("pme", 2, re.compile(r"Unable to await partitions release latch")),
    ("pme", 3, re.compile(r"Failed to wait for partition map exchange")),
    ("pme", 1, re.compile(r"Started exchange init")),
    ("pme", 1, re.compile(r"Finished exchange init")),
    ("checkpoint", 1, re.compile(r"Checkpoint started")),
    ("checkpoint", 1, re.compile(r"Checkpoint finished")),
    ("checkpoint", 2, re.compile(r"Throttling is applied to page modifications")),
    ("checkpoint", 2, re.compile(r"Checkpoint read lock acquisition has been timed out")),
    ("rebalance", 1, re.compile(r"Starting rebalance routine|Rebalancing complete|Completed rebalanc")),
    ("cluster_state", 2, re.compile(r"Cluster state changed|activation|deactivat", re.I)),
    ("oom", 3, re.compile(r"OutOfMemoryError|Out of memory")),
]

RE_TOPVER = re.compile(r"Topology snapshot \[ver=(\d+), .*?servers=(\d+), clients=(\d+), state=(\w+)")
RE_CP_FINISH = re.compile(r"Checkpoint finished \[.*?total=(\d+)ms", re.S)
RE_CP_PARTS = re.compile(r"(markDuration|pagesWrite|fsync|walCpRecordFsyncDuration|listenersExecuteTime)=(\d+)ms")
RE_CP_PAGES = re.compile(r"pages=(\d+)")
RE_JVM_PAUSE_MS = re.compile(r"Possible too long JVM pause: (\d+) milliseconds")
RE_EXC = re.compile(r"\b((?:[a-z][\w]*\.)+[A-Z]\w*(?:Exception|Error|Throwable))\b")
RE_LATCH_ACK = re.compile(r"pendingAcks=\w*\s*\[([^\]]*)\]")


def parse_line(line):
    m = RE_FULL.match(line)
    if m:
        return (m.group("date"), m.group("time"), m.group("ms"),
                m.group("level").strip(), m.group("thread"), m.group("cat"), m.group("msg"))
    m = RE_TS_ONLY.match(line)
    if m:
        rest = m.group("rest")
        lm = RE_LEVEL_LOOSE.search(rest[:80])
        return (m.group("date"), m.group("time"), m.group("ms"),
                lm.group(1) if lm else "", "", "", rest)
    return None


def to_dt(date, time, ms):
    return datetime.strptime("%s %s.%s" % (date, time, ms), "%Y-%m-%d %H:%M:%S.%f")


def scan_log(path, node, window):
    """Yield events, gaps, topology history, checkpoints and exceptions for one log."""
    events = []
    gaps = []
    topo = []
    cps = []
    excs = {}
    prev_dt = None
    prev_line = 0
    counts = Counter()

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            p = parse_line(line)
            if p is None:
                # continuation (stack trace etc.) - only mine it for exception classes
                for cls in RE_EXC.findall(line):
                    if cls not in excs:
                        excs[cls] = (prev_dt, lineno)
                continue
            date, time, ms, level, thread, cat, msg = p
            try:
                dt = to_dt(date, time, ms)
            except ValueError:
                continue

            if prev_dt is not None:
                delta = (dt - prev_dt).total_seconds()
                if delta >= window["gap"]:
                    gaps.append({"node": node, "start": prev_dt, "end": dt,
                                 "seconds": delta, "line": lineno, "before_line": prev_line})
            prev_dt = dt
            prev_line = lineno

            if window["start"] and dt < window["start"]:
                continue
            if window["end"] and dt > window["end"]:
                continue

            tm = RE_TOPVER.search(msg)
            if tm:
                topo.append({"node": node, "dt": dt, "ver": int(tm.group(1)),
                             "servers": int(tm.group(2)), "clients": int(tm.group(3)),
                             "state": tm.group(4), "line": lineno})

            if "Checkpoint finished" in msg:
                fm = RE_CP_FINISH.search(msg)
                parts = dict(RE_CP_PARTS.findall(msg))
                pg = RE_CP_PAGES.search(msg)
                cps.append({"node": node, "dt": dt, "total_ms": int(fm.group(1)) if fm else None,
                            "pages": int(pg.group(1)) if pg else None,
                            "parts": {k: int(v) for k, v in parts.items()}, "line": lineno})

            for cls in RE_EXC.findall(msg):
                if cls not in excs:
                    excs[cls] = (dt, lineno)

            matched = False
            for category, sev, rx in EVENTS:
                if rx.search(msg):
                    counts[category] += 1
                    events.append({"node": node, "dt": dt, "level": level, "cat": category,
                                   "sev": sev, "thread": thread, "logger": cat,
                                   "msg": msg.strip(), "line": lineno,
                                   "file": str(path)})
                    matched = True
                    break
            if not matched and level in ("ERROR", "FATAL"):
                counts["error_line"] += 1
                events.append({"node": node, "dt": dt, "level": level, "cat": "error",
                               "sev": 2, "thread": thread, "logger": cat,
                               "msg": msg.strip(), "line": lineno, "file": str(path)})

    return events, gaps, topo, cps, excs, counts


def truncate(s, n):
    s = s.replace("|", "\\|")
    return s if len(s) <= n else s[:n - 1] + "..."


def render(all_events, all_gaps, all_topo, all_cps, all_excs, counts, sources, args, out):
    w = out.write
    w("# 10 - Cluster timeline (Phase 1)\n\n")
    w("Source logs (%d):\n\n" % len(sources))
    for node, path, lines in sources:
        w("- **%s** - `%s` (%d lines)\n" % (node, path, lines))
    w("\n")
    if args.start or args.end:
        w("Window filter: %s .. %s\n\n" % (args.start or "(open)", args.end or "(open)"))

    # ---- headline -------------------------------------------------------- #
    decisive = [e for e in all_events if e["sev"] == 3]
    w("## Decisive events (severity 3)\n\n")
    if not decisive:
        w("_None matched._ Either the window excludes them, or this incident does not fit the\n"
          "known catalogue - say so explicitly rather than forcing a match.\n\n")
    else:
        w("| time | node | category | logger | message | ref |\n|---|---|---|---|---|---|\n")
        for e in sorted(decisive, key=lambda x: x["dt"])[:args.max_events]:
            w("| %s | %s | %s | %s | %s | %s:%d |\n" % (
                e["dt"].strftime("%H:%M:%S.%f")[:-3], e["node"], e["cat"],
                truncate(e["logger"], 28), truncate(e["msg"], 150),
                Path(e["file"]).name, e["line"]))
        w("\n")

    # ---- log gaps -------------------------------------------------------- #
    w("## Log gaps (>= %ds of silence)\n\n" % args.gap_seconds)
    w("A node that stops logging is a node that stopped running. Gaps are the cheapest\n"
      "evidence of a stall and are frequently missed because nothing is *written* about them.\n\n")
    if not all_gaps:
        w("_No gaps at or above the threshold._\n\n")
    else:
        w("| node | from | to | seconds | resumes at line |\n|---|---|---|---|---|\n")
        for g in sorted(all_gaps, key=lambda x: -x["seconds"])[:30]:
            w("| %s | %s | %s | %.1f | %d |\n" % (
                g["node"], g["start"].strftime("%H:%M:%S.%f")[:-3],
                g["end"].strftime("%H:%M:%S.%f")[:-3], g["seconds"], g["line"]))
        w("\n")

    # ---- topology -------------------------------------------------------- #
    w("## Topology history\n\n")
    if not all_topo:
        w("_No topology snapshots in window._\n\n")
    else:
        w("| time | node reporting | ver | servers | clients | state |\n|---|---|---|---|---|---|\n")
        seen = set()
        for t in sorted(all_topo, key=lambda x: (x["dt"], x["ver"])):
            key = (t["ver"], t["servers"], t["clients"])
            if key in seen:
                continue
            seen.add(key)
            w("| %s | %s | %d | %d | %d | %s |\n" % (
                t["dt"].strftime("%H:%M:%S"), t["node"], t["ver"],
                t["servers"], t["clients"], t["state"]))
        w("\nServer count drops are the cluster's own account of who left and when.\n\n")

    # ---- checkpoints ----------------------------------------------------- #
    w("## Checkpoints\n\n")
    if not all_cps:
        w("_No completed checkpoints in window._\n\n")
    else:
        worst = sorted([c for c in all_cps if c["total_ms"]], key=lambda x: -x["total_ms"])[:10]
        w("Slowest %d by total duration:\n\n" % len(worst))
        w("| finished | node | total | pages | phase breakdown |\n|---|---|---|---|---|\n")
        for c in worst:
            parts = ", ".join("%s=%dms" % (k, v) for k, v in sorted(c["parts"].items()))
            w("| %s | %s | %.1fs | %s | %s |\n" % (
                c["dt"].strftime("%H:%M:%S"), c["node"], c["total_ms"] / 1000.0,
                c["pages"] if c["pages"] is not None else "-", parts or "-"))
        w("\nA checkpoint whose `fsync` or `pagesWrite` dominates is an I/O problem, not a\n"
          "cache problem. Compare against `nmon` disk busy% for the same minutes.\n\n")

    # ---- jvm pauses ------------------------------------------------------ #
    pauses = [e for e in all_events if e["cat"] == "jvm_pause"]
    if pauses:
        w("## JVM pauses reported by Ignite\n\n")
        w("| time | node | pause | ref |\n|---|---|---|---|\n")
        for e in sorted(pauses, key=lambda x: x["dt"])[:20]:
            m = RE_JVM_PAUSE_MS.search(e["msg"])
            w("| %s | %s | %s ms | %s:%d |\n" % (
                e["dt"].strftime("%H:%M:%S.%f")[:-3], e["node"],
                m.group(1) if m else "?", Path(e["file"]).name, e["line"]))
        w("\n**These numbers come from `LongJVMPauseDetector`, which samples wall-clock in a\n"
          "loop. It reports that the JVM stopped making progress - NOT that GC caused it.**\n"
          "Confirm against the safepoint log before attributing anything to GC (Phase 2).\n\n")

    # ---- full timeline --------------------------------------------------- #
    w("## Merged timeline (severity >= %d)\n\n" % args.min_severity)
    rows = sorted([e for e in all_events if e["sev"] >= args.min_severity], key=lambda x: x["dt"])
    if len(rows) > args.max_events:
        w("_Showing %d of %d events. Raise --max-events or narrow --start/--end._\n\n"
          % (args.max_events, len(rows)))
        rows = rows[:args.max_events]
    w("| time | node | lvl | category | message | ref |\n|---|---|---|---|---|---|\n")
    for e in rows:
        w("| %s | %s | %s | %s | %s | %s:%d |\n" % (
            e["dt"].strftime("%H:%M:%S.%f")[:-3], e["node"], e["level"] or "-", e["cat"],
            truncate(e["msg"], 160), Path(e["file"]).name, e["line"]))
    w("\n")

    # ---- exceptions ------------------------------------------------------ #
    w("## First occurrence of each exception class\n\n")
    if not all_excs:
        w("_None seen._\n\n")
    else:
        w("| exception | node | first seen | line |\n|---|---|---|---|\n")
        flat = []
        for node, d in all_excs.items():
            for cls, (dt, line) in d.items():
                flat.append((dt or datetime.max, cls, node, line))
        for dt, cls, node, line in sorted(flat)[:40]:
            w("| `%s` | %s | %s | %d |\n" % (
                cls, node, dt.strftime("%H:%M:%S") if dt != datetime.max else "?", line))
        w("\n")

    # ---- counts ---------------------------------------------------------- #
    w("## Event counts by category\n\n")
    w("| category | count |\n|---|---|\n")
    for cat, n in counts.most_common():
        w("| %s | %d |\n" % (cat, n))
    w("\n")

    w("## Phase 1 gate\n\n")
    w("Before Phase 2, write below, in prose, in this file:\n\n")
    w("1. The sequence of events across nodes, in order, naming which node observed each.\n")
    w("2. Which node made the decision (dropped a peer / halted itself) and on what evidence.\n")
    w("3. The earliest anomaly you can point to - and whether anything precedes it that you\n")
    w("   have NOT explained.\n")
    w("4. What the Ignite logs alone cannot tell you. That list is the agenda for Phase 2.\n\n")
    w("Do not open GC logs, nmon, dmesg or JFR before this section is written.\n\n")
    w("### Narrative\n\n_write here_\n\n")


EXPLAIN = """\
ignite_timeline.py - Phase 1 digest of Ignite node logs.

WHAT IT DOES
  Reads every Ignite log you point it at, across ALL nodes, and emits one bounded
  Markdown digest: decisive events, log gaps, topology history, checkpoint timings,
  JVM pauses as Ignite saw them, first-occurrence exceptions, and category counts.
  Every row carries file:line so you can read the exact source line if needed.

WHY IT EXISTS
  Raw Ignite logs are far too large to read, and the events that matter are a tiny
  fraction of the lines. Reading them into a model's context is how an analysis runs
  out of room before it has started.

WHAT IT DOES NOT DO
  It does not decide anything. It matches a known catalogue of message patterns.
  An incident that does not fit the catalogue will produce a thin digest - that is a
  signal to look manually, not to force the nearest matching category.

ASSUMPTIONS
  - Log lines start with [yyyy-MM-dd'T'HH:mm:ss,SSS]. Other layouts degrade to a
    timestamp-only parse; lines with no parseable timestamp are treated as
    continuations and mined only for exception class names.
  - Timestamps are in the JVM's default timezone, which the log does NOT record.
    Never compare these against another host's clock without establishing the offset.

USAGE
  python ignite_timeline.py <bundle-dir> --out 10-cluster-timeline.md
  python ignite_timeline.py <bundle-dir> --inventory inventory.json \\
      --start "2024-03-14 10:15:00" --end "2024-03-14 10:30:00"
"""


def discover(root, inventory):
    """Return [(node, Path)] of Ignite logs, from inventory.json when available."""
    out = []
    if inventory:
        data = json.loads(Path(inventory).read_text(encoding="utf-8"))
        base = Path(data["root"])
        for f in data["files"]:
            if f["kind"] == "ignite_log":
                out.append((f.get("node") or "?", base / f["path"]))
        return out
    # fallback: sniff every text file cheaply
    for p in sorted(Path(root).rglob("*")):
        if not p.is_file() or p.stat().st_size == 0:
            continue
        try:
            head = p.open("rb").read(65536).decode("utf-8", errors="replace")
        except OSError:
            continue
        if re.search(r"\[(?:INFO|WARN|ERROR)\s*\]\[", head) and (
                "org.apache.ignite" in head or "Topology snapshot" in head
                or ">>> ver." in head or "IgniteKernal" in head):
            rel = p.relative_to(root)
            node = rel.parts[0] if len(rel.parts) > 1 else "(root)"
            out.append((node, p))
    return out


def main():
    ap = argparse.ArgumentParser(description="Phase 1: cross-node Ignite log timeline.")
    ap.add_argument("root", nargs="?", help="incident bundle directory")
    ap.add_argument("--inventory", help="inventory.json from identify.py (preferred)")
    ap.add_argument("--out", help="write Markdown here instead of stdout")
    ap.add_argument("--json", dest="json_out", help="machine-readable events for correlate.py")
    ap.add_argument("--start", help="window start, 'YYYY-MM-DD HH:MM:SS'")
    ap.add_argument("--end", help="window end, 'YYYY-MM-DD HH:MM:SS'")
    ap.add_argument("--gap-seconds", type=float, default=30.0,
                    help="report silences at or above this many seconds (default 30)")
    ap.add_argument("--min-severity", type=int, default=2, choices=(1, 2, 3),
                    help="minimum severity in the merged timeline (default 2)")
    ap.add_argument("--max-events", type=int, default=200, help="cap on rows printed")
    ap.add_argument("--explain", action="store_true")
    args = ap.parse_args()

    if args.explain:
        print(EXPLAIN)
        return 0
    if not args.root and not args.inventory:
        ap.error("give a bundle directory or --inventory")

    def parse_when(s):
        if not s:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                pass
        raise SystemExit("error: cannot parse time %r (use 'YYYY-MM-DD HH:MM:SS')" % s)

    window = {"start": parse_when(args.start), "end": parse_when(args.end),
              "gap": args.gap_seconds}

    logs = discover(Path(args.root).resolve() if args.root else None, args.inventory)
    if not logs:
        print("error: no Ignite logs found. Run identify.py first and pass --inventory.",
              file=sys.stderr)
        return 1

    all_events, all_gaps, all_topo, all_cps = [], [], [], []
    all_excs = defaultdict(dict)
    counts = Counter()
    sources = []
    for node, path in logs:
        if not path.is_file():
            print("warning: %s missing, skipped" % path, file=sys.stderr)
            continue
        ev, gaps, topo, cps, excs, c = scan_log(path, node, window)
        all_events += ev
        all_gaps += gaps
        all_topo += topo
        all_cps += cps
        all_excs[node].update(excs)
        counts.update(c)
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            nlines = sum(1 for _ in fh)
        sources.append((node, path.name, nlines))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            render(all_events, all_gaps, all_topo, all_cps, all_excs, counts, sources, args, fh)
        print("wrote %s (%d events, %d gaps)" % (args.out, len(all_events), len(all_gaps)))
    else:
        render(all_events, all_gaps, all_topo, all_cps, all_excs, counts, sources, args, sys.stdout)

    if args.json_out:
        payload = {"events": [{**e, "dt": e["dt"].isoformat()} for e in all_events],
                   "gaps": [{**g, "start": g["start"].isoformat(), "end": g["end"].isoformat()}
                            for g in all_gaps],
                   "checkpoints": [{**c, "dt": c["dt"].isoformat()} for c in all_cps]}
        Path(args.json_out).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print("wrote %s" % args.json_out, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
