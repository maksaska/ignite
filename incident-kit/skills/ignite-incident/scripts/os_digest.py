#!/usr/bin/env python3
"""Phase 2: digest kernel and system logs (dmesg, syslog/messages, journal exports).

Extracts only the classes of event that bear on an Ignite incident, with counts and
first/last occurrence, so you never page through the raw file.

CLOCK WARNING: dmesg timestamps are seconds since boot (monotonic), not wall clock.
This script reports them as such and, when the same kernel lines also appear in a
syslog-format file, offers the pairing you can use to align them. Never place a dmesg
line on a wall-clock timeline without doing that alignment.

Stdlib only.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import patterns as P                                      # noqa: E402

# (label, severity, regex, why it matters)
PATTERNS = [
    ("oom_kill", 3, re.compile(r"Out of memory: Kill|oom-kill:|Killed process|oom_reaper"),
     "The kernel killed a process. If it was the JVM, the Ignite log simply stops with no "
     "shutdown sequence."),
    ("memory_reclaim", 3, re.compile(
        r"page allocation stalls|page allocation failure|kswapd|direct reclaim|"
        r"allocation stall|OOM killer|low memory"),
     "Memory reclaim pressure. The leading cause of a long time-to-safepoint on a "
     "persistence-enabled node: JVM pages get evicted and must fault back in."),
    ("hung_task", 3, re.compile(r"blocked for more than \d+ seconds|hung_task|task .* state:D"),
     "A task was uninterruptibly blocked, almost always on I/O. Read the call trace: "
     "swap_readpage / folio_wait_bit / io_schedule mean it was waiting on storage or swap."),
    ("swap", 2, re.compile(r"swap_readpage|swapon|swapoff|Adding \d+k swap|swap_writepage"),
     "Swap activity. Any swap involvement on a JVM node is significant."),
    ("net_error", 2, re.compile(
        r"NETDEV WATCHDOG|Link is Down|link down|TX unit hang|transmit queue \d+ timed out|"
        r"NIC Link is|carrier lost|bonding.*(?:down|failure)|ixgbe|e1000e.*(?:Reset|Hang)"),
     "Network interface trouble. Required evidence for any 'it was the network' claim."),
    ("net_drop", 2, re.compile(
        r"Possible SYN flooding|out of memory -- consider tuning|nf_conntrack: table full|"
        r"neighbour table overflow|TCP: too many orphaned|dropping packet|ring buffer overflow"),
     "Packets were dropped by the host. Can produce discovery timeouts without any NIC fault."),
    ("storage_error", 3, re.compile(
        r"I/O error|blk_update_request|EXT4-fs error|XFS \(.*\): |device-mapper: |"
        r"rejecting I/O|SCSI error|task abort|multipath.*(?:fail|switch)|nvme.*(?:reset|timeout)"),
     "Storage errors or resets. Explains checkpoint fsync spikes and WAL stalls."),
    ("clock", 2, re.compile(
        r"Clocksource|clocksource.*(?:unstable|switched)|time jumped|adjusting system clock|"
        r"systemd-timesyncd|ntpd?\[\d+\].*(?:step|slew|offset)|chronyd.*(?:step|slew)"),
     "Clock changes invalidate cross-node timestamp comparison. Check before trusting "
     "any ordering between hosts."),
    ("cpu_throttle", 2, re.compile(
        r"clocking down|thermal|CPU\d+: Core temperature|throttled|cgroup.*cpu.*throttl"),
     "The CPU was throttled - the JVM cannot see this, but it starves safepoint arrival."),
    ("service", 2, re.compile(
        r"(?:ignite|systemd)\S*\[\d+\]:.*(?:Main process exited|Failed with result|"
        r"Scheduled restart|Stopped|Started|Killing process)"),
     "Service lifecycle. Tells you how the process ended and whether something restarted "
     "it - which the Ignite log cannot."),
    ("kernel_panic", 3, re.compile(r"Kernel panic|BUG: unable to handle|general protection fault|"
                                   r"soft lockup|rcu_sched detected stall"),
     "Kernel-level fault. Everything above it in the analysis is a consequence."),
]

RE_DMESG = re.compile(r"^\[\s*(\d+\.\d{6})\]\s*(.*)$")
RE_SYSLOG = re.compile(
    r"^([A-Z][a-z]{2}\s+\d{1,2} \d{2}:\d{2}:\d{2})\s+(\S+)\s+(.*)$")
RE_ISO_SYSLOG = re.compile(
    r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:[+-]\d{2}:?\d{2}|Z)?)\s+(\S+)\s+(.*)$")


def parse(line):
    """Return (stamp, kind, host, text)."""
    m = RE_DMESG.match(line)
    if m:
        return m.group(1), "monotonic", None, m.group(2)
    m = RE_ISO_SYSLOG.match(line)
    if m:
        return m.group(1), "wall", m.group(2), m.group(3)
    m = RE_SYSLOG.match(line)
    if m:
        return m.group(1), "wall", m.group(2), m.group(3)
    return None, "none", None, line


def all_patterns(overlay):
    """Site-local patterns first, then the built-in catalogue."""
    extra = []
    for row in (overlay or {}).get("os_patterns", []):
        label, sev, rx = row[0], int(row[1]), re.compile(row[2])
        why = row[3] if len(row) > 3 else "site-local pattern"
        extra.append((label, sev, rx, why))
    return extra + PATTERNS


def scan(path, findings, ctx, pats=None, health=None):
    pats = pats or PATTERNS
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    for i, raw in enumerate(lines):
        line = raw.rstrip("\n")
        stamp, kind, host, text = parse(line)
        if health is not None and line.strip():
            health.line(kind != "none", i + 1, line)
        if host:
            ctx["hosts"].add(host)
        ctx["clock_kinds"].add(kind)
        if "disables this message" in line:
            continue    # the kernel's own hint line that follows every hung-task report
        for label, sev, rx, _why in pats:
            if rx.search(line):
                if health is not None:
                    health.recognised += 1
                trace = []
                if label in ("hung_task", "kernel_panic"):
                    # capture the following call trace, bounded
                    for j in range(i + 1, min(i + 16, len(lines))):
                        nxt = lines[j].rstrip("\n")
                        _, _, _, ntext = parse(nxt)
                        if re.match(r"^\s*(?:Call Trace:|\s*[\w?+.]+\+0x|\s*\[<)", ntext) or \
                           re.search(r"\+0x[0-9a-f]+", ntext):
                            trace.append(ntext.strip())
                        elif trace:
                            break
                findings[label].append({
                    "file": path.name, "line": i + 1, "stamp": stamp, "clock": kind,
                    "host": host, "text": text.strip()[:220], "trace": trace[:10],
                })
                break


def render(findings, ctx, args, out, healths=None, overlay_path=None, overlay=None):
    w = out.write
    w("# 20 - OS and kernel digest (Phase 2)\n\n")
    w("Files read: %s\n\n" % ", ".join("`%s`" % f for f in ctx["files"]))
    if ctx["hosts"]:
        w("Hosts seen: %s\n\n" % ", ".join(sorted(ctx["hosts"])))

    if healths is not None:
        P.render_health(healths, out, overlay_path, overlay)

    if "monotonic" in ctx["clock_kinds"]:
        w("> **Clock warning.** At least one input uses dmesg's monotonic clock (seconds\n"
          "> since boot). Those stamps are shown as `+Ns`. To place them on the wall-clock\n"
          "> timeline, find a kernel line that appears in BOTH the dmesg file and a\n"
          "> syslog-format file and compute the offset, or use the host's boot time.\n"
          "> Do not put a `+Ns` value on a timeline before doing that.\n\n")

    total = sum(len(v) for v in findings.values())
    if not total:
        # The distinction this whole block exists for: a clean file and an unreadable
        # file both produce zero findings, and only one of them is evidence.
        if healths is not None and not P.absence_is_safe(healths):
            w("## Nothing matched - BUT THE FILES DID NOT PARSE\n\n")
            w("**This is not a negative finding.** Zero matches here means the parser could\n"
              "not read these files, not that the machine was healthy. Do not write that no\n"
              "kernel events were found, and do not use this section to rule anything out.\n\n"
              "Work `references/90-when-scripts-fail.md`, then re-run. See the parse health\n"
              "table above for which files failed and what their lines look like.\n\n")
            return
        w("## Nothing matched\n\n")
        w("No OOM kills, reclaim stalls, hung tasks, network, storage or clock events were\n"
          "found. The files parsed cleanly, so this **is** a real finding: it argues against a\n"
          "machine-level cause, *within the coverage of these files*. Check in\n"
          "`00-inventory.md` that they actually span the incident window before relying on it.\n\n")
        return

    w("## Summary\n\n| signal | count | first | last | severity |\n|---|---|---|---|---|\n")
    for label, sev, _rx, _why in PATTERNS:
        rows = findings.get(label)
        if not rows:
            continue
        w("| %s | %d | %s | %s | %s |\n" % (
            label, len(rows), fmt_stamp(rows[0]), fmt_stamp(rows[-1]),
            "***" if sev == 3 else "**"))
    w("\n")

    for label, sev, _rx, why in PATTERNS:
        rows = findings.get(label)
        if not rows:
            continue
        w("## %s (%d)\n\n" % (label, len(rows)))
        w("_%s_\n\n" % why)
        shown = rows[:args.per_signal]
        for r in shown:
            w("- `%s` %s  _(%s:%d)_\n" % (fmt_stamp(r), r["text"], r["file"], r["line"]))
            for t in r["trace"]:
                w("    - `%s`\n" % t[:160])
        if len(rows) > len(shown):
            w("- _... %d more, see `%s`_\n" % (len(rows) - len(shown), rows[0]["file"]))
        w("\n")

    w("## Phase 2 gate (OS portion)\n\n")
    w("Record in `20-resource-findings.md`:\n\n")
    w("1. Whether any of these events falls inside the incident window (after clock\n"
      "   alignment - state the offset you used).\n")
    w("2. For memory reclaim / hung task findings: do they line up with the longest\n"
      "   time-to-safepoint from `gc_digest.py`? Quote both timestamps.\n")
    w("3. For network findings: which host, which direction, and does the *other* side\n"
      "   show anything? A one-sided NIC error is weaker evidence than a matching pair.\n")
    w("4. If nothing matched, say that explicitly rather than leaving the section empty.\n\n")


def fmt_stamp(r):
    if r["clock"] == "monotonic":
        return "+%ss" % r["stamp"]
    return r["stamp"] or "?"


EXPLAIN = """\
os_digest.py - Phase 2 digest of dmesg / syslog / messages.

