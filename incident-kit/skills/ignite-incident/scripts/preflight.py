#!/usr/bin/env python3
"""Phase 0.5: check that the parsers actually understand THIS bundle's files.

Runs each digest's real line grammar over the real files and reports how much of each
one it understood -- and nothing else. No analysis, no findings, no interpretation.

WHY THIS EXISTS

A parser that half-works is more dangerous than one that crashes. It produces a thin,
plausible digest, and every phase downstream treats it as complete. The specific trap:
"the parser found no kernel events" and "the parser could not read this file" look
identical in a digest unless something measures the difference. This does.

It imports the digest modules rather than restating their regexes, so a parser fix
automatically improves preflight, and preflight can never disagree with the thing it
is checking.

TWO CHECKS

1. Parse rate  - what fraction of lines matched the line grammar.
2. Completeness - a dumb substring count of high-signal literals over the raw file,
   compared with how many the parser actually recognised. Catches the case where every
   line parses but the CONTENT patterns miss this site's messages, which parse rate
   alone cannot see.

Stdlib only.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import patterns as P                                     # noqa: E402
import gc_digest                                         # noqa: E402
import ignite_timeline                                   # noqa: E402
import nmon_digest                                       # noqa: E402
import os_digest                                         # noqa: E402
import threaddump_digest                                 # noqa: E402

# Literals whose occurrences map ONE-TO-ONE onto events the parser should recognise.
# raw count > recognised count then means events are being dropped silently.
#
# Chosen carefully: a literal that appears more than once per event produces false
# alarms. GC pause descriptions are deliberately absent because unified logging prints
# them twice, once under `gc,start` and once on completion, so a raw count is always
# higher than the event count. For GC the parse rate and the explicit "no safepoint
# records" warning already cover the same ground.
CROSSCHECK = {
    "ignite_log": ["SEGMENTED", "Node FAILED", "Node LEFT", "Possible too long JVM pause",
                   "Checkpoint finished", "Critical system error detected",
                   "Blocked system-critical thread", "Unable to await partitions release latch",
                   "Failed to send message to next node", "Topology snapshot"],
    "safepoint_log": ["Safepoint \"", "application threads were stopped"],
    "gc_safepoint_merged": ["Safepoint \"", "application threads were stopped"],
    "dmesg": ["Out of memory", "blocked for more than", "page allocation stall", "kswapd",
              "I/O error"],
    "syslog": ["Out of memory", "blocked for more than", "page allocation stall", "kswapd",
               "Main process exited"],
}

SKIP_KINDS = {"config_xml", "config_props", "jvm_flags", "jfr", "archive", "gzip",
              "binary", "empty"}


def check_ignite(path, overlay):
    h = P.Health(path.name, "ignite_log")
    layouts = ignite_timeline.overlay_layouts(overlay)
    events = ignite_timeline.overlay_events(overlay)
    raw_hits, seen_hits = _counters("ignite_log")
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            parsed = ignite_timeline.parse_line(line, layouts)
            if ignite_timeline.is_record_start(line):
                # A timestamp-only match is a DEGRADED parse, not a good one: it loses
                # the thread and logger fields the analysis relies on. Banner and
                # stack-trace continuations are not records, so they do not count.
                full = parsed is not None and (parsed[4] != "" or parsed[5] != "")
                h.line(bool(full), lineno, line)
            for lit in raw_hits:
                if lit in line:
                    raw_hits[lit] += 1
            if parsed is not None:
                msg = parsed[6]
                if any(rx.search(msg) for _c, _s, rx in events):
                    h.recognised += 1
                    for lit in seen_hits:
                        if lit in line:
                            seen_hits[lit] += 1
    return h, raw_hits, seen_hits


def check_jvm(path, kind, overlay):
    h = P.Health(path.name, kind)
    extra_sp = gc_digest.overlay_safepoints(overlay)
    raw_hits, seen_hits = _counters(kind)
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            u = gc_digest.parse_unified(line)
            h.line(u is not None, lineno, line)
            for lit in raw_hits:
                if lit in line:
                    raw_hits[lit] += 1
            if u is None:
                continue
            msg = u["msg"]
            hit = (gc_digest.RE_GC_EVENT.match(msg) or gc_digest.RE_SP17.search(msg)
                   or gc_digest.RE_SP11.search(msg)
                   or any(rx.search(msg) for rx in extra_sp))
            if hit:
                h.recognised += 1
                for lit in seen_hits:
                    if lit in line:
                        seen_hits[lit] += 1
    if h.lines and h.parsed == 0:
        h.note = ("No line matched unified-logging decorators. This is very likely a JDK 8 "
                  "-XX:+PrintGCDetails log, which this kit does not parse.")
    return h, raw_hits, seen_hits


def check_os(path, kind, overlay):
    h = P.Health(path.name, kind)
    pats = os_digest.all_patterns(overlay)
    raw_hits, seen_hits = _counters(kind)
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            stamp, clock, _host, _text = os_digest.parse(line)
            h.line(clock != "none", lineno, line)
            for lit in raw_hits:
                if lit in line:
                    raw_hits[lit] += 1
            if any(rx.search(line) for _l, _s, rx, _w in pats):
                h.recognised += 1
                for lit in seen_hits:
                    if lit in line:
                        seen_hits[lit] += 1
    return h, raw_hits, seen_hits


def check_nmon(path, overlay):
    h = P.Health(path.name, "nmon")
    meta, times, headers, data = nmon_digest.parse_nmon(path, overlay)
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        total = sum(1 for line in fh if line.strip())
    known = sum(len(v) for v in data.values()) + len(times) + len(meta) + len(headers)
    h.lines = total
    h.parsed = min(known, total)
    usable = [s for s in data if s in nmon_digest.INTEREST]
    h.recognised = len(usable)
    if not data:
        h.note = "No data sections recognised at all."
        h.parsed = 0
    elif not usable:
        # Lines parse fine, but none of the sections are ones the digest can read.
        # Reporting OK here would be exactly the silent half-working case.
        h.note = ("Sections found (%s) but NONE are recognised resource sections. Map them "
                  "with nmon_aliases in site-patterns.json." % ", ".join(sorted(data)))
        h.parsed = 0
        h.samples.append((None, "unrecognised sections: %s" % ", ".join(sorted(data))))
    else:
        h.note = "usable sections: %s" % ", ".join(sorted(usable))
    if h.parsed < h.lines and not h.samples:
        h.samples.append((None, "%d of %d lines matched no nmon record type"
                          % (h.lines - h.parsed, h.lines)))
    return h, {}, {}


def check_threaddump(path, overlay):
    h = P.Health(path.name, "thread_dump")
    threads, dump_time, _vm = threaddump_digest.parse_dump(path)
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        total = sum(1 for line in fh if line.strip())
    h.lines = total
    h.recognised = len(threads)
    stated = sum(1 for t in threads if t.state)
    # A dump is not line-oriented; health is "did we get threads, and do they have states".
    h.parsed = total if threads and stated else 0
    if not threads:
        h.note = "No threads parsed. Is this jstack output?"
        h.samples.append((None, "no lines matched the jstack thread-header grammar"))
    elif not stated:
        h.note = "Threads found but none had a parseable java.lang.Thread.State line."
        h.samples.append((None, "thread headers matched but state lines did not"))
    else:
        h.note = "%d threads, %d with a state%s" % (
            len(threads), stated, ", dump time %s" % dump_time if dump_time else
            ", NO dump timestamp found")
    return h, {}, {}


def _counters(kind):
    lits = CROSSCHECK.get(kind, [])
    return ({l: 0 for l in lits}, {l: 0 for l in lits})


CHECKERS = {
    "ignite_log": lambda p, k, o: check_ignite(p, o),
    "gc_log": check_jvm,
    "safepoint_log": check_jvm,
    "gc_safepoint_merged": check_jvm,
    "dmesg": check_os,
    "syslog": check_os,
    "nmon": lambda p, k, o: check_nmon(p, o),
    "thread_dump": lambda p, k, o: check_threaddump(p, o),
}


RE_POM_VER = re.compile(r"\|\s*`?[\w.-]+`?\s*\|[^|]*\|[^|]*\|[^|]*\|[^|]*\|[^|]*\|\s*"
                        r"([\w.\-+]+)\s*\|")


def check_indexes(indexes, bundle_vers, out):
    """Compare the indexed source against the bundle, and say so loudly on mismatch.

    This replaces the old `indexes/<version>/` placeholder: there is exactly one index
    set per analysis, so the question is not "which index" but "was it built from the
    right code". A line number cited from the wrong branch is worse than none.
    """
    w = out.write
    w("## Source index provenance\n\n")
    idx = Path(indexes) if indexes else None
    if not idx or not idx.is_dir():
        w("**No index directory.** Phase 4 source lookups will not be possible. Run\n"
          "`kit.py init <bundle> --repo <path> ...` to build one.\n\n")
        return
    info = idx / "INDEX-INFO.md"
    msgs = idx / "messages.tsv"
    if not msgs.is_file():
        w("**`messages.tsv` is missing from `%s`.** Re-run `kit.py init`.\n\n" % idx)
        return
    with msgs.open("r", encoding="utf-8", errors="replace") as fh:
        n = sum(1 for _ in fh) - 1
    w("Index: `%s` (%d message entries)\n\n" % (idx, max(n, 0)))
    if not info.is_file():
        w("**INDEX-INFO.md is missing** - the provenance of this index is unknown.\n\n")
        return
    text = info.read_text(encoding="utf-8", errors="replace")
    w(text.split("## Version check")[0].split("\n", 2)[-1].strip() + "\n\n")

    idx_vers = set(re.findall(r"\|\s*(\d+\.\d+\.\d+[\w.\-+]*)\s*\|\s*\d+\s*\|", text))
    w("| | |\n|---|---|\n")
    w("| Ignite version in the bundle | %s |\n" % (", ".join(sorted(bundle_vers)) or "not found"))
    w("| Version of the indexed source | %s |\n\n" % (", ".join(sorted(idx_vers)) or "not stated"))

    if "**YES**" in text:
        w("> **An indexed repository has uncommitted changes.** Its line numbers describe a\n"
          "> working copy, not any released build. Either commit/stash and re-run\n"
          "> `kit.py init`, or cite classes and methods rather than line numbers.\n\n")
    if bundle_vers and idx_vers and not (bundle_vers & idx_vers):
        w("> ### VERSION MISMATCH\n>\n")
        w("> The bundle came from Ignite %s but the index was built from %s. Class and\n"
          "> method names are usually stable across 2.x; **line numbers are not**. Cite the\n"
          "> class and method, not the line, or re-run `kit.py init` against a checkout of\n"
          "> the matching release.\n\n"
          % (", ".join(sorted(bundle_vers)), ", ".join(sorted(idx_vers))))
    elif bundle_vers and idx_vers:
        w("Versions agree - line numbers from this index can be cited directly.\n\n")


def render(rows, unknowns, skipped, overlay_path, overlay, out,
           indexes=None, bundle_vers=None):
    w = out.write
    w("# 00.5 - Preflight (Phase 0.5)\n\n")
    w("Does the tooling understand this bundle? No analysis happens here.\n\n")
    w("%s\n\n" % P.overlay_note(overlay_path, overlay))

    healths = [r["health"] for r in rows]
    verdict = P.worst(healths) if healths else P.FAILED
    if unknowns:
        verdict = P.FAILED

    w("## Verdict: **%s**\n\n" % verdict)
    if verdict == P.OK and not unknowns:
        w("Every file parses cleanly. Phase 1 may start.\n\n")
    elif verdict == P.DEGRADED:
        w("At least one file parsed only partially. **Do not start Phase 1 yet.** Work the\n"
          "ladder in `references/90-when-scripts-fail.md`. If you decide to proceed anyway,\n"
          "you must write in `00-inventory.md` which files are degraded and what you will\n"
          "therefore NOT claim from them.\n\n")
    else:
        w("At least one file could not be read. **Phase 1 is blocked.** Work the ladder in\n"
          "`references/90-when-scripts-fail.md`.\n\n")

    if healths:
        P.render_health(healths, out)

    # ---- completeness ---------------------------------------------------- #
    w("## Completeness cross-check\n\n")
    w("A plain substring count over the raw file, against what the parser recognised. A raw\n"
      "count higher than the recognised count means events are being dropped even though the\n"
      "lines parse - the line grammar is right but the content patterns are missing this\n"
      "site's messages. Extend `ignite_events` / `os_patterns` in the overlay, not the layout.\n\n")
    any_gap = False
    printed = False
    for r in rows:
        raw, seen = r["raw"], r["seen"]
        gaps = [(l, raw[l], seen.get(l, 0)) for l in raw if raw[l] > seen.get(l, 0)]
        if not gaps:
            continue
        if not printed:
            w("| file | literal | in file | recognised |\n|---|---|---|---|\n")
            printed = True
        any_gap = True
        for lit, rc, sc in sorted(gaps, key=lambda x: -(x[1] - x[2])):
            w("| `%s` | `%s` | %d | %d |\n" % (r["health"].name, lit, rc, sc))
    if not any_gap:
        w("No gaps: every high-signal literal present in the files was also recognised by a\n"
          "content pattern.\n")
    w("\n")

    # ---- unknown / skipped ----------------------------------------------- #
    if unknowns:
        w("## Unclassified files (Phase 0 gate still open)\n\n")
        for u in unknowns:
            w("- `%s` - identify.py could not classify this. Resolve it in Phase 0 before\n"
              "  preflight can say anything about it.\n" % u)
        w("\n")
    if skipped:
        w("## Not checked\n\n")
        for name, kind in skipped:
            w("- `%s` (%s) - no line grammar to check; read directly or handled in Phase 4.\n"
              % (name, kind))
        w("\n")

    check_indexes(indexes, bundle_vers or set(), out)

    w("## Phase 0.5 gate\n\n")
    w("- [ ] Verdict is OK, **or** every DEGRADED/FAILED file is listed in `00-inventory.md`\n"
      "      with what will not be claimed from it\n")
    w("- [ ] The completeness cross-check shows no unexplained gaps\n")
    w("- [ ] Any overlay added has been verified with `selftest.py` (must stay green)\n\n")


EXPLAIN = """\
preflight.py - Phase 0.5. Does the tooling understand this bundle?

