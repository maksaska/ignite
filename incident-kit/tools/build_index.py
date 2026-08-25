#!/usr/bin/env python3
"""Build version-pinned lookup indexes over an Ignite source tree.

Run this HERE (on a machine with a strong model / a human), once per release branch you
intend to analyse against. The output is what stops the analysing model from grepping the
whole repository: a log line becomes a TSV lookup, not an exploration.

    python tools/build_index.py --repo /path/to/ignite --version 2.16.0 --out indexes/

Produces, under indexes/<version>/:
    messages.tsv   log/exception string literal  -> file:line
    sysprops.tsv   IGNITE_* system property      -> file:line + description + default
    timeouts.tsv   timeout/threshold constants and setters -> file:line + default
    repo-map.md    subsystem -> package map, with file counts

Stdlib only.
"""

import argparse
import os
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# messages.tsv
# --------------------------------------------------------------------------- #

# Call sites whose first string literal reaches the log.
#
# NOTE: org.apache.ignite.IgniteLogger declares warning()/error()/info()/debug()/trace(),
# NOT the slf4j warn(). Both spellings appear in the tree, so both are matched.
# `String.format(` and `String msg = "..."` are included because a large share of Ignite's
# most diagnostic messages are assembled that way and then handed to the logger; the kind
# column marks them so a reader knows the hit is a message *fragment*.
CALL_RE = re.compile(
    r"\b(?:"
    r"(?P<logger>log|log0|LOG|logger)\s*\.\s*"
    r"(?P<lvl>info|warn|warning|error|severe|debug|trace|config|fine|finer|finest)"
    r"|(?P<util>U|LT)\s*\.\s*(?P<ulvl>info|warn|warning|error|debug|quietAndWarn|quietAndInfo|quiet)"
    r"|new\s+(?P<exc>[A-Z]\w*(?:Exception|Error))"
    r"|(?P<fmt>String\s*\.\s*format)"
    r"|(?:final\s+)?String\s+(?P<var>\w*(?:msg|Msg|MSG|err|Err|ERR|reason|Reason|REASON)\w*)"
    r")\s*[\(=]",
    re.M,
)

MIN_LITERAL_LEN = 12          # shorter literals are noise ("Failed", "[", ...)
SCAN_WINDOW = 4000            # chars to scan forward from the open paren


