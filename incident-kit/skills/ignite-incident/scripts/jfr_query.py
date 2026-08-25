#!/usr/bin/env python3
"""Phase 4 only: query a JFR recording against a specific, written question.

Wraps the JDK's `jfr` CLI, applies a time window and an event-type filter, and reduces
execution samples to a frame histogram instead of dumping stacks.

DO NOT RUN THIS WITHOUT A QUESTION FROM PHASE 3. A JFR recording holds hundreds of
thousands of events; browsing it fills your context and answers nothing. The question
must name what you expect to find and what result would refute the hypothesis.

Stdlib only (shells out to `jfr`, which ships with the JDK).
"""

import argparse
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

GROUPS = {
    "execution": ["jdk.ExecutionSample", "jdk.NativeMethodSample"],
    "monitor": ["jdk.JavaMonitorEnter", "jdk.JavaMonitorWait", "jdk.ThreadPark"],
    "gc": ["jdk.GCPhasePause", "jdk.YoungGarbageCollection", "jdk.OldGarbageCollection",
           "jdk.GarbageCollection"],
    "safepoint": ["jdk.SafepointBegin", "jdk.SafepointEnd", "jdk.SafepointStateSynchronization"],
    "io": ["jdk.FileWrite", "jdk.FileRead", "jdk.FileForce"],
    "socket": ["jdk.SocketRead", "jdk.SocketWrite"],
    "alloc": ["jdk.ObjectAllocationSample", "jdk.ObjectAllocationInNewTLAB"],
    "cpu": ["jdk.CPULoad", "jdk.ThreadCPULoad", "jdk.ExecutionSample"],
}

RE_EVENT_START = re.compile(r"^(jdk\.\w+)\s*\{")
RE_FIELD = re.compile(r"^\s+(\w+)\s*=\s*(.*?)\s*$")
RE_STACKFRAME = re.compile(r"^\s+(\S+\.\S+)\(")
RE_TIME = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)")


def have_jfr():
    return shutil.which("jfr") is not None


def run_jfr(args_list, timeout=600):
    try:
        proc = subprocess.run(["jfr"] + args_list, capture_output=True, text=True,
                              timeout=timeout, errors="replace")
    except subprocess.TimeoutExpired:
        raise SystemExit("error: `jfr` timed out. Narrow the window or the event list.")
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[:4000])
        raise SystemExit("error: `jfr` exited %d" % proc.returncode)
    return proc.stdout


def parse_when(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt), fmt
        except ValueError:
            pass
    raise SystemExit("error: cannot parse time %r" % s)


def in_window(text, start, end):
    """Best-effort: match the first ISO timestamp in the event block."""
    if not start and not end:
        return True
    m = RE_TIME.search(text)
    if not m:
        return True   # keep events we cannot time rather than silently dropping them
    stamp = m.group(1).split(".")[0]
    try:
        dt = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return True
    if start and dt < start:
        return False
    if end and dt > end:
        return False
    return True


def split_events(raw):
    """Yield (event_type, block_text)."""
    cur_type = None
    buf = []
    depth = 0
    for line in raw.split("\n"):
        m = RE_EVENT_START.match(line)
        if m and depth == 0:
            if cur_type and buf:
                yield cur_type, "\n".join(buf)
            cur_type = m.group(1)
            buf = [line]
            depth = line.count("{") - line.count("}")
            continue
        if cur_type:
            buf.append(line)
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                yield cur_type, "\n".join(buf)
                cur_type, buf, depth = None, [], 0
    if cur_type and buf:
        yield cur_type, "\n".join(buf)


def summarise(path, out):
    raw = run_jfr(["summary", str(path)])
    out.write("# JFR summary\n\n```\n%s\n```\n\n" % raw.strip()[:6000])
    out.write("Check before going further:\n\n")
    out.write("1. Does the recording actually cover your incident window? If not, stop.\n")
    out.write("2. Are the event types your question needs present, and non-zero? A `default`\n"
              "   profile omits many. If the type you need has zero events, say so in the\n"
              "   report - it is a collection gap, not a negative result.\n")
    out.write("3. JFR timestamps are epoch-based. Align them with your Phase 0 reference\n"
              "   timezone before comparing with Ignite logs.\n\n")


