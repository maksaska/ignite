#!/usr/bin/env python3
"""Build the lookup indexes that stop the analysing model exploring source trees.

Run once per analysis, against the repositories the human has checked out at the right
branches. Output goes into the analysis workspace, so each incident carries the index it
was actually analysed with.

    python build_index.py --out analysis/indexes \\
        --repo ~/src/ignite --repo ~/src/ignite-extensions --repo ~/src/private

Several repositories are indexed into ONE merged set of tables with a `repo` column, so a
single grep still answers "where does this message come from". The corporate product is
Ignite plus extensions plus private code; a message that only exists in the private tree
would otherwise resolve to nothing.

Produces, under --out:
    messages.tsv   repo, log/exception literal -> file:line
    sysprops.tsv   IGNITE_* system properties
    timeouts.tsv   timeout/threshold constants and setters
    repo-map.md    subsystem -> package map, per repo
    INDEX-INFO.md  provenance: branch, SHA, dirty flag, counts, build time

Stdlib only.
"""

import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Source-root discovery
# --------------------------------------------------------------------------- #

SKIP_DIRS = {".git", "target", "build", "out", "node_modules", ".idea", ".gradle",
             "bin", "classes", "generated-sources", ".mvn", "test", "tests"}
MAX_DEPTH = 8


def find_source_roots(repo, explicit=None):
    """Locate every `src/main/java` under the repo.

    Auto-discovery rather than a hardcoded `modules/` keeps this working across Apache
    Ignite, ignite-extensions and an arbitrary private Maven/Gradle tree.
    """
    if explicit:
        roots = [repo / r for r in explicit]
        return [r for r in roots if r.is_dir()]
    roots = []
    base_depth = len(repo.parts)
    for dirpath, dirnames, _files in os.walk(repo):
        d = Path(dirpath)
        if len(d.parts) - base_depth > MAX_DEPTH:
            dirnames[:] = []
            continue
        dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS and not x.startswith(".")]
        if d.name == "java" and d.parent.name == "main" and d.parent.parent.name == "src":
            roots.append(d)
            dirnames[:] = []      # do not descend into a source root
    return roots


# --------------------------------------------------------------------------- #
# messages.tsv
# --------------------------------------------------------------------------- #

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

MIN_LITERAL_LEN = 12
SCAN_WINDOW = 4000


def first_literal(src, start):
    """From just after an open paren, return the first Java string literal, following
    `+` concatenation across newlines. Returns (literal, ok)."""
    i = start
    end = min(len(src), start + SCAN_WINDOW)
    parts = []
    while i < end:
        ch = src[i]
        if ch == '"':
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


def line_of(starts, pos):
    lo, hi = 0, len(starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if starts[mid] <= pos:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1


def esc(s):
    return str(s).replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "")


def scan_messages(repo, label, roots):
    rows = []
    files = 0
    for root in roots:
        for path in root.rglob("*.java"):
            try:
                src = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            files += 1
            if '"' not in src:
                continue
            starts = None
            for m in CALL_RE.finditer(src):
                lit, ok = first_literal(src, m.end())
                if not ok or len(lit) < MIN_LITERAL_LEN:
                    continue
                if starts is None:
                    starts = [0] + [i + 1 for i, ch in enumerate(src) if ch == "\n"]
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
                rows.append((label, lit, kind, rel, line_of(starts, m.start())))
    return rows, files


# --------------------------------------------------------------------------- #
# sysprops.tsv / timeouts.tsv
# --------------------------------------------------------------------------- #

SYSPROP_ANNO = re.compile(
    r"@SystemProperty\(\s*value\s*=\s*((?:\"(?:[^\"\\]|\\.)*\"\s*\+?\s*)+)"
    r"(?:,\s*type\s*=\s*(\w+)\.class)?(?:,\s*defaults\s*=\s*([^)]*?))?\s*\)", re.S)

