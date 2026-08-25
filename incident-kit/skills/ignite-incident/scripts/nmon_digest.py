#!/usr/bin/env python3
"""Phase 2: digest an nmon capture.

nmon files are wide CSV with one section per resource and a ZZZZ record mapping each
snapshot id (T0001...) to a wall-clock time. This script pulls the series that matter for
an Ignite node, reports the worst samples, and flags the patterns that explain a stall.

RESOLUTION WARNING: nmon samples every 30-60 s by default. An event shorter than the
interval can be completely invisible. The digest states the interval every run, and you
must not report 'resources were normal' for an event shorter than it.

Stdlib only.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import patterns as P                                      # noqa: E402

RE_AAA = re.compile(r"^AAA,(\w+),(.*)$")
RE_ZZZZ = re.compile(r"^ZZZZ,(T\d+),(\d{2}:\d{2}:\d{2}),(\d{2}-[A-Z]{3}-\d{4})")
RE_DATA = re.compile(r"^([A-Z][A-Z0-9_]*),(T\d+),(.*)$")
RE_HEADER = re.compile(r"^([A-Z][A-Z0-9_]*),([^,T][^,]*),(.*)$")

MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
          "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}

# section -> (column name fragments we care about, higher_is_worse, unit, why)
INTEREST = {
    "CPU_ALL": ([("Idle%", False), ("Wait%", True), ("Sys%", True),
                 ("User%", True), ("Steal%", True)], "%",
                "Idle near zero starves safepoint arrival. High Wait% is I/O. "
                "Steal% is the hypervisor taking time the JVM cannot see."),
    "MEM": ([("memfree", False), ("swapfree", False), ("cached", True),
             ("swapcached", True), ("buffers", True)], "MB",
            "Falling swapfree is the strongest single predictor of a long "
            "time-to-safepoint. Falling memfree with rising cached is page-cache "
            "pressure from checkpointing."),
    "DISKBUSY": ([], "%", "Device saturation. Explains checkpoint fsync spikes."),
    "DISKREAD": ([], "KB/s", "Read throughput."),
    "DISKWRITE": ([], "KB/s", "Write throughput - compare with checkpoint windows."),
    "NET": ([], "KB/s", "Interface throughput. A collapse correlates with, but does "
                        "not prove, a network fault."),
    "NETERROR": ([], "count", "Interface errors. Real evidence for a network claim."),
    "PROC": ([("Runnable", True), ("Blocked", True), ("pswitch", True)], "count",
             "Runnable above core count means CPU starvation. Blocked means "
             "uninterruptible I/O wait."),
    "VM": ([("pgpgin", True), ("pgpgout", True), ("pswpin", True), ("pswpout", True)],
           "pages", "pswpin/pswpout non-zero means active swapping."),
}


def alias_map(overlay):
    """{alias_section: canonical_section} from the overlay's nmon_aliases."""
    out = {}
    for canonical, aliases in (overlay or {}).get("nmon_aliases", {}).items():
        for a in aliases:
            out[a] = canonical
    return out


def parse_nmon(path, overlay=None):
    aliases = alias_map(overlay)
    meta = {}
    times = {}
    headers = {}
    data = defaultdict(dict)   # section -> {snap: [values]}
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\r\n")
            if not line:
                continue
            m = RE_AAA.match(line)
            if m:
                meta[m.group(1)] = m.group(2)
                continue
            m = RE_ZZZZ.match(line)
            if m:
                snap, hhmmss, ddmonyyyy = m.groups()
                try:
                    d, mon, y = ddmonyyyy.split("-")
                    h, mi, s = hhmmss.split(":")
                    times[snap] = datetime(int(y), MONTHS[mon], int(d), int(h), int(mi), int(s))
                except (ValueError, KeyError):
                    times[snap] = None
                continue
            m = RE_DATA.match(line)
            if m:
                sect, snap, rest = m.groups()
                if sect in ("ZZZZ", "AAA", "BBB"):
                    continue
                sect = aliases.get(sect, sect)
                data[sect][snap] = rest.split(",")
                continue
            m = RE_HEADER.match(line)
            if m:
                sect, _title, cols = m.groups()
                sect = aliases.get(sect, sect)
                if sect not in headers:
                    headers[sect] = [c.strip() for c in cols.split(",")]
    return meta, times, headers, data