def analyse(path, event_types, args, out):
    w = out.write
    w("# JFR query\n\n")
    w("| | |\n|---|---|\n")
    w("| Recording | `%s` |\n" % path.name)
    w("| Question | %s |\n" % (args.question or "**NOT STATED - go back to Phase 3**"))
    w("| Window | %s .. %s |\n" % (args.window[0] if args.window else "(all)",
                                   args.window[1] if args.window else "(all)"))
    w("| Event types | %s |\n\n" % ", ".join(event_types))

    if not args.question:
        w("> This query was run without a stated question. Record the question from\n"
          "> `30-hypotheses.md` and re-run with `--question`, or the result cannot be\n"
          "> interpreted against any hypothesis.\n\n")

    start = end = None
    if args.window:
        s, sfmt = parse_when(args.window[0])
        e, efmt = parse_when(args.window[1])
        if sfmt == "%H:%M:%S" or efmt == "%H:%M:%S":
            w("> Window given as time-of-day only; the date is taken from the recording.\n"
              "> If the recording spans midnight this will be wrong.\n\n")
        start, end = s, e

    raw = run_jfr(["print", "--events", ",".join(event_types),
                   "--stack-depth", str(args.stack_depth), str(path)])

    per_type = Counter()
    frames = Counter()
    per_thread = Counter()
    thread_frames = defaultdict(Counter)
    durations = []
    kept = 0
    times = []

    for etype, block in split_events(raw):
        if not in_window(block, start, end):
            continue
        kept += 1
        per_type[etype] += 1
        m = RE_TIME.search(block)
        if m:
            times.append(m.group(1))
        tm = re.search(r"^\s+(?:sampledThread|eventThread|thread)\s*=\s*\"?([^\"\n]+)",
                       block, re.M)
        thread = tm.group(1).strip().rstrip(",") if tm else "(unknown)"
        per_thread[thread] += 1
        dm = re.search(r"^\s+duration\s*=\s*([\d.]+)\s*(\w+)", block, re.M)
        if dm:
            val = float(dm.group(1))
            unit = dm.group(2)
            secs = {"ns": 1e-9, "us": 1e-6, "ms": 1e-3, "s": 1.0}.get(unit, 1.0) * val
            durations.append((secs, thread, etype))
        fm = RE_STACKFRAME.search(block[block.find("stackTrace"):]) if "stackTrace" in block else None
        if fm:
            frames[fm.group(1)] += 1
            thread_frames[thread][fm.group(1)] += 1

    w("## Events in window\n\n")
    if not kept:
        w("**No events of these types in this window.**\n\n")
        w("That is a result, not a failure. It means one of:\n\n")
        w("- the JVM was stopped, so nothing was sampled - which corroborates a stall;\n")
        w("- the event type was not enabled in the recording profile;\n")
        w("- the window or the clock alignment is wrong.\n\n")
        w("Distinguish these before writing it up - check `jfr summary` for the type's\n"
          "total count across the whole recording.\n\n")
        return
    if times:
        w("Span of matched events: `%s` .. `%s`\n\n" % (min(times), max(times)))
    w("| event type | count |\n|---|---|\n")
    for t, n in per_type.most_common():
        w("| %s | %d |\n" % (t, n))
    w("\n")

    w("## By thread\n\n| thread | events | dominant top frame |\n|---|---|---|\n")
    for th, n in per_thread.most_common(args.top):
        top = thread_frames[th].most_common(1)
        w("| `%s` | %d | %s |\n" % (th, n, ("`%s` (%d)" % (top[0][0], top[0][1])) if top else "-"))
    w("\n")
    w("A single thread dominating the samples with the same top frame throughout is the\n"
      "signature of a thread that would not yield - the candidate for a time-to-safepoint\n"
      "stall. Many threads sharing one framework frame is contention instead.\n\n")

    if frames:
        w("## Top frames overall\n\n| samples | frame |\n|---|---|\n")
        for f, n in frames.most_common(args.top):
            w("| %d | `%s` |\n" % (n, f))
        w("\n")

    if durations:
        durations.sort(reverse=True)
        w("## Longest durations\n\n| duration | thread | event |\n|---|---|---|\n")
        for secs, th, et in durations[:args.top]:
            w("| %.3f s | `%s` | %s |\n" % (secs, th, et))
        w("\n")

    w("## Record this in 40-source-evidence.md\n\n")
    w("- The question, verbatim from Phase 3.\n")
    w("- The window and event types used (above).\n")
    w("- The result, including a nil result if that is what you got.\n")
    w("- Whether it confirms, refutes, or fails to discriminate the hypothesis. All three\n"
      "  are legitimate; the third means you need a different check, not a re-read.\n\n")