WHAT IT DOES
  Runs each digest's real line grammar over the real files and reports, per file:
  lines, lines parsed, parse rate, content matches, and a verdict OK/DEGRADED/FAILED.
  Plus a completeness cross-check: a plain substring count of high-signal literals in
  the raw file versus how many the parser recognised.

  It imports the digest modules rather than restating their regexes, so it can never
  disagree with the parsers it is checking.

WHAT IT DOES NOT DO
  Any analysis at all. It produces no findings and reads no meaning into anything.

WHY BOTH CHECKS
  Parse rate catches "the line format is different". The cross-check catches "the lines
  parse fine but this site's messages differ", which parse rate cannot see - the digest
  would just come out thin, and look like a quiet incident.

EXIT CODES
  0 = OK.  2 = DEGRADED (something parsed only partially).  1 = FAILED or unclassified
  files present. Non-zero means work references/90-when-scripts-fail.md before Phase 1.

USAGE
  python preflight.py --inventory analysis/inventory.json --out analysis/00.5-preflight.md
  python preflight.py --inventory analysis/inventory.json --patterns site-patterns.json
"""


def main():
    ap = argparse.ArgumentParser(description="Phase 0.5: can the parsers read this bundle?")
    ap.add_argument("--inventory", help="inventory.json from identify.py")
    ap.add_argument("--out", help="write Markdown here instead of stdout")
    ap.add_argument("--json", dest="json_out", help="machine-readable preflight result")
    ap.add_argument("--patterns", help="site-patterns.json overlay")
    ap.add_argument("--indexes", help="analysis/indexes directory, to check its provenance")
    ap.add_argument("--node", help="restrict to one node")
    ap.add_argument("--explain", action="store_true")
    args = ap.parse_args()

    if args.explain:
        print(EXPLAIN)
        return 0
    if not args.inventory:
        ap.error("--inventory is required (run identify.py first), or pass --explain")

    try:
        overlay, overlay_path = P.load_overlay(args.patterns, near=args.inventory)
    except P.OverlayError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1

    data = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
    base = Path(data["root"])

    # The Ignite version the bundle itself reports, for the provenance comparison.
    bundle_vers = {f.get("details", {}).get("ignite_version", "").split("#")[0]
                   for f in data["files"]}
    bundle_vers = {v for v in bundle_vers if v}

    rows, unknowns, skipped = [], [], []
    for f in data["files"]:
        if args.node and f.get("node") != args.node:
            continue
        kind, rel = f["kind"], f["path"]
        if kind == "unknown":
            unknowns.append(rel)
            continue
        if kind in SKIP_KINDS:
            skipped.append((rel, kind))
            continue
        checker = CHECKERS.get(kind)
        if checker is None:
            skipped.append((rel, kind))
            continue
        path = base / rel
        if not path.is_file():
            print("warning: %s missing, skipped" % path, file=sys.stderr)
            continue
        health, raw, seen = checker(path, kind, overlay)
        health.name = rel
        rows.append({"health": health, "raw": raw, "seen": seen})

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            render(rows, unknowns, skipped, overlay_path, overlay, fh,
                   args.indexes, bundle_vers)
        print("wrote %s" % args.out)
    else:
        render(rows, unknowns, skipped, overlay_path, overlay, sys.stdout,
               args.indexes, bundle_vers)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "files": [r["health"].as_dict() for r in rows],
            "unknown": unknowns,
            "crosscheck": [{"file": r["health"].name,
                            "gaps": {l: [r["raw"][l], r["seen"].get(l, 0)]
                                     for l in r["raw"] if r["raw"][l] > r["seen"].get(l, 0)}}
                           for r in rows],
        }, indent=2), encoding="utf-8")

    healths = [r["health"] for r in rows]
    if unknowns or (healths and P.worst(healths) == P.FAILED) or not healths:
        return 1
    return 2 if P.worst(healths) == P.DEGRADED else 0


if __name__ == "__main__":
    sys.exit(main())
