#!/usr/bin/env python3
"""Phase 3: merge the phase digests into one time-aligned table.

Reads the JSON side-outputs produced by the earlier scripts (timeline.json from
ignite_timeline.py, gc.json from gc_digest.py, and any others present in the analysis
directory) and renders a single chronological view, so hypotheses are formed against one
sequence rather than four separate documents.

CLOCK ALIGNMENT IS YOUR JOB, NOT THIS SCRIPT'S. Different artifacts use different clocks
(see 00-inventory.md). Pass the offsets you established in Phase 0 with --offset; the
script applies them and records what it applied, so the report can state it. Sources with
no offset given are assumed to be already in the reference timezone, and the output says
so explicitly for each one.

Stdlib only.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path


def load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("warning: could not read %s (%s)" % (path, exc), file=sys.stderr)
        return None


def parse_iso(s):
    if not s:
        return None
    s = s.replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def gather(analysis, offsets, args):
    """Return (rows, sources) where rows are dicts with dt/source/node/kind/text."""
    rows = []
    sources = []

    tl = load(analysis / "timeline.json")
    if tl:
        off = offsets.get("ignite", timedelta(0))
        sources.append(("ignite", "timeline.json", off))
        for e in tl.get("events", []):
            dt = parse_iso(e.get("dt"))
            if not dt or e.get("sev", 0) < args.min_severity:
                continue
            rows.append({"dt": dt + off, "source": "ignite", "node": e.get("node", "?"),
                         "kind": e.get("cat", "?"),
                         "text": (e.get("msg") or "")[:args.width],
                         "ref": "%s:%s" % (Path(e.get("file", "")).name, e.get("line", "?"))})
        for g in tl.get("gaps", []):
            s, t = parse_iso(g.get("start")), parse_iso(g.get("end"))
            if s and t and g.get("seconds", 0) >= args.gap_seconds:
                rows.append({"dt": s + off, "source": "ignite", "node": g.get("node", "?"),
                             "kind": "GAP",
                             "text": "log silent for %.1fs (resumes %s)"
                                     % (g["seconds"], (t + off).strftime("%H:%M:%S")),
                             "ref": "gap"})
        for c in tl.get("checkpoints", []):
            dt = parse_iso(c.get("dt"))
            if dt and c.get("total_ms") and c["total_ms"] >= args.checkpoint_ms:
                rows.append({"dt": dt + off, "source": "ignite", "node": c.get("node", "?"),
                             "kind": "checkpoint",
                             "text": "checkpoint finished, total=%.1fs pages=%s %s"
                                     % (c["total_ms"] / 1000.0, c.get("pages"),
                                        c.get("parts")),
                             "ref": "cp"})

    # One gc.json per node is normal when nodes run different collectors, so take
    # gc.json plus anything matching gc-*.json / gc_*.json.
    gc_files = sorted(set(list(analysis.glob("gc.json"))
                          + list(analysis.glob("gc-*.json"))
                          + list(analysis.glob("gc_*.json"))))
    for gc_path in gc_files:
        gc = load(gc_path)
        if not gc:
            continue
        off = offsets.get("gc", timedelta(0))
        sources.append(("gc", gc_path.name, off))
        node = gc.get("node", "?")
        for s in gc.get("safepoints", []):
            dt = parse_iso(s.get("dt"))
            if not dt or s.get("total_s", 0) < args.safepoint_s:
                continue
            rows.append({"dt": dt + off, "source": "jvm", "node": node,
                         "kind": "safepoint",
                         "text": "%s: TTSP %.3fs + at-safepoint %.3fs = %.3fs"
                                 % (s.get("op"), s.get("ttsp_s", 0), s.get("at_s", 0),
                                    s.get("total_s", 0)),
                         "ref": "%s:%s" % (s.get("file", "?"), s.get("line", "?"))})
        for e in gc.get("gc_events", []):
            dt = parse_iso(e.get("dt"))
            if not dt or not e.get("stw") or e.get("ms", 0) < args.gc_ms:
                continue
            rows.append({"dt": dt + off, "source": "jvm", "node": node, "kind": "gc",
                         "text": "%s %.1fms" % (e.get("desc", "")[:60], e.get("ms", 0)),
                         "ref": "%s:%s" % (e.get("file", "?"), e.get("line", "?"))})

    osj = load(analysis / "os.json")
    if osj:
        off = offsets.get("os", timedelta(0))
        sources.append(("os", "os.json", off))
        for label, items in osj.items():
            for r in items:
                if r.get("clock") != "wall":
                    continue   # monotonic entries cannot be placed without alignment
                dt = None
                st = r.get("stamp")
                if st:
                    m = re.match(r"^([A-Z][a-z]{2})\s+(\d{1,2}) (\d{2}):(\d{2}):(\d{2})$", st)
                    if m and args.year:
                        months = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                                  "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
                        try:
                            dt = datetime(args.year, months[m.group(1)], int(m.group(2)),
                                          int(m.group(3)), int(m.group(4)), int(m.group(5)))
                        except (KeyError, ValueError):
                            dt = None
                    else:
                        dt = parse_iso(st)
                if dt:
                    rows.append({"dt": dt + off, "source": "os",
                                 "node": r.get("host", "?"), "kind": label,
                                 "text": (r.get("text") or "")[:args.width],
                                 "ref": "%s:%s" % (r.get("file"), r.get("line"))})

    rows.sort(key=lambda r: r["dt"])
    return rows, sources


def render(rows, sources, offsets, args, out):
    w = out.write
    w("# 30 - Correlated timeline (Phase 3)\n\n")

    w("## Clock handling\n\n")
    if not sources:
        w("_No JSON side-outputs found._ Re-run the phase scripts with their `--json`\n"
          "option so this script has something to merge.\n\n")
        return
    w("| source | file | offset applied |\n|---|---|---|\n")
    for name, fname, off in sources:
        w("| %s | `%s` | %s |\n" % (
            name, fname,
            "**none - assumed already in the reference timezone**" if off == timedelta(0)
            else "%+d s" % off.total_seconds()))
    w("\n")
    if all(off == timedelta(0) for _n, _f, off in sources):
        w("> **No offsets were supplied.** Every source above is being treated as already\n"
          "> in one timezone. If `00-inventory.md` says otherwise, this table is wrong.\n"
          "> Re-run with `--offset ignite=+10800` (seconds) for each source that needs it.\n\n")
    w("dmesg entries with monotonic (`+Ns`) stamps are **excluded** from this table - they\n"
      "cannot be placed on a wall clock without the boot-time alignment. Use the matching\n"
      "wall-clock lines from `messages` instead.\n\n")

    if not rows:
        w("## No rows\n\nNothing passed the thresholds. Lower them (`--min-severity 1`,\n"
          "`--safepoint-s 0.1`, `--gc-ms 50`) or widen the window.\n\n")
        return

    w("## Merged sequence (%d rows, %s .. %s)\n\n"
      % (len(rows), rows[0]["dt"].strftime("%H:%M:%S"), rows[-1]["dt"].strftime("%H:%M:%S")))
    w("| time | source | node | kind | detail | ref |\n|---|---|---|---|---|---|\n")
    shown = rows[:args.max_rows]
    prev = None
    for r in shown:
        if prev is not None and (r["dt"] - prev).total_seconds() >= args.mark_gap:
            w("| _..._ | | | **%.0fs with nothing recorded** | | |\n"
              % (r["dt"] - prev).total_seconds())
        w("| %s | %s | %s | %s | %s | %s |\n" % (
            r["dt"].strftime("%H:%M:%S.%f")[:-3], r["source"], r["node"], r["kind"],
            r["text"].replace("|", "\\|"), r["ref"]))
        prev = r["dt"]
    if len(rows) > len(shown):
        w("\n_%d more rows suppressed; raise --max-rows or narrow the window._\n" % (len(rows) - len(shown)))
    w("\n")

    w("## Phase 3 gate\n\n")
    w("Write `30-hypotheses.md` now. For each hypothesis:\n\n")
    w("1. **Statement** - one sentence.\n")
    w("2. **Prediction** - what else must be true if this is the cause.\n")
    w("3. **Evidence for / against** - with refs from the table above.\n")
    w("4. **Cheapest discriminating check** - the single thing that would move confidence most.\n\n")
    w("Then write the Phase 4 decision explicitly:\n\n")
    w("- Is more evidence needed at all? If not, go to Phase 5. That is a good outcome.\n")
    w("- If JFR: the question, the window to the second, and the event types.\n")
    w("- If source: the exact log line, and what you expect the code to reveal.\n\n")
    w("Do not open JFR or Ignite sources before this section is written.\n\n")


EXPLAIN = """\
correlate.py - Phase 3 merge of the phase digests.