EXPLAIN = """\
jfr_query.py - Phase 4 JFR access, gated on a written question.

WHAT IT DOES
  Wraps the JDK `jfr` CLI. Applies an event-type filter and a time window, then REDUCES
  the result: counts per event type, per thread, dominant top frame per thread, a global
  frame histogram, and the longest durations. It does not print raw stacks.

WHY IT IS GATED
  A recording holds hundreds of thousands of events. Browsing it consumes your context
  and answers nothing. Phase 3 must produce the question, the window and the event types
  before this script is run; --question records it in the output.

EVENT GROUPS
  execution, monitor, gc, safepoint, io, socket, alloc, cpu
  (or pass exact names with --event-types jdk.ExecutionSample,jdk.SocketRead)

IMPORTANT
  Expect a HOLE in the recording during a JVM stall - sampling is impaired while threads
  cannot reach a safepoint. An empty window is evidence of the stall, not a failed query.
  Always run --summary first to confirm the recording covers your window and that the
  event types you need were enabled.

REQUIREMENTS
  The `jfr` binary from the JDK must be on PATH.

USAGE
  python jfr_query.py rec.jfr --summary
  python jfr_query.py rec.jfr --events execution --window "10:22:00" "10:23:30" \\
      --question "which thread failed to reach a safepoint at 10:22:32?"
"""


def main():
    ap = argparse.ArgumentParser(description="Phase 4: targeted JFR query.")
    ap.add_argument("recording", nargs="?", help="path to the .jfr file")
    ap.add_argument("--summary", action="store_true", help="print `jfr summary` and stop")
    ap.add_argument("--events", help="comma-separated group names: %s" % ", ".join(sorted(GROUPS)))
    ap.add_argument("--event-types", help="comma-separated exact jdk.* event names")
    ap.add_argument("--window", nargs=2, metavar=("START", "END"),
                    help="restrict to this time window")
    ap.add_argument("--question", help="the Phase 3 question this query answers")
    ap.add_argument("--stack-depth", type=int, default=8)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out")
    ap.add_argument("--explain", action="store_true")
    args = ap.parse_args()

    if args.explain:
        print(EXPLAIN)
        return 0
    if not args.recording:
        ap.error("give a .jfr recording (or --explain)")
    path = Path(args.recording)
    if not path.is_file():
        print("error: %s not found" % path, file=sys.stderr)
        return 1
    if not have_jfr():
        print("error: the `jfr` CLI is not on PATH. It ships with the JDK "
              "($JAVA_HOME/bin/jfr).", file=sys.stderr)
        return 1

    fh = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
    try:
        if args.summary or (not args.events and not args.event_types):
            summarise(path, fh)
            if not args.events and not args.event_types:
                fh.write("_No event selection given, so only the summary was produced._\n"
                         "_Choose event types from your Phase 3 question and re-run._\n")
            return 0
        types = []
        if args.events:
            for g in args.events.split(","):
                g = g.strip()
                if g not in GROUPS:
                    print("error: unknown group %r; known: %s"
                          % (g, ", ".join(sorted(GROUPS))), file=sys.stderr)
                    return 1
                types += GROUPS[g]
        if args.event_types:
            types += [t.strip() for t in args.event_types.split(",")]
        analyse(path, sorted(set(types)), args, fh)
    finally:
        if args.out:
            fh.close()
            print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
