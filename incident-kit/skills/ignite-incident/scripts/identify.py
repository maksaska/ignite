#!/usr/bin/env python3
"""Phase 0 of the Ignite incident workflow: classify every file in an incident bundle.

Classification is by CONTENT SIGNATURE, never by filename -- naming varies per site
(ignite.log / ignite-<ts>.log / server.log, gc and safepoint sometimes in one file,
'messages' with no extension). Emits a Markdown inventory for the human/model to read
and an inventory.json for the other scripts in this kit to consume.

Stdlib only. No pip installs.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

HEAD_BYTES = 192 * 1024
TAIL_BYTES = 96 * 1024

# --------------------------------------------------------------------------- #
# Signatures: kind -> [(regex, weight), ...].  Score = sum of weights of hits.
# A file is classified as the highest-scoring kind above MIN_SCORE.
# --------------------------------------------------------------------------- #

SIGNATURES = {
    "ignite_log": [
        (r">>> ver\. \d+\.\d+\.\d+", 5),
        (r"\[(?:INFO|WARN|ERROR|DEBUG)\s*\]\[", 3),
        (r"\[(?:GridDiscoveryManager|IgniteKernal|TcpDiscoverySpi|Checkpointer|"
         r"GridCachePartitionExchangeManager|FailureProcessor|GridDhtPartitionsExchangeFuture)\]", 4),
        (r"Topology snapshot \[ver=", 4),
        (r"org\.apache\.ignite\.", 1),
    ],
    "gc_log": [
        (r"\]\[info\s*\]\[gc[,\s\]]", 4),
        (r"\]\[gc\s*\]", 2),
        (r"Pause Young|Pause Full|Garbage Collection \(|Pause Init Mark|Concurrent Mark", 3),
        (r"Using (?:G1|Serial|Parallel|Shenandoah|The Z Garbage Collector)", 4),
    ],
    "safepoint_log": [
        (r"\]\[safepoint\s*\]", 5),
        (r'Safepoint "\w+", Time since last:', 4),
        (r"Total time for which application threads were stopped", 4),
    ],
    "dmesg": [
        (r"^\[\s*\d+\.\d{6}\]", 5),
        (r"Call Trace:|kernel BUG|Out of memory: Kill", 2),
        (r"hung_task_timeout_secs|blocked for more than", 2),
    ],
    "syslog": [
        (r"^[A-Z][a-z]{2}\s+\d{1,2} \d{2}:\d{2}:\d{2} \S+ ", 5),
        (r"systemd\[\d+\]:|kernel:", 2),
    ],
    "nmon": [
        (r"^AAA,progname,nmon", 8),
        (r"^AAA,(?:host|date|interval),", 4),
        (r"^ZZZZ,T\d{4},", 3),
        (r"^CPU_ALL,", 2),
    ],
    "thread_dump": [
        (r"Full thread dump", 8),
        (r'^"[^"]+" #\d+ .*(?:prio=|tid=)', 4),
        (r"java\.lang\.Thread\.State: (?:RUNNABLE|BLOCKED|WAITING|TIMED_WAITING)", 4),
    ],
    "config_xml": [
        (r"<beans\b|<\?xml", 3),
        (r"org\.apache\.ignite\.configuration\.IgniteConfiguration", 6),
    ],
    "jvm_flags": [
        (r"^-X(?:mx|ms|X:|log:)", 5),
        (r"^-D[A-Z_]+=", 2),
    ],
    "config_props": [
        (r"^[A-Za-z][\w.]*\s*=\s*\S+", 2),
    ],
}

MIN_SCORE = 6

BINARY_MAGIC = [
    (b"FLR\x00", "jfr"),
    (b"PK\x03\x04", "archive"),
    (b"\x1f\x8b", "gzip"),
    (b"\x7fELF", "binary"),
]

# --------------------------------------------------------------------------- #
# Timestamp grammars, per kind.
# --------------------------------------------------------------------------- #

RE_IGNITE_TS = re.compile(r"\[(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})[,.](\d{3})\]")
RE_JVM_TS = re.compile(r"\[(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})\.(\d{3})([+-]\d{4})\]")
RE_JVM_UPTIME = re.compile(r"\[(\d+\.\d+)s\]")
RE_DMESG_TS = re.compile(r"^\[\s*(\d+\.\d{6})\]", re.M)
RE_SYSLOG_TS = re.compile(r"^([A-Z][a-z]{2}\s+\d{1,2} \d{2}:\d{2}:\d{2})", re.M)
RE_NMON_ZZZZ = re.compile(r"^ZZZZ,T\d{4},(\d{2}:\d{2}:\d{2}),(\d{2}-[A-Z]{3}-\d{4})", re.M)
RE_TD_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

# --------------------------------------------------------------------------- #
# Detail extractors.
# --------------------------------------------------------------------------- #

RE_IGNITE_VER = re.compile(r">>> ver\. (\S+)")
RE_COLLECTOR = [
    (re.compile(r"Using The Z Garbage Collector|ZMarkStart|Initializing The Z Garbage Collector"), "ZGC"),
    (re.compile(r"Using Shenandoah|Pause Init Mark|Concurrent evacuation"), "Shenandoah"),
    (re.compile(r"Using G1|G1 Evacuation Pause|G1 Humongous Allocation"), "G1"),
    (re.compile(r"Using Parallel"), "ParallelGC"),
    (re.compile(r"Using Serial"), "SerialGC"),
]
RE_JDK_VER = re.compile(r"Version: (\d+[\w.+-]*)|OpenJDK 64-Bit Server VM \(([\d.+\w-]+)")
RE_HEAP_MAX = re.compile(r"Heap Max Capacity: (\S+)|Max Capacity: (\S+)")
RE_NMON_HOST = re.compile(r"^AAA,host,(\S+)", re.M)
RE_SYSLOG_HOST = re.compile(r"^[A-Z][a-z]{2}\s+\d{1,2} \d{2}:\d{2}:\d{2} (\S+) ", re.M)
RE_LOCAL_ADDR = re.compile(r"Local node addresses: \[([^\]]+)\]")
RE_LOCAL_NODE = re.compile(r"Local node \[ID=([0-9a-fA-F-]+), order=(\d+)")
RE_CONSISTENT_ID = re.compile(r"consistentId=([\w.-]+)")


def read_head_tail(path):
    """Return (head_text, tail_text, magic_kind or None)."""
    size = path.stat().st_size
    with path.open("rb") as fh:
        head = fh.read(HEAD_BYTES)
        magic = None
        for sig, kind in BINARY_MAGIC:
            if head.startswith(sig):
                magic = kind
                break
        if size > HEAD_BYTES + TAIL_BYTES:
            fh.seek(-TAIL_BYTES, os.SEEK_END)
            tail = fh.read(TAIL_BYTES)
        else:
            tail = b""
    return (head.decode("utf-8", errors="replace"),
            tail.decode("utf-8", errors="replace"),
            magic)


def score_kinds(text):
    scores = {}
    for kind, pats in SIGNATURES.items():
        total = 0
        for pat, weight in pats:
            if re.search(pat, text, re.M):
                total += weight
        if total:
            scores[kind] = total
    return scores


def classify(head, tail, extra_sigs=None):
    text = head + "\n" + tail
    scores = score_kinds(text)
    if extra_sigs:
        for kind, pats in extra_sigs.items():
            total = sum(w for p, w in pats if re.search(p, text, re.M))
            if total:
                scores[kind] = scores.get(kind, 0) + total

    # gc + safepoint in one file is common; report it as its own kind.
    has_gc = scores.get("gc_log", 0) >= MIN_SCORE
    has_sp = scores.get("safepoint_log", 0) >= MIN_SCORE
    if has_gc and has_sp:
        return "gc_safepoint_merged", scores

    if not scores:
        return "unknown", scores
    kind = max(scores, key=scores.get)
    if scores[kind] < MIN_SCORE:
        return "unknown", scores
    return kind, scores


def time_range(head, tail, kind):
    """Return dict with first/last timestamp, timezone and clock domain."""
    text_head = head
    text_tail = tail or head
    out = {"first": None, "last": None, "tz": None, "clock": "unknown"}

    if kind == "ignite_log":
        f = RE_IGNITE_TS.search(text_head)
        alls = RE_IGNITE_TS.findall(text_tail)
        if f:
            out["first"] = "%s %s.%s" % (f.group(1), f.group(2), f.group(3))
        if alls:
            g = alls[-1]
            out["last"] = "%s %s.%s" % (g[0], g[1], g[2])
        out["clock"] = "wall clock, JVM default timezone (NOT printed - confirm from OS/config)"
    elif kind in ("gc_log", "safepoint_log", "gc_safepoint_merged"):
        f = RE_JVM_TS.search(text_head)
        alls = RE_JVM_TS.findall(text_tail)
        if f:
            out["first"] = "%s %s.%s" % (f.group(1), f.group(2), f.group(3))
            out["tz"] = f.group(4)
        if alls:
            g = alls[-1]
            out["last"] = "%s %s.%s" % (g[0], g[1], g[2])
            out["tz"] = out["tz"] or g[3]
        out["clock"] = "wall clock with explicit UTC offset"
        if not f:
            if RE_JVM_UPTIME.search(text_head):
                out["clock"] = "uptime only - no 'time' decorator; correlate via JVM start time"
    elif kind == "dmesg":
        f = RE_DMESG_TS.search(text_head)
        alls = RE_DMESG_TS.findall(text_tail)
        if f:
            out["first"] = "+%ss since boot" % f.group(1)
        if alls:
            out["last"] = "+%ss since boot" % alls[-1]
        out["clock"] = ("MONOTONIC since boot - no wall clock. Must be aligned via `messages`/journal "
                        "or boot time before it can be compared with anything else.")
    elif kind == "syslog":
        f = RE_SYSLOG_TS.search(text_head)
        alls = RE_SYSLOG_TS.findall(text_tail)
        if f:
            out["first"] = f.group(1)
        if alls:
            out["last"] = alls[-1]
        out["clock"] = "wall clock, NO YEAR and NO timezone in format - infer year from the bundle"
    elif kind == "nmon":
        alls = RE_NMON_ZZZZ.findall(head + tail)
        if alls:
            out["first"] = "%s %s" % (alls[0][1], alls[0][0])
            out["last"] = "%s %s" % (alls[-1][1], alls[-1][0])
        out["clock"] = "wall clock, local time of the collecting host"
    elif kind == "thread_dump":
        f = RE_TD_TS.search(text_head)
        if f:
            out["first"] = f.group(1)
            out["last"] = f.group(1)
        out["clock"] = "single point in time"
    return out


def details(head, tail, kind):
    text = head + "\n" + (tail or "")
    d = {}
    if kind == "ignite_log":
        m = RE_IGNITE_VER.search(text)
        if m:
            d["ignite_version"] = m.group(1)
        m = RE_LOCAL_NODE.search(text)
        if m:
            d["local_node_id"] = m.group(1)
            d["local_node_order"] = m.group(2)
        m = RE_LOCAL_ADDR.search(text)
        if m:
            d["local_addresses"] = m.group(1)
        ids = set(RE_CONSISTENT_ID.findall(text))
        if ids:
            d["consistent_ids_seen"] = sorted(ids)
        for key, pat in (("failureDetectionTimeout", r"failureDetectionTimeout=(\d+)"),
                         ("clientFailureDetectionTimeout", r"clientFailureDetectionTimeout=(\d+)")):
            m = re.search(pat, text)
            if m:
                d[key] = m.group(1)
    elif kind in ("gc_log", "safepoint_log", "gc_safepoint_merged"):
        for rx, name in RE_COLLECTOR:
            if rx.search(text):
                d["collector"] = name
                break
        m = RE_JDK_VER.search(text)
        if m:
            d["jdk"] = m.group(1) or m.group(2)
        m = RE_HEAP_MAX.search(text)
        if m:
            d["heap_max"] = m.group(1) or m.group(2)
        if kind in ("safepoint_log", "gc_safepoint_merged"):
            if re.search(r'Safepoint "', text):
                d["safepoint_format"] = "jdk17"
            elif "Total time for which application threads were stopped" in text:
                d["safepoint_format"] = "jdk11"
    elif kind == "nmon":
        m = RE_NMON_HOST.search(text)
        if m:
            d["host"] = m.group(1)
    elif kind == "syslog":
        m = RE_SYSLOG_HOST.search(text)
        if m:
            d["host"] = m.group(1)
    elif kind == "thread_dump":
        m = RE_JDK_VER.search(text)
        if m:
            d["jdk"] = m.group(1) or m.group(2)
        d["thread_count"] = len(re.findall(r'^"', text, re.M))
    return d


def node_of(path, root, det):
    """Best-effort node attribution: explicit host detail, else first path segment."""
    if det.get("host"):
        return det["host"]
    rel = path.relative_to(root)
    if len(rel.parts) > 1:
        return rel.parts[0]
    return "(bundle root)"


def human(n):
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return "%dB" % size if unit == "B" else "%.1f%s" % (size, unit)
        size /= 1024.0
    return "%.1fTB" % size


def walk(root, extra_sigs):
    results = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        size = path.stat().st_size
        rel = str(path.relative_to(root)).replace("\\", "/")
        if size == 0:
            results.append({"path": rel, "kind": "empty", "size": 0, "size_h": "0B",
                            "scores": {}, "time": {}, "details": {}, "node": node_of(path, root, {})})
            continue
        head, tail, magic = read_head_tail(path)
        if magic:
            kind = magic
            scores = {}
            det = {"note": "binary; not text-classified"}
            tr = {"clock": "n/a"}
            if magic == "jfr":
                det = {"note": "JDK Flight Recorder file - use jfr_query.py, never cat it"}
        else:
            kind, scores = classify(head, tail, extra_sigs)
            det = details(head, tail, kind)
            tr = time_range(head, tail, kind)
        results.append({
            "path": rel,
            "kind": kind,
            "size": size,
            "size_h": human(size),
            "scores": scores,
            "time": tr,
            "details": det,
            "node": node_of(path, root, det),
        })
    return results


KIND_HELP = {
    "ignite_log": "Ignite server/client node log. Phase 1 input (ignite_timeline.py).",
    "gc_log": "JVM unified GC log. Phase 2 input (gc_digest.py).",
    "safepoint_log": "JVM safepoint log. Phase 2 input (gc_digest.py) - carries time-to-safepoint.",
    "gc_safepoint_merged": "GC and safepoint written to one file. gc_digest.py handles both.",
    "dmesg": "Kernel ring buffer. Phase 2 input (os_digest.py). MONOTONIC clock - align first.",
    "syslog": "syslog/messages. Phase 2 input (os_digest.py).",
    "nmon": "nmon capture. Phase 2 input (nmon_digest.py).",
    "thread_dump": "jstack output. Phase 2 input (threaddump_digest.py).",
    "jfr": "Flight Recorder. Phase 4 ONLY, and only with a written question (jfr_query.py).",
    "config_xml": "Ignite Spring config. Read directly; small.",
    "jvm_flags": "JVM command line / flags dump. Read directly; small.",
    "config_props": "Properties file. Read directly; small.",
    "unknown": "UNCLASSIFIED - follow the manual routine in the Phase 0 gate before continuing.",
    "empty": "Zero bytes. Note it and move on (an empty log is itself evidence).",
    "archive": "Compressed archive - expand it, then re-run identify.py.",
    "gzip": "Gzipped - expand it (zcat), then re-run identify.py.",
    "binary": "Binary blob - not analysable by this kit.",
}


def render_markdown(root, results, out):
    by_node = {}
    for r in results:
        by_node.setdefault(r["node"], []).append(r)

    w = out.write
    w("# 00 - Inventory (Phase 0)\n\n")
    w("Bundle root: `%s`\n\n" % root)
    w("Files: %d  |  Nodes detected: %d\n\n" % (len(results), len(by_node)))

    unknown = [r for r in results if r["kind"] == "unknown"]
    w("## Gate status\n\n")
    if unknown:
        w("**BLOCKED - %d file(s) unclassified.** Do not start Phase 1. "
          "Follow the manual classification routine (references/00-workflow.md, Phase 0 gate) "
          "for each file below, record the signature in `signatures.local.json`, and re-run.\n\n" % len(unknown))
        for r in unknown:
            guesses = ", ".join("%s=%s" % (k, v) for k, v in
                                sorted(r["scores"].items(), key=lambda x: -x[1]))
            w("- `%s` (%s) - best guesses: %s\n" % (r["path"], r["size_h"], guesses or "no signal"))
        w("\n")
    else:
        w("All files classified. Fill in the incident window below, then Phase 1 may start.\n\n")

    w("## Incident window\n\n")
    w("| field | value |\n|---|---|\n")
    w("| First symptom (from Ignite logs) | _fill in during Phase 1_ |\n")
    w("| Analysis window start | _fill in_ |\n")
    w("| Analysis window end | _fill in_ |\n")
    w("| Reference timezone chosen | _fill in - every later timestamp is normalised to this_ |\n\n")

    w("## Clock domains present\n\n")
    w("Different artifacts use different clocks. Alignment error is the most common cause of a "
      "wrong conclusion; record the offsets here before Phase 3.\n\n")
    w("| kind | clock |\n|---|---|\n")
    seen = set()
    for r in results:
        c = r["time"].get("clock")
        if c and c not in ("unknown", "n/a") and (r["kind"], c) not in seen:
            seen.add((r["kind"], c))
            w("| %s | %s |\n" % (r["kind"], c))
    w("\n")

    for node in sorted(by_node):
        w("## Node: %s\n\n" % node)
        w("| file | kind | size | first | last | detail |\n|---|---|---|---|---|---|\n")
        for r in sorted(by_node[node], key=lambda x: x["path"]):
            det = ", ".join("%s=%s" % (k, v) for k, v in r["details"].items()
                            if k != "note" and not isinstance(v, list))
            w("| `%s` | %s | %s | %s | %s | %s |\n" % (
                r["path"], r["kind"], r["size_h"],
                r["time"].get("first") or "-", r["time"].get("last") or "-", det or "-"))
        w("\n")

    w("## What each kind feeds\n\n")
    for kind in sorted({r["kind"] for r in results}):
        w("- **%s** - %s\n" % (kind, KIND_HELP.get(kind, "no guidance recorded")))
    w("\n")

    versions = {r["details"].get("ignite_version") for r in results if r["details"].get("ignite_version")}
    jdks = {r["details"].get("jdk") for r in results if r["details"].get("jdk")}
    colls = {r["details"].get("collector") for r in results if r["details"].get("collector")}
    w("## Environment\n\n")
    w("- Ignite version(s): %s\n" % (", ".join(sorted(versions)) or "NOT FOUND - find it before Phase 4"))
    w("- JDK: %s\n" % (", ".join(sorted(jdks)) or "not found"))
    w("- Collector(s): %s\n" % (", ".join(sorted(colls)) or "not found"))
    if len(versions) > 1:
        w("- **Mixed Ignite versions in one bundle - confirm this is expected before matching "
          "log lines to source.**\n")
    if len(colls) > 1:
        w("- **Different collectors on different nodes - GC digests are not comparable across them.**\n")
    w("\n")


EXPLAIN = """\
identify.py - Phase 0 classifier.