WHAT IT DOES
  Reads timeline.json / gc.json / os.json from the analysis directory and renders one
  chronological table across Ignite, JVM and OS sources, marking periods where nothing
  was recorded at all.

WHAT IT DOES NOT DO
  It does not align clocks for you. Different artifacts use different clocks; you
  establish the offsets in Phase 0 and pass them with --offset. Sources without an
  offset are assumed to be in the reference timezone already, and the output says so
  for each source so a wrong assumption is visible rather than silent.

  dmesg rows with monotonic stamps are excluded by design - they cannot be placed on a
  wall clock without boot-time alignment.

USAGE
  python correlate.py --analysis ./analysis --out 30-correlated.md
  python correlate.py --analysis ./analysis --offset gc=-10800 --year 2024
"""


def main():
    ap = argparse.ArgumentParser(description="Phase 3: correlate the digests.")
    ap.add_argument("--analysis", help="the analysis workspace directory")
    ap.add_argument("--out")
    ap.add_argument("--offset", action="append", default=[],
                    help="clock offset in seconds, e.g. --offset ignite=+10800 (repeatable)")
    ap.add_argument("--year", type=int, help="year for syslog stamps (they omit it)")
    ap.add_argument("--min-severity", type=int, default=2)
    ap.add_argument("--safepoint-s", type=float, default=0.5,
                    help="include safepoints at or above this total (default 0.5s)")
    ap.add_argument("--gc-ms", type=float, default=200.0,
                    help="include STW GC pauses at or above this (default 200ms)")
    ap.add_argument("--gap-seconds", type=float, default=30.0)
    ap.add_argument("--checkpoint-ms", type=float, default=10000.0)
    ap.add_argument("--mark-gap", type=float, default=120.0,
                    help="mark stretches with nothing recorded above this many seconds")
    ap.add_argument("--max-rows", type=int, default=150)
    ap.add_argument("--width", type=int, default=130)
    ap.add_argument("--explain", action="store_true")
    args = ap.parse_args()

    if args.explain:
        print(EXPLAIN)
        return 0
    if not args.analysis:
        ap.error("--analysis is required (or pass --explain)")

    offsets = {}
    for spec in args.offset:
        if "=" not in spec:
            raise SystemExit("error: --offset needs the form source=seconds, got %r" % spec)
        k, v = spec.split("=", 1)
        try:
            offsets[k.strip()] = timedelta(seconds=float(v))
        except ValueError:
            raise SystemExit("error: offset %r is not a number of seconds" % v)

    analysis = Path(args.analysis)
    if not analysis.is_dir():
        print("error: %s is not a directory" % analysis, file=sys.stderr)
        return 1

    rows, sources = gather(analysis, offsets, args)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            render(rows, sources, offsets, args, fh)
        print("wrote %s (%d rows from %d sources)" % (args.out, len(rows), len(sources)))
    else:
        render(rows, sources, offsets, args, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
