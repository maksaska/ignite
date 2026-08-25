#!/usr/bin/env python3
"""Phase 2: digest JVM unified GC and safepoint logs.

Handles JDK 11+ unified logging for G1, ZGC, Shenandoah, Parallel and Serial, whether
GC and safepoint go to separate files or one merged file, and both safepoint wordings
(the JDK 11 'Total time for which application threads were stopped' line and the
JDK 17 'Safepoint "op", Reaching safepoint / At safepoint' line).

THE POINT OF THIS SCRIPT is the split between:

    Reaching safepoint  (time-to-safepoint, TTSP)  - the JVM asking threads to stop
    At safepoint        (the actual GC / VM operation)

A 20-second stall with a 30ms 'At safepoint' is NOT a GC problem. It means threads
could not be brought to a safepoint - swapping, CPU starvation, a page-fault storm,
a counted loop without a safepoint poll. Blaming GC there sends the whole analysis
in the wrong direction, which is why this digest reports the two separately and
refuses to collapse them into one 'pause' number.

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
# Unified-log decorator parsing.
#
# -Xlog decorators are configurable and may appear in any subset/order:
#   [time][uptime][level][tags] message
# So we parse the leading bracket groups generically and classify each one, rather
# than assuming a fixed layout. A site that logs with fewer decorators still works.
# --------------------------------------------------------------------------- #

RE_BRACKETS = re.compile(r"^((?:\[[^\]]*\])+)\s*(.*)$")
RE_ONE = re.compile(r"\[([^\]]*)\]")
RE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}([+-]\d{4}|Z)?$")
RE_UPTIME = re.compile(r"^(\d+[.,]\d+)s$")
RE_LEVEL = re.compile(r"^(trace|debug|info|warning|error)$")
RE_PID = re.compile(r"^\d+$")

LEVELS = {"trace", "debug", "info", "warning", "error"}


def parse_unified(line):
    """Return dict(time, uptime, level, tags, msg) or None."""
    m = RE_BRACKETS.match(line)
    if not m:
        return None
    parts = RE_ONE.findall(m.group(1))
    out = {"time": None, "uptime": None, "level": None, "tags": [], "msg": m.group(2)}
    for p in parts:
        p = p.strip()
        if RE_ISO.match(p):
            out["time"] = p
        elif RE_UPTIME.match(p):
            out["uptime"] = float(RE_UPTIME.match(p).group(1).replace(",", "."))
        elif p.lower() in LEVELS:
            out["level"] = p.lower()
        elif RE_PID.match(p):
            pass  # pid/tid decorator
        elif p:
            out["tags"] = [t.strip() for t in p.split(",")]
    return out


def parse_time(s):
    if not s:
        return None
    try:
        if s.endswith("Z"):
            return datetime.strptime(s[:-1], "%Y-%m-%dT%H:%M:%S.%f")
        if re.search(r"[+-]\d{4}$", s):
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f%z").replace(tzinfo=None)
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f")
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Message grammars.
# --------------------------------------------------------------------------- #

# G1/Parallel/Serial:  GC(411) Pause Young (Normal) (G1 Evacuation Pause) 19456M->9800M(32768M) 481.223ms
# Shenandoah:          GC(122) Pause Init Mark (process weakrefs) 2.881ms
# ZGC (gc,phases):     GC(88) Pause Mark Start 1.204ms
RE_GC_EVENT = re.compile(
    r"^GC\((?P<id>\d+)\)\s+(?P<desc>.+?)"
    r"(?:\s+(?P<before>\d+)(?P<bu>[KMG])->(?P<after>\d+)(?P<au>[KMG])"
    r"(?:\((?P<cap>\d+)(?P<cu>[KMG])\))?)?"
    r"\s+(?P<ms>\d+[.,]\d+)ms$")

# JDK 17+ safepoint line
RE_SP17 = re.compile(
    r'Safepoint "(?P<op>[^"]+)",\s*Time since last:\s*(?P<since>\d+) ns,\s*'
    r"Reaching safepoint:\s*(?P<ttsp>\d+) ns,\s*(?:Cleanup:\s*(?P<cleanup>\d+) ns,\s*)?"
    r"At safepoint:\s*(?P<at>\d+) ns,\s*Total:\s*(?P<total>\d+) ns")

# JDK 11 safepoint line
RE_SP11 = re.compile(
    r"Total time for which application threads were stopped:\s*(?P<total>\d+[.,]\d+) seconds,\s*"
    r"Stopping threads took:\s*(?P<ttsp>\d+[.,]\d+) seconds")

RE_COLLECTOR = [
    (re.compile(r"Using The Z Garbage Collector|Initializing The Z Garbage Collector"), "ZGC"),
    (re.compile(r"Using Shenandoah"), "Shenandoah"),
    (re.compile(r"Using G1"), "G1"),
    (re.compile(r"Using Parallel"), "ParallelGC"),
    (re.compile(r"Using Serial"), "SerialGC"),
]
RE_VERSION = re.compile(r"^Version:\s*(\S+)")
RE_HEAPCAP = re.compile(r"Heap (?:Min|Initial|Max) Capacity:\s*(\S+)|Max Capacity:\s*(\S+)")

# Pressure / failure signals worth surfacing verbatim.
TROUBLE = [
    ("to-space exhausted", re.compile(r"To-space [Ee]xhausted|Evacuation Failure")),
    ("full gc", re.compile(r"Pause Full")),
    ("humongous allocation", re.compile(r"G1 Humongous Allocation")),
    ("allocation stall", re.compile(r"Allocation Stall|Stalled")),
    ("concurrent mode failure", re.compile(r"[Cc]oncurrent [Mm]ode [Ff]ailure|Degenerated GC")),
    ("metaspace", re.compile(r"Metadata GC Threshold|Metaspace")),
    ("system.gc", re.compile(r"System\.gc\(\)")),
    ("soft ref / heap exhaustion", re.compile(r"Heap [Dd]ump|OutOfMemory")),
]

UNIT = {"K": 1 / 1024.0, "M": 1.0, "G": 1024.0}   # normalise to MB

BUCKETS = [(0.010, "<10ms"), (0.050, "10-50ms"), (0.100, "50-100ms"), (0.500, "100-500ms"),
           (1.0, "500ms-1s"), (5.0, "1-5s"), (10.0, "5-10s"), (float("inf"), ">10s")]


def bucket_of(seconds):
    for hi, label in BUCKETS:
        if seconds < hi:
            return label
    return ">10s"


class Digest:
    def __init__(self):
        self.gc_events = []      # dict(dt, uptime, id, desc, ms, before, after, cap, stw)
        self.safepoints = []     # dict(dt, uptime, op, ttsp_s, at_s, total_s, fmt)
        self.collector = None
        self.jdk = None
        self.heap = []
        self.trouble = defaultdict(list)
        self.files = []
        self.unparsed = 0
        self.lines = 0


def scan(path, dg, window):
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            if not line:
                continue
            dg.lines += 1
            u = parse_unified(line)
            if u is None:
                dg.unparsed += 1
                continue
            msg = u["msg"]
            dt = parse_time(u["time"])
            if window[0] and dt and dt < window[0]:
                continue
            if window[1] and dt and dt > window[1]:
                continue

            if dg.collector is None:
                for rx, name in RE_COLLECTOR:
                    if rx.search(msg):
                        dg.collector = name
                        break
            if dg.jdk is None:
                m = RE_VERSION.match(msg)
                if m:
                    dg.jdk = m.group(1)
            m = RE_HEAPCAP.search(msg)
            if m and len(dg.heap) < 6:
                dg.heap.append(msg)

            tags = u["tags"]
            if "safepoint" in tags:
                m = RE_SP17.search(msg)
                if m:
                    dg.safepoints.append({
                        "dt": dt, "uptime": u["uptime"], "op": m.group("op"),
                        "ttsp_s": int(m.group("ttsp")) / 1e9,
                        "at_s": int(m.group("at")) / 1e9,
                        "total_s": int(m.group("total")) / 1e9,
                        "fmt": "jdk17", "file": path.name, "line": lineno})
                    continue
                m = RE_SP11.search(msg)
                if m:
                    total = float(m.group("total").replace(",", "."))
                    ttsp = float(m.group("ttsp").replace(",", "."))
                    dg.safepoints.append({
                        "dt": dt, "uptime": u["uptime"], "op": "(not named in JDK 11 format)",
                        "ttsp_s": ttsp, "at_s": max(0.0, total - ttsp), "total_s": total,
                        "fmt": "jdk11", "file": path.name, "line": lineno})
                    continue

            if any(t == "gc" or t.startswith("gc") for t in tags):
                for label, rx in TROUBLE:
                    if rx.search(msg):
                        if len(dg.trouble[label]) < 12:
                            dg.trouble[label].append((dt, msg[:180], path.name, lineno))
                m = RE_GC_EVENT.match(msg)
                if m and "start" not in tags:
                    ms = float(m.group("ms").replace(",", "."))
                    desc = m.group("desc")
                    stw = desc.startswith("Pause") or "Pause" in desc.split("(")[0]
                    ev = {"dt": dt, "uptime": u["uptime"], "id": int(m.group("id")),
                          "desc": desc, "ms": ms, "stw": stw,
                          "file": path.name, "line": lineno,
                          "before": None, "after": None, "cap": None}
                    if m.group("before"):
                        ev["before"] = float(m.group("before")) * UNIT[m.group("bu")]
                        ev["after"] = float(m.group("after")) * UNIT[m.group("au")]
                        if m.group("cap"):
                            ev["cap"] = float(m.group("cap")) * UNIT[m.group("cu")]
                    dg.gc_events.append(ev)


def fmt_dt(d):
    return d.strftime("%H:%M:%S.%f")[:-3] if d else "-"


def worst_windows(items, key_dt, key_sec, width_s, top):
    """Cumulative stop time in a sliding window of `width_s` seconds."""
    pts = sorted([(i[key_dt], i[key_sec]) for i in items if i[key_dt]], key=lambda x: x[0])
    if not pts:
        return []
    out = []
    j = 0
    run = 0.0
    for i in range(len(pts)):
        run += pts[i][1]
        while pts[i][0] - pts[j][0] > timedelta(seconds=width_s):
            run -= pts[j][1]
            j += 1
        out.append((pts[j][0], pts[i][0], run, i - j + 1))
    out.sort(key=lambda x: -x[2])
    picked = []
    for s, e, run, n in out:
        if any(not (e < ps or s > pe) for ps, pe, _, _ in picked):
            continue
        picked.append((s, e, run, n))
        if len(picked) >= top:
            break
    return picked


def render(dg, args, out):
    w = out.write
    w("# 20 - GC and safepoint digest (Phase 2)\n\n")
    w("Files read: %s\n\n" % ", ".join("`%s`" % f for f in dg.files))
    w("| | |\n|---|---|\n")
    w("| Collector | %s |\n" % (dg.collector or "NOT DETECTED - check the flags dump"))
    w("| JDK | %s |\n" % (dg.jdk or "not stated in log"))
    w("| GC events parsed | %d |\n" % len(dg.gc_events))
    w("| Safepoint records parsed | %d |\n" % len(dg.safepoints))
    if dg.safepoints:
        w("| Safepoint log format | %s |\n" % ", ".join(sorted({s["fmt"] for s in dg.safepoints})))
    w("| Unparsed lines | %d |\n" % dg.unparsed)
    for h in dg.heap:
        w("| heap | %s |\n" % h)
    w("\n")

    if not dg.safepoints:
        w("> **No safepoint records.** Without them you cannot separate a GC pause from a\n"
          "> time-to-safepoint stall, and any claim about *why* the JVM stopped is a guess.\n"
          "> Check whether the bundle has a separate safepoint file that was missed, or note\n"
          "> in the report that this distinction could not be made.\n\n")

    # ---- the headline split --------------------------------------------- #
    if dg.safepoints:
        max_ttsp = max(dg.safepoints, key=lambda s: s["ttsp_s"])
        max_at = max(dg.safepoints, key=lambda s: s["at_s"])
        total_stop = sum(s["total_s"] for s in dg.safepoints)
        total_ttsp = sum(s["ttsp_s"] for s in dg.safepoints)
        w("## Where the stop time actually went\n\n")
        w("| | seconds | share |\n|---|---|---|\n")
        w("| Total application stop time | %.3f | 100%% |\n" % total_stop)
        w("| ...spent reaching safepoint (TTSP) | %.3f | %.1f%% |\n"
          % (total_ttsp, 100.0 * total_ttsp / total_stop if total_stop else 0))
        w("| ...spent at safepoint (the VM operation) | %.3f | %.1f%% |\n"
          % (total_stop - total_ttsp,
             100.0 * (total_stop - total_ttsp) / total_stop if total_stop else 0))
        w("\n")
        w("- Longest **TTSP**: **%.3f s** at %s (op `%s`, at-safepoint %.3f s)\n"
          % (max_ttsp["ttsp_s"], fmt_dt(max_ttsp["dt"]), max_ttsp["op"], max_ttsp["at_s"]))
        w("- Longest **at-safepoint**: **%.3f s** at %s (op `%s`, TTSP %.3f s)\n"
          % (max_at["at_s"], fmt_dt(max_at["dt"]), max_at["op"], max_at["ttsp_s"]))
        w("\n")
        if max_ttsp["ttsp_s"] >= args.ttsp_alarm:
            w("> ### READ THIS BEFORE BLAMING GC\n>\n")
            w("> The longest stall is dominated by **time-to-safepoint (%.3f s)**, not by the\n"
              "> VM operation itself (%.3f s at safepoint). The JVM was not slow at collecting;\n"
              "> it could not get the application threads to stop.\n>\n"
              % (max_ttsp["ttsp_s"], max_ttsp["at_s"]))
            w("> Causes to check, in the order they are cheapest to confirm:\n"
              "> 1. **Memory pressure / swapping** - the thread's stack or code pages were paged\n"
              ">    out. Check `nmon` swap-in and free memory, and `dmesg` for allocation stalls\n"
              ">    or `kswapd`. This is the most common cause on a persistence-enabled node.\n"
              "> 2. **CPU starvation** - run queue longer than cores, or steal time on a VM.\n"
              "> 3. **A counted loop with no safepoint poll** in JIT-compiled code (rarer; look\n"
              ">    for one thread consistently late in JFR's ExecutionSample).\n"
              "> 4. **Slow I/O in a native call** that must complete before the thread polls.\n>\n")
            w("> Correlate the %s window with nmon and dmesg before writing any conclusion.\n\n"
              % fmt_dt(max_ttsp["dt"]))

    # ---- STW histogram --------------------------------------------------- #
    stw = [e for e in dg.gc_events if e["stw"]]
    w("## Stop-the-world GC pause distribution\n\n")
    if not stw:
        w("_No stop-the-world GC pauses parsed._")
        if dg.gc_events:
            w(" (%d GC events were parsed but none classified as a pause - "
              "for ZGC/Shenandoah most work is concurrent, which is expected.)" % len(dg.gc_events))
        w("\n\n")
    else:
        hist = Counter(bucket_of(e["ms"] / 1000.0) for e in stw)
        w("| bucket | count |\n|---|---|\n")
        for _, label in BUCKETS:
            if hist.get(label):
                w("| %s | %d |\n" % (label, hist[label]))
        tot = sum(e["ms"] for e in stw) / 1000.0
        w("\nTotal STW GC time: **%.3f s** across %d pauses (mean %.1f ms, max %.1f ms).\n\n"
          % (tot, len(stw), 1000.0 * tot / len(stw), max(e["ms"] for e in stw)))

        w("### Longest %d STW pauses\n\n" % args.top)
        w("| time | pause | description | heap before->after (cap) | ref |\n|---|---|---|---|---|\n")
        for e in sorted(stw, key=lambda x: -x["ms"])[:args.top]:
            heap = ("%.0fM->%.0fM(%.0fM)" % (e["before"], e["after"], e["cap"])
                    if e["before"] is not None and e["cap"] else
                    ("%.0fM->%.0fM" % (e["before"], e["after"]) if e["before"] is not None else "-"))
            w("| %s | %.1f ms | %s | %s | %s:%d |\n"
              % (fmt_dt(e["dt"]), e["ms"], e["desc"][:70], heap, e["file"], e["line"]))
        w("\n")

    # ---- worst windows --------------------------------------------------- #
    src = dg.safepoints if dg.safepoints else [
        {"dt": e["dt"], "s": e["ms"] / 1000.0} for e in stw]
    key = "total_s" if dg.safepoints else "s"
    ww = worst_windows(src, "dt", key, args.window_seconds, 5)
    if ww:
        w("## Worst %ds windows by cumulative stop time\n\n" % args.window_seconds)
        w("| from | to | stopped | events | share of window |\n|---|---|---|---|---|\n")
        for s, e, run, n in ww:
            w("| %s | %s | %.3f s | %d | %.1f%% |\n"
              % (fmt_dt(s), fmt_dt(e), run, n, 100.0 * run / args.window_seconds))
        w("\nA window where the JVM is stopped for a large share of the time is a window in\n"
          "which discovery heartbeats and socket writes cannot happen. Compare these against\n"
          "`failureDetectionTimeout` from the Ignite config.\n\n")

    # ---- safepoint operations -------------------------------------------- #
    if dg.safepoints:
        w("## Safepoint operations\n\n")
        agg = defaultdict(lambda: [0, 0.0, 0.0])
        for s in dg.safepoints:
            a = agg[s["op"]]
            a[0] += 1
            a[1] += s["total_s"]
            a[2] = max(a[2], s["total_s"])
        w("| operation | count | total s | max s |\n|---|---|---|---|\n")
        for op, (n, tot, mx) in sorted(agg.items(), key=lambda x: -x[1][1])[:20]:
            w("| `%s` | %d | %.3f | %.3f |\n" % (op, n, tot, mx))
        w("\n")
        w("### Longest %d safepoints (TTSP split)\n\n" % args.top)
        w("| time | operation | reaching (TTSP) | at safepoint | total | ref |\n"
          "|---|---|---|---|---|---|\n")
        for s in sorted(dg.safepoints, key=lambda x: -x["total_s"])[:args.top]:
            w("| %s | `%s` | %.3f s | %.3f s | **%.3f s** | %s:%d |\n"
              % (fmt_dt(s["dt"]), s["op"], s["ttsp_s"], s["at_s"], s["total_s"],
                 s["file"], s["line"]))
        w("\n")

    # ---- heap trend ------------------------------------------------------ #
    withheap = [e for e in dg.gc_events if e["after"] is not None]
    if withheap:
        w("## Heap after collection (live-set trend)\n\n")
        step = max(1, len(withheap) // 12)
        w("| time | after GC | capacity | description |\n|---|---|---|---|\n")
        for e in withheap[::step][:12]:
            w("| %s | %.0f M | %s | %s |\n"
              % (fmt_dt(e["dt"]), e["after"],
                 "%.0f M" % e["cap"] if e["cap"] else "-", e["desc"][:50]))
        first, last = withheap[0], withheap[-1]
        w("\nAfter-GC occupancy went %.0f M -> %.0f M across the log. A steadily rising floor "
          "means the live set is growing; a flat floor with rising pause times points at the "
          "collector or the machine, not at a leak.\n\n" % (first["after"], last["after"]))

    # ---- trouble --------------------------------------------------------- #
    if dg.trouble:
        w("## Pressure and failure signals\n\n")
        for label, rows in sorted(dg.trouble.items()):
            w("**%s** (%d shown)\n\n" % (label, len(rows)))
            for dt, msg, f, ln in rows[:5]:
                w("- `%s` %s  _(%s:%d)_\n" % (fmt_dt(dt), msg, f, ln))
            w("\n")

    w("## Phase 2 gate (GC portion)\n\n")
    w("Answer these in `20-resource-findings.md` before moving on:\n\n")
    w("1. Was the JVM stopped long enough, and close enough in time, to explain what the\n"
      "   Ignite timeline showed? Quote both numbers side by side.\n")
    w("2. Was the stop time TTSP or at-safepoint? Say which, with the figure.\n")
    w("3. If TTSP dominates: what does nmon/dmesg say about memory and CPU in that window?\n")
    w("4. If at-safepoint dominates: which collector phase, and is the live set growing?\n")
    w("5. If neither is large enough to explain the incident, say so plainly - a small GC\n"
      "   pause that happens to sit near the failure is a coincidence until proven otherwise.\n\n")


EXPLAIN = """\
gc_digest.py - Phase 2 digest of JVM unified GC and safepoint logs.