def first_literal(src, start):
    """From index `start` (just after an open paren) return the first Java string
    literal, following `+` concatenation across newlines. Returns (literal, ok)."""
    i = start
    end = min(len(src), start + SCAN_WINDOW)
    parts = []
    while i < end:
        ch = src[i]
        if ch == '"':
            # consume the literal, honouring backslash escapes
            j = i + 1
            buf = []
            while j < end:
                c = src[j]
                if c == "\\" and j + 1 < end:
                    nxt = src[j + 1]
                    buf.append({"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}.get(nxt, nxt))
                    j += 2
                    continue
                if c == '"':
                    break
                buf.append(c)
                j += 1
            parts.append("".join(buf))
            # look ahead: literal immediately followed by `+` and another literal?
            k = j + 1
            while k < end and src[k] in " \t\r\n":
                k += 1
            if k < end and src[k] == "+":
                k += 1
                while k < end and src[k] in " \t\r\n":
                    k += 1
                if k < end and src[k] == '"':
                    i = k
                    continue
            return "".join(parts), True
        if ch in ");":
            return "", False
        i += 1
    return "", False


def scan_messages(repo, roots):
    rows = []
    for root in roots:
        base = repo / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.java"):
            try:
                src = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if '"' not in src:
                continue
            line_starts = None
            for m in CALL_RE.finditer(src):
                lit, ok = first_literal(src, m.end())
                if not ok or len(lit) < MIN_LITERAL_LEN:
                    continue
                if line_starts is None:
                    line_starts = [0]
                    for idx, ch in enumerate(src):
                        if ch == "\n":
                            line_starts.append(idx + 1)
                line = bisect(line_starts, m.start())
                if m.group("lvl"):
                    kind = "log_" + m.group("lvl")
                elif m.group("ulvl"):
                    kind = "util_" + m.group("ulvl")
                elif m.group("exc"):
                    kind = "exception:" + m.group("exc")
                elif m.group("fmt"):
                    kind = "format"
                else:
                    kind = "assign:" + m.group("var")
                rel = str(path.relative_to(repo)).replace("\\", "/")
                rows.append((lit, kind, rel, line))
    return rows


def bisect(starts, pos):
    lo, hi = 0, len(starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if starts[mid] <= pos:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1


def esc(s):
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "")


# --------------------------------------------------------------------------- #
# sysprops.tsv
# --------------------------------------------------------------------------- #

SYSPROP_DECL = re.compile(
    r"public\s+static\s+final\s+String\s+(IGNITE_\w+)\s*=\s*\n?\s*\"([^\"]+)\"", re.M)
SYSPROP_ANNO = re.compile(
    r"@SystemProperty\(\s*value\s*=\s*((?:\"(?:[^\"\\]|\\.)*\"\s*\+?\s*)+)"
    r"(?:,\s*type\s*=\s*(\w+)\.class)?(?:,\s*defaults\s*=\s*([^)]*?))?\s*\)", re.S)


def scan_sysprops(repo):
    path = repo / "modules/core/src/main/java/org/apache/ignite/IgniteSystemProperties.java"
    rows = []
    if not path.is_file():
        return rows
    src = path.read_text(encoding="utf-8", errors="replace")
    lines = src.split("\n")
    for i, line in enumerate(lines, 1):
        m = re.search(r"public\s+static\s+final\s+String\s+(IGNITE_\w+)\s*=", line)
        if not m:
            continue
        name = m.group(1)
        # value may sit on this line or the next
        val = re.search(r'"([^"]+)"', line)
        if not val and i < len(lines):
            val = re.search(r'"([^"]+)"', lines[i])
        # walk back for the @SystemProperty block / javadoc
        desc, typ, dflt = "", "", ""
        back = "\n".join(lines[max(0, i - 12):i - 1])
        a = SYSPROP_ANNO.search(back)
        if a:
            desc = " ".join(re.findall(r'"((?:[^"\\]|\\.)*)"', a.group(1)))
            typ = a.group(2) or ""
            dflt = (a.group(3) or "").strip().replace("\n", " ")
        rows.append((name, val.group(1) if val else name, typ, dflt, desc,
                     "modules/core/src/main/java/org/apache/ignite/IgniteSystemProperties.java", i))
    return rows


# --------------------------------------------------------------------------- #
# timeouts.tsv
# --------------------------------------------------------------------------- #

TIMEOUT_CONST = re.compile(
    r"public\s+static\s+final\s+(?:long|int)\s+(DFLT_\w*(?:TIMEOUT|FREQ|FREQUENCY|INTERVAL|"
    r"THRESHOLD|RETRIES|RECONNECT_CNT|SIZE)\w*)\s*=\s*([^;]+);")
TIMEOUT_SETTER = re.compile(
    r"public\s+\w[\w<>\[\], ]*\s+(set\w*(?:Timeout|Frequency|Interval|Threshold|RetryCount|"
    r"ReconnectCount)\w*)\s*\(")

TIMEOUT_FILES = [
    "modules/core/src/main/java/org/apache/ignite/configuration/IgniteConfiguration.java",
    "modules/core/src/main/java/org/apache/ignite/spi/discovery/tcp/TcpDiscoverySpi.java",
    "modules/core/src/main/java/org/apache/ignite/spi/communication/tcp/TcpCommunicationSpi.java",
    "modules/core/src/main/java/org/apache/ignite/configuration/DataStorageConfiguration.java",
    "modules/core/src/main/java/org/apache/ignite/configuration/TransactionConfiguration.java",
    "modules/core/src/main/java/org/apache/ignite/failure/FailureHandler.java",
]


def scan_timeouts(repo):
    rows = []
    seen_files = []
    for rel in TIMEOUT_FILES:
        p = repo / rel
        if p.is_file():
            seen_files.append((rel, p))
    # plus anything else declaring failure-detection knobs
    for rel, p in seen_files:
        src = p.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(src.split("\n"), 1):
            m = TIMEOUT_CONST.search(line)
            if m:
                rows.append((m.group(1), "constant", m.group(2).strip(), rel, i))
                continue
            m = TIMEOUT_SETTER.search(line)
            if m:
                rows.append((m.group(1), "setter", "", rel, i))
    return rows


# --------------------------------------------------------------------------- #
# repo-map.md
# --------------------------------------------------------------------------- #

SUBSYSTEMS = [
    ("Discovery / cluster membership, segmentation",
     "modules/core/src/main/java/org/apache/ignite/spi/discovery/tcp",
     "TcpDiscoverySpi, ServerImpl (ring worker), ClientImpl. Ring message send failures, "
     "failure detection, node drop decisions."),
    ("Discovery manager / topology events",
     "modules/core/src/main/java/org/apache/ignite/internal/managers/discovery",
     "GridDiscoveryManager: 'Topology snapshot', 'Node FAILED/JOINED/LEFT', "
     "'Local node SEGMENTED', segmentation policy dispatch."),
    ("Communication (data plane)",
     "modules/core/src/main/java/org/apache/ignite/spi/communication/tcp",
     "TcpCommunicationSpi and internals: connection pools, NIO server, handshake timeouts."),
    ("Failure handling / worker liveness",
     "modules/core/src/main/java/org/apache/ignite/internal/worker",
     "WorkersRegistry: 'Blocked system-critical thread'. Pairs with "
     "internal/processors/failure/FailureProcessor.java and org/apache/ignite/failure/."),
    ("JVM pause detection",
     "modules/core/src/main/java/org/apache/ignite/internal/LongJVMPauseDetector.java",
     "'Possible too long JVM pause' - wall-clock sampling, NOT a GC measurement."),
    ("Partition map exchange (PME)",
     "modules/core/src/main/java/org/apache/ignite/internal/processors/cache/distributed/dht/preloader",
     "GridDhtPartitionsExchangeFuture: exchange init/finish, 'Unable to await partitions "
     "release latch', diagnostic dumps."),
    ("Cache exchange manager",
     "modules/core/src/main/java/org/apache/ignite/internal/processors/cache/GridCachePartitionExchangeManager.java",
     "'Failed to wait for partition map exchange' and the pending-object dump that follows."),
    ("Persistence / checkpointing",
     "modules/core/src/main/java/org/apache/ignite/internal/processors/cache/persistence/checkpoint",
     "Checkpointer: 'Checkpoint started/finished' with phase timings (markDuration, "
     "pagesWrite, fsync, total)."),
    ("Page memory / write throttling",
     "modules/core/src/main/java/org/apache/ignite/internal/processors/cache/persistence/pagemem",
     "PageMemoryImpl and throttling policies: 'Throttling is applied to page modifications'."),
    ("WAL",
     "modules/core/src/main/java/org/apache/ignite/internal/processors/cache/persistence/wal",
     "WAL managers and segment archiving; fsync stalls surface here."),
    ("Transactions",
     "modules/core/src/main/java/org/apache/ignite/internal/processors/cache/transactions",
     "IgniteTxManager and friends: tx timeouts, deadlock detection, 'long running transaction'."),
    ("Configuration surface",
     "modules/core/src/main/java/org/apache/ignite/configuration",
     "IgniteConfiguration, DataStorageConfiguration, DataRegionConfiguration - the defaults "
     "you compare against what the incident config actually set."),
]


def render_repo_map(repo, version, out):
    w = out.write
    w("# Ignite repo map - %s\n\n" % version)
    w("Generated by `tools/build_index.py`. Use this to jump straight to a subsystem.\n")
    w("**Do not explore the repository to rediscover this.** If a log line is what you are\n")
    w("chasing, go through `messages.tsv` instead - it gives you the exact file and line.\n\n")
    w("| subsystem | path | what lives there | .java files |\n|---|---|---|---|\n")
    for name, rel, note in SUBSYSTEMS:
        p = repo / rel
        if p.is_file():
            count = 1
        elif p.is_dir():
            count = sum(1 for _ in p.rglob("*.java"))
        else:
            count = 0
        flag = "" if count else " **(MISSING in this version - check the path)**"
        w("| %s | `%s` | %s%s | %d |\n" % (name, rel, note, flag, count))
    w("\n## Modules present\n\n")
    mods = sorted(p.name for p in (repo / "modules").iterdir()
                  if p.is_dir() and (p / "src").exists()) if (repo / "modules").is_dir() else []
    w(", ".join("`%s`" % m for m in mods) + "\n\n")
    w("## How to use this with messages.tsv\n\n")
    w("```sh\n")
    w("# 1. take a distinctive, non-variable fragment of the log line\n")
    w("grep -F 'Unable to await partitions release latch' indexes/%s/messages.tsv\n" % version)
    w("# 2. that prints: <literal>\\t<kind>\\t<file>\\t<line>\n")
    w("# 3. read ONLY the enclosing method at that file:line\n")
    w("```\n")


# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(description="Build Ignite lookup indexes for the incident kit.")
    ap.add_argument("--repo", required=True, help="path to the Ignite source tree (checked out at the target release)")
    ap.add_argument("--version", required=True, help="version label, e.g. 2.16.0 (becomes the output directory)")
    ap.add_argument("--out", default="indexes", help="output root (default: indexes)")
    ap.add_argument("--roots", nargs="*", default=["modules"],
                    help="source roots to scan under --repo (default: modules)")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print("error: --repo %s is not a directory" % repo, file=sys.stderr)
        return 1
    outdir = Path(args.out).resolve() / args.version
    outdir.mkdir(parents=True, exist_ok=True)

    print("scanning %s ..." % repo, file=sys.stderr)
    msgs = scan_messages(repo, args.roots)
    # de-duplicate identical (literal, file, line)
    seen = set()
    uniq = []
    for lit, kind, rel, line in msgs:
        key = (lit, rel, line)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((lit, kind, rel, line))
    uniq.sort(key=lambda r: (r[0].lower(), r[2], r[3]))
    with (outdir / "messages.tsv").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("literal\tkind\tfile\tline\n")
        for lit, kind, rel, line in uniq:
            fh.write("%s\t%s\t%s\t%d\n" % (esc(lit), kind, rel, line))
    print("messages.tsv: %d entries" % len(uniq), file=sys.stderr)

    props = scan_sysprops(repo)
    with (outdir / "sysprops.tsv").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("name\tvalue\ttype\tdefault\tdescription\tfile\tline\n")
        for row in props:
            fh.write("\t".join(esc(str(c)) for c in row) + "\n")
    print("sysprops.tsv: %d entries" % len(props), file=sys.stderr)

    tmo = scan_timeouts(repo)
    with (outdir / "timeouts.tsv").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("name\tkind\tdefault\tfile\tline\n")
        for row in tmo:
            fh.write("\t".join(esc(str(c)) for c in row) + "\n")
    print("timeouts.tsv: %d entries" % len(tmo), file=sys.stderr)

    with (outdir / "repo-map.md").open("w", encoding="utf-8", newline="\n") as fh:
        render_repo_map(repo, args.version, fh)
    print("repo-map.md written", file=sys.stderr)

    print("indexes for %s -> %s" % (args.version, outdir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