def to_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def series(section, headers, data, times):
    """Return (columns, [(dt, [values])]) sorted by time."""
    cols = headers.get(section, [])
    rows = []
    for snap, vals in data.get(section, {}).items():
        dt = times.get(snap)
        rows.append((dt, snap, [to_float(v) for v in vals]))
    rows.sort(key=lambda r: (r[0] is None, r[0] or datetime.min, r[1]))
    return cols, rows


def col_index(cols, fragment):
    for i, c in enumerate(cols):
        if fragment.lower() in c.lower():
            return i
    return None


def fmt_dt(d):
    return d.strftime("%H:%M:%S") if d else "?"


def render(meta, times, headers, data, args, out):
    w = out.write
    w("# 20 - nmon digest (Phase 2)\n\n")
    host = meta.get("host", "?")
    interval = meta.get("interval", "?")
    w("| | |\n|---|---|\n")
    w("| Host | %s |\n" % host)
    w("| Date | %s |\n" % meta.get("date", "?"))
    w("| Sample interval | **%s s** |\n" % interval)
    w("| Snapshots | %s |\n" % meta.get("snapshots", len(times)))
    stamps = sorted([t for t in times.values() if t])
    if stamps:
        w("| Covers | %s - %s |\n" % (fmt_dt(stamps[0]), fmt_dt(stamps[-1])))
    w("| Sections present | %s |\n" % ", ".join(sorted(data.keys())))
    w("\n")

    try:
        iv = float(interval)
    except (TypeError, ValueError):
        iv = None
    w("> **Resolution limit.** Samples are %s seconds apart. An event shorter than that\n"
      "> can be entirely invisible here. If the stall you are investigating is shorter\n"
      "> than one interval, do NOT write 'nmon shows nothing unusual' - write that nmon\n"
      "> cannot resolve an event of that length.\n\n" % interval)

    flags = []

    for section in ("CPU_ALL", "MEM", "PROC", "VM", "DISKBUSY", "DISKWRITE", "DISKREAD",
                    "NET", "NETERROR"):
        if section not in data:
            continue
        cols, rows = series(section, headers, data, times)
        if not rows:
            continue
        interesting, unit, why = INTEREST.get(section, ([], "", ""))
        w("## %s\n\n" % section)
        if why:
            w("_%s_\n\n" % why)

        if interesting:
            picked = [(frag, worse, col_index(cols, frag)) for frag, worse in interesting]
            picked = [(f, wo, i) for f, wo, i in picked if i is not None]
            if picked:
                w("| time | %s |\n" % " | ".join(f for f, _, _ in picked))
                w("|---|%s\n" % ("---|" * len(picked)))
                step = max(1, len(rows) // args.rows)
                for dt, _snap, vals in rows[::step][:args.rows]:
                    cells = []
                    for _f, _wo, i in picked:
                        v = vals[i] if i < len(vals) else None
                        cells.append("%.1f" % v if v is not None else "-")
                    w("| %s | %s |\n" % (fmt_dt(dt), " | ".join(cells)))
                w("\n")
                for frag, worse, i in picked:
                    vv = [(v[i], d) for d, _s, v in rows if i < len(v) and v[i] is not None]
                    if not vv:
                        continue
                    if worse:
                        val, when = max(vv)
                        w("- peak `%s` = **%.1f %s** at %s\n" % (frag, val, unit, fmt_dt(when)))
                    else:
                        val, when = min(vv)
                        w("- lowest `%s` = **%.1f %s** at %s\n" % (frag, val, unit, fmt_dt(when)))
                w("\n")
        else:
            # unnamed columns (per-device): report the worst column/sample
            best = None
            for dt, _snap, vals in rows:
                for i, v in enumerate(vals):
                    if v is None:
                        continue
                    if best is None or v > best[0]:
                        best = (v, dt, cols[i] if i < len(cols) else "col%d" % i)
            if best:
                w("- peak **%.1f %s** on `%s` at %s\n\n" % (best[0], unit, best[2], fmt_dt(best[1])))
            step = max(1, len(rows) // args.rows)
            show_cols = cols[:6]
            if show_cols:
                w("| time | %s |\n|---|%s\n" % (" | ".join(show_cols), "---|" * len(show_cols)))
                for dt, _snap, vals in rows[::step][:args.rows]:
                    cells = ["%.1f" % vals[i] if i < len(vals) and vals[i] is not None else "-"
                             for i in range(len(show_cols))]
                    w("| %s | %s |\n" % (fmt_dt(dt), " | ".join(cells)))
                w("\n")

        # ---- flags ------------------------------------------------------- #
        if section == "MEM":
            i = col_index(cols, "swapfree")
            j = col_index(cols, "swaptotal")
            if i is not None:
                vv = [(v[i], d) for d, _s, v in rows if i < len(v) and v[i] is not None]
                if vv and len(vv) > 1:
                    drop = vv[0][0] - min(x[0] for x in vv)
                    tot = None
                    if j is not None:
                        tj = [v[j] for _d, _s, v in rows if j < len(v) and v[j] is not None]
                        tot = tj[0] if tj else None
                    if drop > 1.0:
                        flags.append(
                            "**Swap was consumed**: swapfree fell by %.0f MB%s during the "
                            "capture. Active swapping is the leading explanation for a long "
                            "time-to-safepoint. Cross-check `dmesg` reclaim stalls and the "
                            "TTSP figure from `gc_digest.py`."
                            % (drop, " of %.0f MB total" % tot if tot else ""))
            i = col_index(cols, "memfree")
            if i is not None:
                vv = [v[i] for _d, _s, v in rows if i < len(v) and v[i] is not None]
                if vv and min(vv) < 0.02 * max(vv + [1]):
                    flags.append("**Free memory approached zero** (min %.0f MB)." % min(vv))
        if section == "CPU_ALL":
            i = col_index(cols, "Idle%")
            if i is not None:
                vv = [(v[i], d) for d, _s, v in rows if i < len(v) and v[i] is not None]
                if vv and min(x[0] for x in vv) < 5.0:
                    val, when = min(vv)
                    flags.append("**CPU saturated**: idle fell to %.1f%% at %s."
                                 % (val, fmt_dt(when)))
            i = col_index(cols, "Wait%")
            if i is not None:
                vv = [(v[i], d) for d, _s, v in rows if i < len(v) and v[i] is not None]
                if vv and max(x[0] for x in vv) > 25.0:
                    val, when = max(vv)
                    flags.append("**High I/O wait**: %.1f%% at %s - look at DISKBUSY and the "
                                 "checkpoint timings for the same minutes." % (val, fmt_dt(when)))
            i = col_index(cols, "Steal%")
            if i is not None:
                vv = [v[i] for _d, _s, v in rows if i < len(v) and v[i] is not None]
                if vv and max(vv) > 2.0:
                    flags.append("**Hypervisor steal time %.1f%%** - the guest was denied CPU. "
                                 "Invisible to the JVM." % max(vv))
        if section == "DISKBUSY":
            mx = 0.0
            when = None
            for dt, _s, vals in rows:
                for v in vals:
                    if v is not None and v > mx:
                        mx, when = v, dt
            if mx > 90.0:
                flags.append("**Disk saturated**: %.0f%% busy at %s." % (mx, fmt_dt(when)))

    if flags:
        w("## Flags\n\n")
        for f in flags:
            w("- %s\n" % f)
        w("\n")
    else:
        w("## Flags\n\nNo threshold was crossed in the sampled data")
        w(" (remember the %s s resolution limit).\n\n" % interval)

    w("## Phase 2 gate (nmon portion)\n\n")
    w("Record in `20-resource-findings.md`:\n\n")
    w("1. The sample interval, and whether it can resolve the event you are investigating.\n")
    w("2. Whether memory/swap, CPU or disk crossed a threshold inside the incident window.\n")
    w("3. If a flag fired, the matching evidence from `gc_digest.py` and `os_digest.py` -\n"
      "   three artifacts agreeing on the same minute is what makes a cause defensible.\n\n")


EXPLAIN = """\
nmon_digest.py - Phase 2 digest of an nmon capture.

WHAT IT DOES
  Parses nmon CSV (AAA metadata, ZZZZ snapshot->time map, one section per resource) and
  reports CPU, memory/swap, process queue, VM paging, disk and network series: a sampled
  table, the worst value per metric, and threshold flags with an interpretation.

WHY IT EXISTS
  nmon files are thousands of wide CSV lines. The five numbers that matter for a JVM
  stall are buried in them.

RESOLUTION
  The digest prints the sample interval prominently. An event shorter than the interval
  may leave no trace at all -- reporting 'resources were normal' in that case is wrong.

FLAGS RAISED
  swap consumed, free memory near zero, CPU saturated, high I/O wait, hypervisor steal,
  disk saturated. Each names the cross-check that would confirm it.

ASSUMPTIONS
  - Standard nmon CSV. Section header rows are the first non-Tnnnn row for that section.
  - Memory values are MB as nmon reports them; no unit conversion is applied.

USAGE
  python nmon_digest.py --inventory inventory.json --out 20-nmon.md
  python nmon_digest.py node03_240314.nmon --rows 20
"""


def collect_inputs(args):
    paths = []
    if args.inventory:
        data = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
        base = Path(data["root"])
        for f in data["files"]:
            if f["kind"] == "nmon":
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
    ap = argparse.ArgumentParser(description="Phase 2: nmon digest.")
    ap.add_argument("targets", nargs="*", default=[])
    ap.add_argument("--inventory")
    ap.add_argument("--node")
    ap.add_argument("--out")
    ap.add_argument("--rows", type=int, default=15, help="sampled rows per section")
    ap.add_argument("--patterns", help="site-patterns.json overlay")
    ap.add_argument("--diagnose", action="store_true",
                    help="report parse health instead of the digest")
    ap.add_argument("--explain", action="store_true")
    args = ap.parse_args()

    if args.explain:
        print(EXPLAIN)
        return 0

    paths = collect_inputs(args)
    if not paths:
        print("error: no nmon files. Run identify.py and pass --inventory.", file=sys.stderr)
        return 1

    try:
        overlay, overlay_path = P.load_overlay(args.patterns, near=args.inventory)
    except P.OverlayError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1

    parsed = [(p, parse_nmon(p, overlay)) for p in paths]
    healths = []
    for p, (meta, times, headers, data) in parsed:
        h = P.Health(p.name, "nmon")
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            h.lines = sum(1 for line in fh if line.strip())
        known = sum(len(v) for v in data.values()) + len(times) + len(meta) + len(headers)
        h.parsed = min(known, h.lines)
        usable = [s for s in data if s in INTEREST]
        h.recognised = len(usable)
        if not usable:
            h.note = ("No usable resource sections%s. If this capture uses non-standard "
                      "section names, map them with nmon_aliases in site-patterns.json."
                      % (" (found: %s)" % ", ".join(sorted(data)) if data else ""))
            h.parsed = 0
        else:
            h.note = "usable sections: %s" % ", ".join(sorted(usable))
        healths.append(h)

    if args.diagnose:
        P.render_diagnose(healths, sys.stdout, overlay_path, overlay)
        return 0 if P.worst(healths) == P.OK else 2

    fh_out = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
    try:
        P.render_health(healths, fh_out, overlay_path, overlay)
        for (p, (meta, times, headers, data)), h in zip(parsed, healths):
            if not data:
                # Surfaced in Markdown, not just stderr: a warning the model never sees
                # is a warning that does not exist.
                fh_out.write("## `%s`\n\nNo nmon data sections were recognised in this file, so "
                             "nothing below covers it. This is a parsing problem, not a finding "
                             "about the machine.\n\n" % p.name)
                continue
            render(meta, times, headers, data, args, fh_out)
    finally:
        if args.out:
            fh_out.close()
            print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
