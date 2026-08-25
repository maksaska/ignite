#!/usr/bin/env python3
"""Run every script in this kit against the bundled synthetic fixtures and assert the
digests say what they should.

Run this after installing the kit on a new machine, and after changing any parser. It
uses only the fixtures in ../samples -- no real incident data is involved.

    python selftest.py
    python selftest.py --keep   # leave the generated output in a temp dir for inspection
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAMPLES = HERE.parent / "samples" / "incident"

PASS, FAIL = "PASS", "FAIL"
results = []


def run(label, argv, cwd=None):
    proc = subprocess.run([sys.executable] + argv, capture_output=True, text=True,
                          cwd=str(cwd) if cwd else None, errors="replace")
    return proc


def check(label, condition, detail=""):
    results.append((PASS if condition else FAIL, label, detail))
    print("%-4s %s%s" % (PASS if condition else FAIL, label,
                         "" if condition else "  <- " + detail))
    return condition


def main():
    ap = argparse.ArgumentParser(description="Self-test the incident kit against fixtures.")
    ap.add_argument("--keep", action="store_true", help="keep the output directory")
    args = ap.parse_args()

    if not SAMPLES.is_dir():
        print("error: fixtures not found at %s" % SAMPLES, file=sys.stderr)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="ignite-kit-selftest-"))
    print("fixtures: %s" % SAMPLES)
    print("output:   %s\n" % tmp)

    inv_json = tmp / "inventory.json"
    inv_md = tmp / "00-inventory.md"

    # ---- identify -------------------------------------------------------- #
    p = run("identify", [str(HERE / "identify.py"), str(SAMPLES),
                         "--out", str(inv_md), "--json", str(inv_json)])
    inv = inv_md.read_text(encoding="utf-8") if inv_md.exists() else ""
    check("identify.py runs", p.returncode in (0, 2), p.stderr[:300])
    check("identify: exit 2 on the unclassified fixture", p.returncode == 2,
          "expected the .dat fixture to be unknown, got exit %d" % p.returncode)
    check("identify: gate reports BLOCKED", "BLOCKED" in inv)
    check("identify: classifies node01's Ignite log",
          re.search(r"`node01/ignite\.log` \| ignite_log \|", inv) is not None)
    check("identify: classifies node03's timestamped Ignite log",
          re.search(r"`node03/ignite-2024-03-14\.log` \| ignite_log \|", inv) is not None,
          "a timestamped filename must still classify by content")
    check("identify: detects G1", "collector=G1" in inv)
    check("identify: detects ZGC", "collector=ZGC" in inv)
    check("identify: detects Shenandoah", "collector=Shenandoah" in inv)
    check("identify: detects merged gc+safepoint file", "gc_safepoint_merged" in inv)
    check("identify: detects jdk11 safepoint wording", "safepoint_format=jdk11" in inv)
    check("identify: detects jdk17 safepoint wording", "safepoint_format=jdk17" in inv)
    check("identify: finds the Ignite version", "2.16.0" in inv)
    check("identify: flags dmesg as monotonic", "MONOTONIC" in inv)
    check("identify: classifies nmon", "| nmon |" in inv)
    check("identify: classifies thread dump", "| thread_dump |" in inv)

    # ---- timeline -------------------------------------------------------- #
    tl_md = tmp / "10-cluster-timeline.md"
    p = run("timeline", [str(HERE / "ignite_timeline.py"), "--inventory", str(inv_json),
                         "--out", str(tl_md), "--json", str(tmp / "timeline.json"),
                         "--gap-seconds", "60"])
    tl = tl_md.read_text(encoding="utf-8") if tl_md.exists() else ""
    check("ignite_timeline.py runs", p.returncode == 0, p.stderr[:300])
    check("timeline: sees both nodes", "node01" in tl and "node03" in tl)
    check("timeline: finds segmentation", "Local node SEGMENTED" in tl)
    check("timeline: finds the peer's Node FAILED", "Node FAILED" in tl)
    check("timeline: finds the JVM pause", "21987" in tl)
    check("timeline: parses the bracketed thread name",
          "| node01 | discovery | ServerImpl |" in tl,
          "logger column empty -> nested-bracket thread names regressed")
    check("timeline: reports the slow checkpoint", "415.4s" in tl)
    check("timeline: topology drops to 3 servers", re.search(r"\| 5 \| 3 \| 0 \|", tl) is not None)
    check("timeline: reports log gaps", "Log gaps" in tl and "1346.6" in tl)

    # ---- gc digest ------------------------------------------------------- #
    gc_md = tmp / "20-gc.md"
    p = run("gc", [str(HERE / "gc_digest.py"), "--inventory", str(inv_json),
                   "--node", "node03", "--out", str(gc_md),
                   "--json", str(tmp / "gc.json")])
    gc = gc_md.read_text(encoding="utf-8") if gc_md.exists() else ""
    check("gc_digest.py runs", p.returncode == 0, p.stderr[:300])
    check("gc: identifies G1", "| Collector | G1 |" in gc)
    check("gc: TTSP dominates the split", "97.1%" in gc,
          "the TTSP/at-safepoint split is wrong")
    check("gc: fires the 'not GC' warning", "READ THIS BEFORE BLAMING GC" in gc)
    check("gc: longest TTSP is 21.4s", "21.469 s" in gc)
    check("gc: worst-window analysis present", "Worst 60s windows" in gc)

    for node, coll in (("node02", "ZGC"), ("node04", "Shenandoah")):
        q = run("gc-" + node, [str(HERE / "gc_digest.py"), "--inventory", str(inv_json),
                               "--node", node])
        check("gc: %s parses (%s)" % (node, coll),
              q.returncode == 0 and ("| Collector | %s |" % coll) in q.stdout,
              q.stderr[:200])

    # ---- os digest ------------------------------------------------------- #
    os_md = tmp / "20-os.md"
    p = run("os", [str(HERE / "os_digest.py"), "--inventory", str(inv_json),
                   "--out", str(os_md), "--json", str(tmp / "os.json")])
    osd = os_md.read_text(encoding="utf-8") if os_md.exists() else ""
    check("os_digest.py runs", p.returncode == 0, p.stderr[:300])
    check("os: finds memory reclaim stalls", "memory_reclaim" in osd)
    check("os: finds the hung task", "hung_task" in osd)
    check("os: captures the swap call trace", "swap_readpage" in osd)
    check("os: warns about the monotonic clock", "Clock warning" in osd)
    check("os: drops the kernel hint line",
          "disables this message" not in osd,
          "the hung_task_timeout_secs hint line is being reported as a finding")

    # ---- nmon ------------------------------------------------------------ #
    nm_md = tmp / "20-nmon.md"
    p = run("nmon", [str(HERE / "nmon_digest.py"), "--inventory", str(inv_json),
                     "--out", str(nm_md)])
    nm = nm_md.read_text(encoding="utf-8") if nm_md.exists() else ""
    check("nmon_digest.py runs", p.returncode == 0, p.stderr[:300])
    check("nmon: states the sample interval", "**60 s**" in nm)
    check("nmon: warns about resolution", "Resolution limit" in nm)
    check("nmon: flags swap consumption", "Swap was consumed" in nm)
    check("nmon: flags disk saturation", "Disk saturated" in nm)
    check("nmon: flags I/O wait", "High I/O wait" in nm)
    check("nmon: parses CPU_ALL columns", "| Idle% |" in nm)

    # ---- thread dump ----------------------------------------------------- #
    td_md = tmp / "20-threads.md"
    p = run("threads", [str(HERE / "threaddump_digest.py"), "--inventory", str(inv_json),
                        "--out", str(td_md)])
    td = td_md.read_text(encoding="utf-8") if td_md.exists() else ""
    check("threaddump_digest.py runs", p.returncode == 0, p.stderr[:300])
    check("threads: finds the lock owner", "db-checkpoint-thread-#79" in td)
    check("threads: counts the two blocked stripes", "**2**" in td)
    check("threads: flags stripe pool saturation", "saturated" in td)
    check("threads: keeps Native Method frames intact", "Native Method" in td,
          "frame parsing truncates at whitespace")
    check("threads: warns it is one instant", "one instant" in td.lower())

    # ---- correlate ------------------------------------------------------- #
    co_md = tmp / "30-correlated.md"
    p = run("correlate", [str(HERE / "correlate.py"), "--analysis", str(tmp),
                          "--out", str(co_md), "--year", "2024"])
    co = co_md.read_text(encoding="utf-8") if co_md.exists() else ""
    check("correlate.py runs", p.returncode == 0, p.stderr[:300])
    check("correlate: merges ignite and jvm sources",
          "| ignite |" in co and "| gc |" in co)
    check("correlate: warns when no offsets were supplied", "No offsets were supplied" in co)
    check("correlate: excludes monotonic dmesg rows", "excluded" in co)

    # ---- --explain on every script --------------------------------------- #
    for script in sorted(HERE.glob("*.py")):
        if script.name == "selftest.py":
            continue
        q = run("explain", [str(script), "--explain"])
        check("%s --explain works" % script.name,
              q.returncode == 0 and len(q.stdout) > 200, q.stderr[:200])

    # ---- summary --------------------------------------------------------- #
    failed = [r for r in results if r[0] == FAIL]
    print("\n%d checks, %d failed" % (len(results), len(failed)))
    if failed:
        print("\nFailures:")
        for _s, label, detail in failed:
            print("  - %s%s" % (label, ("  (%s)" % detail) if detail else ""))

    if args.keep:
        print("\noutput kept in %s" % tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