WHAT IT DOES
  Walks an incident bundle and classifies every file by content signature, not by name.
  Emits a Markdown inventory (stdout or --out) and optionally inventory.json (--json)
  which every other script in this kit consumes.

WHY IT EXISTS
  Site naming is not stable: ignite.log / ignite-2024-03-14.log / server.log; gc and
  safepoint sometimes share a file; 'messages' has no extension. Guessing from the name
  is how an analysis starts on the wrong file.

WHAT IT EXTRACTS
  kind, size, first/last timestamp, CLOCK DOMAIN, Ignite version, JDK, GC collector,
  safepoint log format (jdk11 vs jdk17 wording), node attribution.

ASSUMPTIONS
  - Text logs are UTF-8-ish; decoding errors are replaced, not fatal.
  - Only the first 192KB and last 96KB of each file are read. A file whose signature
    appears only in the middle will land in 'unknown' -- that is deliberate, and the
    unknown path tells you what to do next.
  - Node attribution falls back to the first path segment under the bundle root.

EXIT CODES
  0 = all files classified.  2 = at least one 'unknown' (Phase 0 gate is CLOSED).
"""


def main():
    ap = argparse.ArgumentParser(description="Classify files in an Ignite incident bundle.")
    ap.add_argument("root", nargs="?", help="incident bundle directory")
    ap.add_argument("--out", help="write Markdown here instead of stdout")
    ap.add_argument("--json", dest="json_out", help="also write machine-readable inventory here")
    ap.add_argument("--signatures", help="extra signatures JSON (site-local additions)")
    ap.add_argument("--explain", action="store_true", help="print what this script does and assumes")
    args = ap.parse_args()

    if args.explain:
        print(EXPLAIN)
        return 0
    if not args.root:
        ap.error("root directory is required (or pass --explain)")

    root = Path(args.root).resolve()
    if not root.is_dir():
        print("error: %s is not a directory" % root, file=sys.stderr)
        return 1

    extra = None
    if args.signatures and Path(args.signatures).exists():
        raw = json.loads(Path(args.signatures).read_text(encoding="utf-8"))
        extra = {k: [(p, int(w)) for p, w in v] for k, v in raw.items()}

    results = walk(root, extra)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            render_markdown(root, results, fh)
        print("wrote %s" % args.out)
    else:
        render_markdown(root, results, sys.stdout)

    if args.json_out:
        payload = {"root": str(root), "files": results}
        Path(args.json_out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print("wrote %s" % args.json_out, file=sys.stderr)

    return 2 if any(r["kind"] == "unknown" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