WHAT IT DOES
  Scans kernel and system logs for the event classes that bear on an Ignite incident:
  OOM kills, memory reclaim stalls, hung tasks (with their call traces), swap activity,
  NIC and packet-drop errors, storage errors, clock changes, CPU throttling, service
  lifecycle, kernel panics. Reports counts, first/last, and bounded samples.

WHY IT EXISTS
  These files are mostly irrelevant noise with a few decisive lines in them. Reading
  them whole wastes context; grepping ad hoc misses the categories you did not think of.

CLOCK
  dmesg is monotonic-since-boot and is reported as '+Ns'. syslog is wall clock with no
  year and no timezone. The script tells you this every run because putting a dmesg
  timestamp on a wall-clock timeline without aligning it is a recurring error.

ASSUMPTIONS
  - Standard dmesg '[   123.456789] text' or syslog 'Mon DD HH:MM:SS host text' layouts,
    plus ISO-prefixed journal exports.
  - Call traces are captured only for hung tasks and panics, up to 10 frames.

USAGE
  python os_digest.py --inventory inventory.json --out 20-os.md
  python os_digest.py /path/to/dmesg /path/to/messages
"""


def collect_inputs(args):
    paths = []
    if args.inventory:
        data = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
        base = Path(data["root"])
        for f in data["files"]:
            if f["kind"] in ("dmesg", "syslog"):
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
    ap = argparse.ArgumentParser(description="Phase 2: kernel and system log digest.")
    ap.add_argument("targets", nargs="*", default=[])
    ap.add_argument("--inventory")
    ap.add_argument("--node", help="restrict to one node from the inventory")
    ap.add_argument("--out")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--per-signal", type=int, default=8, help="sample lines per signal")
    ap.add_argument("--patterns", help="site-patterns.json overlay")
    ap.add_argument("--diagnose", action="store_true",
                    help="report parse health and unparsed lines instead of the digest")
    ap.add_argument("--explain", action="store_true")
    args = ap.parse_args()

    if args.explain:
        print(EXPLAIN)
        return 0

    paths = collect_inputs(args)
    if not paths:
        print("error: no dmesg/syslog files. Run identify.py and pass --inventory.",
              file=sys.stderr)
        return 1

    try:
        overlay, overlay_path = P.load_overlay(args.patterns, near=args.inventory)
    except P.OverlayError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    pats = all_patterns(overlay)

    findings = defaultdict(list)
    ctx = {"files": [], "hosts": set(), "clock_kinds": set()}
    healths = []
    for p in paths:
        if not p.is_file():
            continue
        ctx["files"].append(p.name)
        health = P.Health(p.name)
        healths.append(health)
        scan(p, findings, ctx, pats, health)

    if args.diagnose:
        P.render_diagnose(healths, sys.stdout, overlay_path, overlay)
        return 0 if P.worst(healths) == P.OK else 2

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            render(findings, ctx, args, fh, healths, overlay_path, overlay)
        print("wrote %s (%d findings)" % (args.out, sum(len(v) for v in findings.values())))
    else:
        render(findings, ctx, args, sys.stdout, healths, overlay_path, overlay)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(
            {k: v for k, v in findings.items()}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
