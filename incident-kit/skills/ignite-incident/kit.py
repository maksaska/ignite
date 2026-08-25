#!/usr/bin/env python3
"""Driver for the Ignite incident analysis kit.

Two variables in the whole workflow, and every other path is derived from them:

    SKILL=~/.claude/skills/ignite-incident        # where the skill is installed
    BUNDLE=/path/to/incident                      # where the incident data is

    python "$SKILL/kit.py" init "$BUNDLE" --repo ~/src/ignite \\
                                          --repo ~/src/ignite-extensions \\
                                          --repo ~/src/private
    python "$SKILL/kit.py" phase0 "$BUNDLE"
    python "$SKILL/kit.py" preflight "$BUNDLE"
    python "$SKILL/kit.py" phase1 "$BUNDLE"
    ...

`init` creates the analysis workspace and builds the source indexes into it, so each
incident carries the index it was actually analysed with. The skill directory is never
written to and needs nothing outside itself - it does not depend on an Ignite checkout
being present, or on any particular branch being checked out anywhere.

Stdlib only.
"""

import argparse
import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent
SCRIPTS = SKILL / "scripts"
TEMPLATES = SKILL / "templates" / "workspace"

PHASES = ["phase0", "preflight", "phase1", "phase2", "phase3", "phase4", "phase5"]


# --------------------------------------------------------------------------- #

def run(script, *args, **kw):
    """Run one of the kit's scripts. Returns the CompletedProcess."""
    cmd = [sys.executable, str(SCRIPTS / script)] + [str(a) for a in args]
    print("  $ %s" % " ".join([Path(cmd[1]).name] + cmd[2:]), file=sys.stderr)
    return subprocess.run(cmd, errors="replace", **kw)


def analysis_dir(bundle):
    return Path(bundle).expanduser().resolve() / "analysis"


def load_paths(bundle):
    p = analysis_dir(bundle) / "paths.json"
    if not p.is_file():
        raise SystemExit(
            "error: %s not found.\n"
            "       Run `kit.py init %s --repo <path> [--repo <path> ...]` first."
            % (p, bundle))
    return json.loads(p.read_text(encoding="utf-8"))


def gate(text):
    """Print a phase gate. The model is meant to stop here, not roll on."""
    print("\n" + "=" * 76)
    print("STOP HERE - PHASE GATE")
    print("=" * 76)
    print(text.strip())
    print("=" * 76 + "\n")


# --------------------------------------------------------------------------- #