WHAT IT DOES
  Parses -Xlog unified output (JDK 11+) for G1 / ZGC / Shenandoah / Parallel / Serial,
  from separate or merged gc+safepoint files, in either safepoint wording (JDK 11's
  'Total time for which application threads were stopped' or JDK 17's 'Safepoint "op"').
  Emits: the TTSP vs at-safepoint split, a pause histogram, the longest pauses and
  safepoints, worst cumulative-stop windows, safepoint operation breakdown, heap trend,
  and pressure signals (to-space exhausted, Full GC, allocation stalls, humongous).

WHY THE TTSP SPLIT MATTERS
  'The JVM paused for 20 seconds' does not mean GC took 20 seconds. If the time went
  into REACHING the safepoint, the collector is innocent and the cause is the machine
  (swap, CPU starvation) or a thread that would not poll. Conflating the two is the
  single most common error in this kind of analysis, so this script never reports one
  aggregate 'pause' number.

DECORATOR HANDLING
  -Xlog decorators are configurable, so bracket groups are classified individually
  (ISO time / uptime / level / tags) rather than assumed positional. A log without the
  'time' decorator still parses, but has no wall clock -- correlate via uptime instead
  and say so in the report.

ASSUMPTIONS
  - JDK 8 style (-XX:+PrintGCDetails) is NOT supported. If identify.py reported a JDK 8
    format, stop and say so rather than reading these numbers.
  - Sizes are normalised to MB. ZGC percentage-form heap output is not parsed as a size.

USAGE
  python gc_digest.py <file-or-dir> [...] --out 20-gc.md
  python gc_digest.py --inventory inventory.json --start "2024-03-14 10:15:00"
"""


def collect_inputs(args):
    paths = []
    if args.inventory:
        data = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
        base = Path(data["root"])
        for f in data["files"]:
            if f["kind"] in ("gc_log", "safepoint_log", "gc_safepoint_merged"):
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
    ap = argparse.ArgumentParser(description="Phase 2: GC and safepoint digest.")
    ap.add_argument("targets", nargs="*", default=[], help="gc/safepoint files or a directory")
    ap.add_argument("--inventory", help="inventory.json from identify.py (preferred)")
    ap.add_argument("--node", help="restrict to one node from the inventory")
    ap.add_argument("--out", help="write Markdown here instead of stdout")
    ap.add_argument("--json", dest="json_out", help="machine-readable output for correlate.py")
    ap.add_argument("--start", help="window start 'YYYY-MM-DD HH:MM:SS'")
    ap.add_argument("--end", help="window end 'YYYY-MM-DD HH:MM:SS'")
    ap.add_argument("--top", type=int, default=15, help="rows in 'longest' tables")
    ap.add_argument("--window-seconds", type=int, default=60,
                    help="width of the cumulative-stop window (default 60)")
    ap.add_argument("--ttsp-alarm", type=float, default=1.0,
                    help="TTSP seconds above which the 'not GC' warning fires (default 1.0)")
    ap.add_argument("--explain", action="store_true")
    args = ap.parse_args()

    if args.explain:
        print(EXPLAIN)
        return 0

    def when(s):
        if not s:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                pass
        raise SystemExit("error: cannot parse time %r" % s)

    window = (when(args.start), when(args.end))
    paths = collect_inputs(args)
    if not paths:
        print("error: no GC/safepoint files. Run identify.py and pass --inventory, "
              "or name files explicitly.", file=sys.stderr)
        return 1

    dg = Digest()
    for p in paths:
        if not p.is_file():
            continue
        dg.files.append(p.name)
        scan(p, dg, window)

    if not dg.gc_events and not dg.safepoints:
        print("error: parsed %d lines from %s but recognised no GC or safepoint records.\n"
              "       This is usually JDK 8 format (-XX:+PrintGCDetails), which this script\n"
              "       does not support. First unparsed-looking line is worth checking by hand."
              % (dg.lines, ", ".join(dg.files)), file=sys.stderr)
        return 1

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            render(dg, args, fh)
        print("wrote %s (%d gc events, %d safepoints)"
              % (args.out, len(dg.gc_events), len(dg.safepoints)))
    else:
        render(dg, args, sys.stdout)

    if args.json_out:
        payload = {
            "collector": dg.collector, "jdk": dg.jdk,
            "node": args.node or "?", "files": dg.files,
            "gc_events": [{**e, "dt": e["dt"].isoformat() if e["dt"] else None}
                          for e in dg.gc_events],
            "safepoints": [{**s, "dt": s["dt"].isoformat() if s["dt"] else None}
                           for s in dg.safepoints],
        }
        Path(args.json_out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print("wrote %s" % args.json_out, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