TIMEOUT_CONST = re.compile(
    r"public\s+static\s+final\s+(?:long|int)\s+(DFLT_\w*(?:TIMEOUT|FREQ|FREQUENCY|INTERVAL|"
    r"THRESHOLD|RETRIES|RECONNECT_CNT|SIZE)\w*)\s*=\s*([^;]+);")
TIMEOUT_SETTER = re.compile(
    r"public\s+\w[\w<>\[\], ]*\s+(set\w*(?:Timeout|Frequency|Interval|Threshold|RetryCount|"
    r"ReconnectCount)\w*)\s*\(")

TIMEOUT_BASENAMES = {
    "IgniteConfiguration.java", "TcpDiscoverySpi.java", "TcpCommunicationSpi.java",
    "DataStorageConfiguration.java", "TransactionConfiguration.java",
    "DataRegionConfiguration.java", "FailureHandler.java", "ClientConfiguration.java",
}


def scan_sysprops(repo, label, roots):
    rows = []
    for root in roots:
        for path in root.rglob("IgniteSystemProperties.java"):
            src = path.read_text(encoding="utf-8", errors="replace")
            lines = src.split("\n")
            rel = str(path.relative_to(repo)).replace("\\", "/")
            for i, line in enumerate(lines, 1):
                m = re.search(r"public\s+static\s+final\s+String\s+(IGNITE_\w+)\s*=", line)
                if not m:
                    continue
                val = re.search(r'"([^"]+)"', line)
                if not val and i < len(lines):
                    val = re.search(r'"([^"]+)"', lines[i])
                desc = typ = dflt = ""
                back = "\n".join(lines[max(0, i - 12):i - 1])
                a = SYSPROP_ANNO.search(back)
                if a:
                    desc = " ".join(re.findall(r'"((?:[^"\\]|\\.)*)"', a.group(1)))
                    typ = a.group(2) or ""
                    dflt = (a.group(3) or "").strip().replace("\n", " ")
                rows.append((label, m.group(1), val.group(1) if val else m.group(1),
                             typ, dflt, desc, rel, i))
    return rows


def scan_timeouts(repo, label, roots):
    rows = []
    for root in roots:
        for path in root.rglob("*.java"):
            if path.name not in TIMEOUT_BASENAMES:
                continue
            src = path.read_text(encoding="utf-8", errors="replace")
            rel = str(path.relative_to(repo)).replace("\\", "/")
            for i, line in enumerate(src.split("\n"), 1):
                m = TIMEOUT_CONST.search(line)
                if m:
                    rows.append((label, m.group(1), "constant", m.group(2).strip(), rel, i))
                    continue
                m = TIMEOUT_SETTER.search(line)
                if m:
                    rows.append((label, m.group(1), "setter", "", rel, i))
    return rows


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #

def git(repo, *args):
    if not shutil.which("git"):
        return None
    try:
        out = subprocess.run(["git", "-C", str(repo)] + list(args),
                             capture_output=True, text=True, timeout=30, errors="replace")
    except (subprocess.TimeoutExpired, OSError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def provenance(repo):
    """What exactly was indexed. Without this, 'which code was this analysed against'
    is unanswerable weeks later, when the report is being acted on."""
    info = {"path": str(repo)}
    info["sha"] = git(repo, "rev-parse", "HEAD") or "(not a git repo)"
    info["branch"] = git(repo, "rev-parse", "--abbrev-ref", "HEAD") or "?"
    desc = git(repo, "describe", "--tags", "--always")
    info["describe"] = desc or "?"
    status = git(repo, "status", "--porcelain")
    info["dirty"] = bool(status) if status is not None else None
    ver = None
    for pom in ("parent/pom.xml", "pom.xml"):
        p = repo / pom
        if p.is_file():
            m = re.search(r"<revision>([^<]+)</revision>", p.read_text(encoding="utf-8",
                                                                      errors="replace"))
            if m:
                ver = m.group(1).strip()
                break
    info["version"] = ver
    return info


# --------------------------------------------------------------------------- #
# repo-map.md
# --------------------------------------------------------------------------- #

SUBSYSTEMS = [
    ("Discovery / segmentation", "spi/discovery/tcp",
     "TcpDiscoverySpi, ServerImpl (ring worker), ClientImpl. Ring send failures, failure "
     "detection, node drop decisions."),
    ("Discovery manager / topology", "internal/managers/discovery",
     "GridDiscoveryManager: 'Topology snapshot', 'Node FAILED/JOINED/LEFT', "
     "'Local node SEGMENTED', segmentation policy dispatch."),
    ("Communication (data plane)", "spi/communication/tcp",
     "TcpCommunicationSpi: connection pools, NIO server, handshake timeouts."),
    ("Failure handling / worker liveness", "internal/worker",
     "WorkersRegistry: 'Blocked system-critical thread'. Pairs with "
     "internal/processors/failure/FailureProcessor.java."),
    ("JVM pause detection", "internal/LongJVMPauseDetector.java",
     "'Possible too long JVM pause' - wall-clock sampling, NOT a GC measurement."),
    ("Partition map exchange", "distributed/dht/preloader",
     "GridDhtPartitionsExchangeFuture: exchange init/finish, 'Unable to await partitions "
     "release latch', diagnostic dumps."),
    ("Cache exchange manager", "processors/cache/GridCachePartitionExchangeManager.java",
     "'Failed to wait for partition map exchange' and the pending-object dump."),
    ("Persistence / checkpointing", "persistence/checkpoint",
     "Checkpointer: 'Checkpoint started/finished' with phase timings."),
    ("Page memory / write throttling", "persistence/pagemem",
     "PageMemoryImpl and throttling: 'Throttling is applied to page modifications'."),
    ("WAL", "persistence/wal", "WAL managers and segment archiving; fsync stalls."),
    ("Transactions", "processors/cache/transactions",
     "IgniteTxManager: tx timeouts, deadlock detection, long-running transactions."),
    ("Configuration surface", "org/apache/ignite/configuration",
     "IgniteConfiguration, DataStorageConfiguration - defaults to compare against."),
]


def render_repo_map(repos, out):
    w = out.write
    w("# Repo map\n\n")
    w("Generated by `build_index.py`. Use it to jump to a subsystem.\n")
    w("**Do not explore the repositories to rediscover this.** If you are chasing a log\n")
    w("line, use `messages.tsv` instead - it gives the repo, file and line directly.\n\n")
    for label, repo, roots, _info in repos:
        w("## %s\n\n" % label)
        w("Source roots: %s\n\n" % ", ".join(
            "`%s`" % str(r.relative_to(repo)).replace("\\", "/") for r in roots[:8]) or "none")
        found = []
        for name, frag, note in SUBSYSTEMS:
            hits = 0
            for root in roots:
                for p in root.rglob("*"):
                    if frag.replace("/", os.sep) in str(p) or frag in str(p).replace("\\", "/"):
                        hits += 1
                        break
                if hits:
                    break
            if hits:
                found.append((name, frag, note))
        if not found:
            w("_No Ignite subsystems recognised here - this is probably extension or "
              "private code. Use `messages.tsv` to locate anything in it._\n\n")
            continue
        w("| subsystem | path fragment | what lives there |\n|---|---|---|\n")
        for name, frag, note in found:
            w("| %s | `%s` | %s |\n" % (name, frag, note))
        w("\n")
    w("## Using this with messages.tsv\n\n```sh\n")
    w("grep -F 'Unable to await partitions release latch' analysis/indexes/messages.tsv\n")
    w("# -> repo <TAB> literal <TAB> kind <TAB> file <TAB> line\n```\n")


def render_index_info(repos, counts, out):
    w = out.write
    w("# INDEX-INFO\n\n")
    w("What these indexes were built from. **Check this before citing any line number.**\n\n")
    w("Built: %s\n\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    w("| repo | path | branch | describe | SHA | dirty | pom version | .java files |\n")
    w("|---|---|---|---|---|---|---|---|\n")
    for label, repo, _roots, info in repos:
        dirty = "?" if info["dirty"] is None else ("**YES**" if info["dirty"] else "no")
        w("| `%s` | `%s` | %s | %s | `%s` | %s | %s | %d |\n" % (
            label, info["path"], info["branch"], info["describe"], (info["sha"] or "?")[:12],
            dirty, info["version"] or "-", counts.get(label, 0)))
    w("\n")
    if any(r[3]["dirty"] for r in repos if r[3]["dirty"]):
        w("> **A repository has uncommitted changes.** Line numbers from it describe your\n"
          "> working tree, not any released build. Either commit/stash and rebuild, or say\n"
          "> so in the report next to any line you cite from it.\n\n")
    w("## Version check\n\n")
    w("Compare the `pom version` above against the Ignite version `identify.py` found in\n")
    w("the bundle banner (see `00-inventory.md`). If they differ, class names are probably\n")
    w("still right but **line numbers are not** - cite the class and method instead.\n")


# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(description="Build merged Ignite lookup indexes.")
    ap.add_argument("--repo", action="append", default=[], required=False,
                    help="repository to index; repeatable (ignite, ignite-extensions, private)")
    ap.add_argument("--label", action="append", default=[],
                    help="label for the matching --repo (default: directory name)")
    ap.add_argument("--out", help="output directory (analysis/indexes)")
    ap.add_argument("--roots", nargs="*", default=None,
                    help="explicit source roots relative to each repo (default: auto-discover)")
    ap.add_argument("--explain", action="store_true")
    args = ap.parse_args()

    if args.explain:
        print(__doc__)
        return 0
    if not args.repo:
        ap.error("at least one --repo is required")
    if not args.out:
        ap.error("--out is required (or pass --explain)")

    outdir = Path(args.out).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    repos = []
    for i, r in enumerate(args.repo):
        repo = Path(r).expanduser().resolve()
        if not repo.is_dir():
            print("error: --repo %s is not a directory" % r, file=sys.stderr)
            return 1
        label = args.label[i] if i < len(args.label) else repo.name
        roots = find_source_roots(repo, args.roots)
        if not roots:
            print("warning: no src/main/java found under %s - nothing to index from it"
                  % repo, file=sys.stderr)
        repos.append((label, repo, roots, provenance(repo)))

    all_msgs, all_props, all_tmo = [], [], []
    counts = {}
    for label, repo, roots, _info in repos:
        print("indexing %s (%d source root%s) ..."
              % (label, len(roots), "" if len(roots) == 1 else "s"), file=sys.stderr)
        msgs, nfiles = scan_messages(repo, label, roots)
        counts[label] = nfiles
        all_msgs += msgs
        all_props += scan_sysprops(repo, label, roots)
        all_tmo += scan_timeouts(repo, label, roots)

    seen = set()
    uniq = []
    for row in all_msgs:
        key = (row[0], row[1], row[3], row[4])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(row)
    uniq.sort(key=lambda r: (r[1].lower(), r[0], r[3], r[4]))

    with (outdir / "messages.tsv").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("repo\tliteral\tkind\tfile\tline\n")
        for row in uniq:
            fh.write("\t".join(esc(c) for c in row) + "\n")
    with (outdir / "sysprops.tsv").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("repo\tname\tvalue\ttype\tdefault\tdescription\tfile\tline\n")
        for row in all_props:
            fh.write("\t".join(esc(c) for c in row) + "\n")
    with (outdir / "timeouts.tsv").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("repo\tname\tkind\tdefault\tfile\tline\n")
        for row in all_tmo:
            fh.write("\t".join(esc(c) for c in row) + "\n")
    with (outdir / "repo-map.md").open("w", encoding="utf-8", newline="\n") as fh:
        render_repo_map(repos, fh)
    with (outdir / "INDEX-INFO.md").open("w", encoding="utf-8", newline="\n") as fh:
        render_index_info(repos, counts, fh)

    print("messages.tsv: %d  sysprops.tsv: %d  timeouts.tsv: %d  -> %s"
          % (len(uniq), len(all_props), len(all_tmo), outdir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