def cmd_init(args):
    bundle = Path(args.bundle).expanduser().resolve()
    if not bundle.is_dir():
        raise SystemExit("error: bundle %s is not a directory" % bundle)
    ana = bundle / "analysis"
    ana.mkdir(exist_ok=True)

    # workspace templates - never clobber findings that already exist
    copied, kept = [], []
    for src in sorted(TEMPLATES.glob("*.md")):
        dst = ana / src.name
        if dst.exists():
            kept.append(dst.name)
        else:
            shutil.copy2(src, dst)
            copied.append(dst.name)
    print("workspace: %d file(s) created%s"
          % (len(copied), (", %d kept as-is" % len(kept)) if kept else ""))

    idx = ana / "indexes"
    if args.skip_index:
        print("indexes: skipped (--skip-index)")
    else:
        if idx.exists():
            shutil.rmtree(idx)          # a stale index is worse than no index
            print("indexes: wiped previous build")
        idx.mkdir(parents=True)
        argv = []
        for r in args.repo:
            argv += ["--repo", str(Path(r).expanduser().resolve())]
        for l in args.label or []:
            argv += ["--label", l]
        rc = run("build_index.py", "--out", idx, *argv).returncode
        if rc != 0:
            raise SystemExit("error: index build failed")

    paths = {
        "skill": str(SKILL),
        "bundle": str(bundle),
        "analysis": str(ana),
        "indexes": str(idx),
        "repos": [str(Path(r).expanduser().resolve()) for r in args.repo],
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    (ana / "paths.json").write_text(json.dumps(paths, indent=2), encoding="utf-8")
    print("paths:  %s" % (ana / "paths.json"))

    if not args.skip_index:
        info = idx / "INDEX-INFO.md"
        if info.is_file():
            print("\n--- INDEX-INFO.md ---")
            print(info.read_text(encoding="utf-8"))

    gate("""
Initialised. Before Phase 0, read the INDEX-INFO table above and confirm with the user:

  - Are these the right repositories, on the right branches, for this incident?
  - Is any of them dirty? Line numbers from a dirty tree describe a working copy,
    not a released build.

Next: `kit.py phase0 <bundle>` - classify every file in the bundle by content.
""")
    return 0


def cmd_phase0(args):
    p = load_paths(args.bundle)
    ana = Path(p["analysis"])
    rc = run("identify.py", p["bundle"],
             "--out", ana / "00-inventory.md",
             "--json", ana / "inventory.json",
             *overlay_args(ana)).returncode
    print("\nwrote %s" % (ana / "00-inventory.md"))
    gate("""
Read 00-inventory.md, then fill in, IN THAT FILE: the incident window, the reference
timezone, and the clock offsets between artifacts.

%s

Then tell the user, in 2-4 sentences: what the bundle contains, which nodes are
represented, the Ignite version and JDK/collector found, and anything unclassified.
Say that the next step is `kit.py preflight <bundle>`, which checks whether the parsers
can actually read these files - no analysis yet. Wait for confirmation.
""" % ("Every file was classified." if rc == 0 else
       "SOME FILES ARE UNCLASSIFIED - resolve them first "
       "(references/90-when-scripts-fail.md)."))
    return 0


def cmd_preflight(args):
    p = load_paths(args.bundle)
    ana = Path(p["analysis"])
    rc = run("preflight.py", "--inventory", ana / "inventory.json",
             "--out", ana / "00.5-preflight.md",
             "--json", ana / "preflight.json",
             "--indexes", ana / "indexes",
             *overlay_args(ana)).returncode
    verdict = {0: "OK - every file parses.",
               2: "DEGRADED - at least one file parsed only partially.",
               1: "FAILED - at least one file could not be read, or is unclassified."}
    gate("""
Preflight verdict: %s

%s

Then tell the user which files (if any) are degraded and what you will therefore NOT
claim from them, and that the next step is `kit.py phase1 <bundle>` - the cross-node
Ignite timeline. Wait for confirmation.
""" % (verdict.get(rc, "unknown"),
       "Proceed to Phase 1." if rc == 0 else
       "Do NOT start Phase 1 yet. Work references/90-when-scripts-fail.md: run the "
       "relevant digest with --diagnose, repair via site-patterns.json in the analysis "
       "directory, then re-run this command AND scripts/selftest.py."))
    return 0


def cmd_phase1(args):
    p = load_paths(args.bundle)
    ana = Path(p["analysis"])
    run("ignite_timeline.py", "--inventory", ana / "inventory.json",
        "--out", ana / "10-cluster-timeline.md",
        "--json", ana / "timeline.json", *overlay_args(ana))
    gate("""
Ignite logs only. Read 10-cluster-timeline.md and write its Narrative section:
the ordered sequence across nodes, who decided what, the earliest anomaly, and what the
Ignite logs cannot tell you.

Consult references/20-ignite-log-anatomy.md and references/30-failure-modes.md.

Then tell the user the story you have so far in 2-4 sentences, name the open questions
that Phase 2 must answer, and say that `kit.py phase2 <bundle>` examines GC/safepoints,
nmon, kernel logs and the thread dump. Wait for confirmation.
""")
    return 0


def cmd_phase2(args):
    p = load_paths(args.bundle)
    ana = Path(p["analysis"])
    inv = ana / "inventory.json"
    ov = overlay_args(ana)

    nodes = gc_nodes(inv)
    if len(nodes) > 1:
        for node in nodes:
            run("gc_digest.py", "--inventory", inv, "--node", node,
                "--out", ana / ("20-gc-%s.md" % node),
                "--json", ana / ("gc-%s.json" % node), *ov)
    else:
        run("gc_digest.py", "--inventory", inv, "--out", ana / "20-gc.md",
            "--json", ana / "gc.json", *ov)
    run("os_digest.py", "--inventory", inv, "--out", ana / "20-os.md",
        "--json", ana / "os.json", *ov)
    run("nmon_digest.py", "--inventory", inv, "--out", ana / "20-nmon.md", *ov)
    run("threaddump_digest.py", "--inventory", inv, "--out", ana / "20-threads.md", *ov)
    gate("""
Read references/40-jvm-and-os.md BEFORE interpreting any of this - especially the
time-to-safepoint versus at-safepoint distinction, which decides whether GC is even
involved.

Write 20-resource-findings.md, ending with an explicit statement:
    Does the resource evidence explain the Phase 1 timeline? yes / no / partly - and why.

Then tell the user that verdict and the two or three numbers behind it, and say that
`kit.py phase3 <bundle>` merges everything into one timeline so hypotheses can be formed.
Wait for confirmation.
""")
    return 0


def cmd_phase3(args):
    p = load_paths(args.bundle)
    ana = Path(p["analysis"])
    extra = []
    if args.offset:
        for o in args.offset:
            extra += ["--offset", o]
    if args.year:
        extra += ["--year", str(args.year)]
    run("correlate.py", "--analysis", ana, "--out", ana / "30-correlated.md", *extra)
    gate("""
Write 30-hypotheses.md. For each hypothesis: statement, what it predicts, evidence for
and against, and the single cheapest check that would discriminate it. Rank them.

Then write the Phase 4 decision EXPLICITLY:
  - no further evidence needed  -> go straight to phase5; this is a good outcome
  - JFR needed                  -> the question, the window to the second, the event types
  - source needed               -> the exact log line, and what you expect the code to show

Tell the user your ranked hypotheses and that decision, and ask them to confirm before
anything expensive is opened. Wait for confirmation.
""")
    return 0


def cmd_phase4(args):
    p = load_paths(args.bundle)
    ana = Path(p["analysis"])
    idx = Path(p["indexes"])
    print("Indexes for this analysis: %s" % idx)
    info = idx / "INDEX-INFO.md"
    if info.is_file():
        print("Provenance: %s" % info)
    print("\nSource lookup (see references/60-source-lookup.md):")
    print("  grep -F '<literal fragment>' %s" % (idx / "messages.tsv"))
    print("  -> repo <TAB> literal <TAB> kind <TAB> file <TAB> line")
    print("\nJFR (see references/50-jfr-playbook.md), only with the Phase 3 question:")
    print("  python %s <recording.jfr> --summary" % (SCRIPTS / "jfr_query.py"))
    print("  python %s <recording.jfr> --events execution --window START END --question '...'"
          % (SCRIPTS / "jfr_query.py"))
    gate("""
This phase runs nothing on its own - it answers only the questions written in Phase 3.
If you find yourself exploring, the question was not sharp enough; go back to Phase 3.

Check INDEX-INFO.md before citing any line number: if the indexed repo is on a different
version than the bundle, or its tree is dirty, cite the class and method instead.

Write 40-source-evidence.md, then tell the user what each lookup established - including
any nil result, which is a real result - and say that `kit.py phase5 <bundle>` produces
the report. Wait for confirmation.
""")
    return 0


def cmd_phase5(args):
    p = load_paths(args.bundle)
    ana = Path(p["analysis"])
    print("Write the report into %s" % (ana / "50-report.md"))
    print("Structure: %s" % (SKILL / "references" / "80-report-template.md"))
    print("Then check it against: %s" % (SKILL / "references" / "70-antipatterns.md"))
    gate("""
The report must contain: the causal chain with a confidence label on every link
(proven / inferred / speculative), a "Ruled out" section, remaining unknowns each with
the check that would settle it, recommendations labelled root cause / mitigation /
detection, and collection gaps - including any file that could not be parsed.

Re-read references/70-antipatterns.md and check the report against every item before
handing it to the user.
""")
    return 0


def cmd_status(args):
    p = load_paths(args.bundle)
    ana = Path(p["analysis"])
    print("bundle:   %s" % p["bundle"])
    print("analysis: %s" % ana)
    print("repos:    %s" % ", ".join(p["repos"]) or "(none)")
    print("indexed:  %s" % p.get("created", "?"))
    print("")
    steps = [
        ("phase0", "00-inventory.md"),
        ("preflight", "00.5-preflight.md"),
        ("phase1", "10-cluster-timeline.md"),
        ("phase2", "20-resource-findings.md"),
        ("phase3", "30-hypotheses.md"),
        ("phase4", "40-source-evidence.md"),
        ("phase5", "50-report.md"),
    ]
    nxt = None
    for cmd, fname in steps:
        print("  [%s] %-10s %s" % ("x" if is_done(ana, fname) else " ", cmd, fname))
        if not is_done(ana, fname) and nxt is None:
            nxt = cmd
    print("\nnext: kit.py %s %s" % (nxt or "(all phases have output)", p["bundle"]))
    return 0


def cmd_selftest(args):
    return run("selftest.py").returncode


def cmd_diagnose(args):
    p = load_paths(args.bundle)
    ana = Path(p["analysis"])
    script = {"ignite": "ignite_timeline.py", "gc": "gc_digest.py", "os": "os_digest.py",
              "nmon": "nmon_digest.py", "threads": "threaddump_digest.py"}[args.what]
    return run(script, "--inventory", ana / "inventory.json", "--diagnose",
               *overlay_args(ana)).returncode


# --------------------------------------------------------------------------- #

def is_done(ana, fname):
    """Has this phase actually produced anything?

    A copied-but-untouched template is NOT done. Getting this wrong would tell the model
    to skip phases whose findings file exists only because `init` created it.
    """
    f = ana / fname
    if not f.is_file() or f.stat().st_size == 0:
        return False
    text = f.read_text(encoding="utf-8", errors="replace")
    tpl = TEMPLATES / fname
    if tpl.is_file() and text.strip() == tpl.read_text(encoding="utf-8",
                                                       errors="replace").strip():
        return False                      # still the pristine template
    return "_write here_" not in text     # generated, but the narrative is unwritten


def overlay_args(ana):
    """Pass the site overlay explicitly if the analyst has created one."""
    ov = ana / "site-patterns.json"
    return ["--patterns", str(ov)] if ov.is_file() else []


def gc_nodes(inv_path):
    """Nodes that have GC/safepoint files, so multi-collector clusters get one digest each."""
    try:
        data = json.loads(Path(inv_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    kinds = ("gc_log", "safepoint_log", "gc_safepoint_merged")
    return sorted({f.get("node") for f in data["files"]
                   if f["kind"] in kinds and f.get("node")})


def main():
    ap = argparse.ArgumentParser(
        description="Driver for the Ignite incident analysis kit.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("init", help="create the workspace and build the indexes")
    p.add_argument("bundle")
    p.add_argument("--repo", action="append", default=[], required=True,
                   help="repository to index; repeatable (ignite, extensions, private)")
    p.add_argument("--label", action="append", default=[],
                   help="label for the matching --repo (default: directory name)")
    p.add_argument("--skip-index", action="store_true",
                   help="do not rebuild indexes (workspace only)")
    p.set_defaults(func=cmd_init)

    for name, fn, helptext in (
            ("phase0", cmd_phase0, "classify every file in the bundle"),
            ("preflight", cmd_preflight, "check the parsers can read those files (Phase 0.5)"),
            ("phase1", cmd_phase1, "cross-node Ignite timeline"),
            ("phase2", cmd_phase2, "GC/safepoint, OS, nmon and thread-dump digests"),
            ("phase3", cmd_phase3, "correlate everything into one timeline"),
            ("phase4", cmd_phase4, "targeted source / JFR lookup (gated)"),
            ("phase5", cmd_phase5, "write the report"),
            ("status", cmd_status, "what has been done, what is next")):
        q = sub.add_parser(name, help=helptext)
        q.add_argument("bundle")
        if name == "phase3":
            q.add_argument("--offset", action="append", default=[],
                           help="clock offset, e.g. --offset gc=-10800")
            q.add_argument("--year", type=int, help="year for syslog stamps")
        q.set_defaults(func=fn)

    q = sub.add_parser("phase05", help="alias for preflight")
    q.add_argument("bundle")
    q.set_defaults(func=cmd_preflight)

    q = sub.add_parser("diagnose", help="why did a parser fail on this bundle")
    q.add_argument("bundle")
    q.add_argument("what", choices=["ignite", "gc", "os", "nmon", "threads"])
    q.set_defaults(func=cmd_diagnose)

    q = sub.add_parser("selftest", help="verify the kit works on this machine")
    q.set_defaults(func=cmd_selftest)

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
