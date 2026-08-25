# Ignite incident analysis kit

A portable kit for analysing Apache Ignite production incidents with an LLM CLI, built so
that the model does as little exploring and as little raw-log reading as possible.

It exists because the interesting work - knowing where Ignite's diagnostic messages come
from, what they mean, and in what order to look at things - can be done **once, in
advance**, and handed to the analysing model as lookup tables and a fixed procedure. What
is left for the model is judgement, which is what you actually want it spending its
context on.

## What's in here

```
incident-kit/
  skills/ignite-incident/
    SKILL.md            the six-phase procedure with hard gates
    references/         nine documents: failure mechanics, evidence limits, antipatterns
    scripts/            digest scripts (python3, stdlib only, no pip installs)
    samples/            synthetic fixtures - no real incident data
  indexes/<version>/
    messages.tsv        ~12k Ignite log literals -> file:line     (built here, committed)
    sysprops.tsv        209 IGNITE_* system properties
    timeouts.tsv        timeout/threshold constants and setters
    repo-map.md         subsystem -> package map
  tools/build_index.py  regenerates the indexes from an Ignite checkout
  templates/workspace/  the analysis skeleton copied next to each incident
```

## The three ideas it is built on

**1. The model must not explore the repository.** Every "find where this message comes
from" is answered by `grep` against `messages.tsv`, which maps a log literal to an exact
`file:line`. Building it takes ~8 seconds and produces a 2 MB table; searching it costs
nothing. Without it, the same question costs thousands of tokens and often finds the
wrong overload.

**2. The model must not read raw logs.** Each artifact type has a digest script that
reduces gigabytes to a bounded table - histograms, top-N, worst windows - with `file:line`
references so anything interesting can be read narrowly. Reading raw logs is how an
analysis runs out of context before it starts.

**3. The procedure is gated, not suggested.** Six phases, each of which must produce its
findings file before the next may start. The findings files are the model's memory, so a
small context window stops being the limiting factor. JFR and Ignite source are locked
behind a written question, because "let me look and see if anything stands out" is where
the budget goes.

## Phases

| phase | question | output |
|---|---|---|
| 0 | What do I have, and on which clocks? | `00-inventory.md` |
| 1 | What did the cluster do? | `10-cluster-timeline.md` |
| 2 | What was the machine doing? | `20-resource-findings.md` |
| 3 | What could explain both? | `30-hypotheses.md` |
| 4 | Targeted JFR / source lookup | `40-source-evidence.md` |
| 5 | Report | `50-report.md` |

## Quick start

```sh
# 1. install the skill into your CLI - see INSTALL.md

# 2. verify the kit works on this machine
python skills/ignite-incident/scripts/selftest.py

# 3. build indexes for the version you are analysing
python tools/build_index.py --repo /path/to/ignite --version 2.16.0 --out indexes

# 4. set up a workspace next to the incident data
mkdir -p /path/to/incident/analysis
cp -r templates/workspace/. /path/to/incident/analysis/

# 5. point the CLI at the incident directory and say:
#    "Run the ignite-incident skill, Phase 0."
```

Then review each phase's findings file before letting it continue. That review is where a
weaker model gets caught going astray, and each file is short enough to actually read.

## Building the indexes

Do this on a machine that has the Ignite sources, once per release you analyse against.
Check out the matching release branch first - log wording and line numbers drift between
versions, which is why the indexes are version-pinned.

```sh
git -C /path/to/ignite checkout 2.16.0
python tools/build_index.py --repo /path/to/ignite --version 2.16.0 --out indexes
```

Commit the result so the analysing machine never has to. For a fork with private plugins,
run it against the fork's tree; messages that resolve to nothing are probably plugin code,
and the skill tells the model to say so rather than force a match.

## Scripts

All are `python3`, stdlib only, and all support `--explain`, which prints what the script
does, what it assumes, and how to read its output.

| script | phase | what it produces |
|---|---|---|
| `identify.py` | 0 | classifies every file by content signature; detects collector, JDK, safepoint format, clock domain |
| `ignite_timeline.py` | 1 | cross-node event timeline, log gaps, topology history, checkpoint timings |
| `gc_digest.py` | 2 | TTSP vs at-safepoint split, pause histogram, worst stop windows |
| `os_digest.py` | 2 | OOM, reclaim stalls, hung tasks with call traces, NIC/storage/clock events |
| `nmon_digest.py` | 2 | CPU/memory/swap/disk/network series with threshold flags |
| `threaddump_digest.py` | 2 | thread states, monitor contention and lock owners, pool saturation |
| `correlate.py` | 3 | one time-aligned table across all sources |
| `jfr_query.py` | 4 | gated JFR access, reduced to frame histograms |
| `selftest.py` | - | 63 assertions against the fixtures |

## Testing

`selftest.py` runs every script against `skills/ignite-incident/samples/` and asserts the
digests reach the right conclusions - that the TTSP split is 97% on the fixture, that the
lock owner is the checkpoint thread, that a timestamped filename still classifies by
content, and so on. Run it after any parser change, and after installing on a new machine.

The fixtures are synthetic and contain no real data. They encode one complete incident:
a slow checkpoint drives memory pressure, memory pressure produces a 21-second
time-to-safepoint stall, the stall stops discovery heartbeats, the coordinator evicts the
node, and the node halts itself on segmentation.

## Scope and limits

- **JDK 11+ unified logging only.** JDK 8 `-XX:+PrintGCDetails` is not parsed; the
  scripts say so rather than producing wrong numbers.
- **Apache Ignite 2.x.** Class names are stable across 2.x; line numbers are not, which
  is why indexes are per-version.
- The digests match a catalogue of known patterns. An incident that does not fit produces
  a thin digest - which the skill treats as a signal to look manually, not to force the
  nearest category.
