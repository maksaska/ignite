---
name: ignite-incident
description: Analyse an Apache Ignite production incident from a bundle of logs (ignite logs, GC and safepoint logs, dmesg, messages, nmon, configs, JFR, thread dumps) and find the root cause, using pre-built indexes over the Ignite sources. Use this whenever asked to investigate an Ignite outage, node failure, segmentation, cluster hang, long pause, or "why did this node die".
---

# Ignite incident analysis

You are analysing a production incident. Work through six phases **in order**. Each
phase has a gate: you may not start the next phase until you have written that phase's
findings file. The findings files are your memory - later phases read them instead of
holding everything in context.

## Rules that override your instincts

1. **Never read a raw log file.** They are too big and you will run out of room. Run the
   digest script for that artifact type and read its output. If a digest line needs
   follow-up, it carries `file:line` - read *those* lines only, with a narrow range.
2. **Never grep the Ignite repository to find where a log message comes from.** Look it
   up in `indexes/<version>/messages.tsv`. That file exists so you don't explore.
3. **Never open the JFR recording, and never read Ignite source, before Phase 4** - and
   then only against a written question that names what you expect to find. "Let me look
   at the JFR to see if anything stands out" is not a question.
4. **The node that died is usually not where the cause is visible.** A segmented or
   halted node logs the *consequence*. The *decision* is in the coordinator's log and the
   ring neighbour's log. Always digest all nodes together.
5. **Do not conflate a JVM pause with a GC pause.** See `references/40-jvm-and-os.md`.
   Getting this wrong sends the entire analysis the wrong way.
6. **Timestamps come from different clocks.** Ignite logs the JVM's local time without
   saying which zone; `dmesg` is monotonic since boot; syslog has no year; JFR is epoch.
   Establish the offsets in Phase 0 and never compare across them before you have.
7. **State confidence.** Distinguish what the evidence proves from what you infer. An
   honest "the logs cannot tell us X" is worth more than a confident wrong chain.
8. **A degraded parse is never a finding.** If a digest reports a low parse rate, its
   output is incomplete by an unknown amount. You may not say a signal was absent, that
   nothing was found, or that anything is ruled out, from a file that did not parse. Go to
   `references/90-when-scripts-fail.md`. Reading raw log lines IS allowed there, bounded -
   that is the one place in this workflow where it is the right move.

## Setup (once per incident)

```sh
KIT=<path to incident-kit>
BUNDLE=<path to the incident bundle>
mkdir -p "$BUNDLE/analysis"
cp -r "$KIT/templates/workspace/." "$BUNDLE/analysis/"
```

All scripts are `python3`, stdlib only. Every script accepts `--explain`; run that first
if you are unsure what its output means.

## Phase 0 - Inventory

```sh
python "$KIT/skills/ignite-incident/scripts/identify.py" "$BUNDLE" \
    --out "$BUNDLE/analysis/00-inventory.md" \
    --json "$BUNDLE/analysis/inventory.json"
```

Read `00-inventory.md`. Then fill in, in that file: the incident window and the reference
timezone, and the clock offsets between the artifacts.

**Gate:** every file classified (exit code 0), and the incident window written down.
If any file is `unknown`, work `references/90-when-scripts-fail.md` before continuing. Do
not skip a file because it "looks unimportant".

## Phase 0.5 - Can the parsers actually read it?

```sh
python "$KIT/skills/ignite-incident/scripts/preflight.py" \
    --inventory "$BUNDLE/analysis/inventory.json" \
    --out "$BUNDLE/analysis/00.5-preflight.md" \
    --json "$BUNDLE/analysis/preflight.json"
```

This runs the real parsers over the real files and reports what fraction of each one they
understood, plus a completeness cross-check that catches events being dropped silently.
It performs no analysis.

Exit 0 = OK, 2 = DEGRADED, 1 = FAILED or files still unclassified.

**Gate:** verdict `OK`, **or** every `DEGRADED`/`FAILED` file recorded in
`00-inventory.md` together with what you will therefore not claim from it. On anything
below OK, work `references/90-when-scripts-fail.md` first.

Skipping this phase is how an analysis ends up confidently describing a cluster from a
digest that read a third of the evidence.

## Phase 1 - What the cluster did

```sh
python "$KIT/skills/ignite-incident/scripts/ignite_timeline.py" \
    --inventory "$BUNDLE/analysis/inventory.json" \
    --out "$BUNDLE/analysis/10-cluster-timeline.md" \
    --json "$BUNDLE/analysis/timeline.json"
```

Ignite logs **only** in this phase. Read the digest, then write the narrative section at
the bottom of `10-cluster-timeline.md`: the ordered sequence across nodes, who decided
what, the earliest anomaly, and what the Ignite logs cannot tell you.

Consult `references/20-ignite-log-anatomy.md` for what the messages mean and
`references/30-failure-modes.md` for the mechanics behind the sequence you are seeing.

**Gate:** the narrative is written, and it ends with a list of open questions. That list
is the agenda for Phase 2.

## Phase 2 - What the machine was doing

```sh
python "$KIT/skills/ignite-incident/scripts/gc_digest.py"        --inventory "$BUNDLE/analysis/inventory.json" --out "$BUNDLE/analysis/tmp-gc.md"
python "$KIT/skills/ignite-incident/scripts/os_digest.py"        --inventory "$BUNDLE/analysis/inventory.json" --out "$BUNDLE/analysis/tmp-os.md"
python "$KIT/skills/ignite-incident/scripts/nmon_digest.py"      --inventory "$BUNDLE/analysis/inventory.json" --out "$BUNDLE/analysis/tmp-nmon.md"
python "$KIT/skills/ignite-incident/scripts/threaddump_digest.py" --inventory "$BUNDLE/analysis/inventory.json" --out "$BUNDLE/analysis/tmp-threads.md"
```

Run `gc_digest.py` per node (`--node nodeNN`) when nodes use different collectors.

Read `references/40-jvm-and-os.md` before interpreting any of it. Write
`20-resource-findings.md`, ending with an explicit statement:

> Does the resource evidence explain the Phase 1 timeline? yes / no / partly - and why.

**Gate:** that statement is written. "Partly" is a legitimate answer and must be followed
by what is still unexplained.

## Phase 3 - Hypotheses

```sh
python "$KIT/skills/ignite-incident/scripts/correlate.py" \
    --analysis "$BUNDLE/analysis" --out "$BUNDLE/analysis/tmp-correlated.md"
```

Write `30-hypotheses.md`. For each hypothesis: what it predicts you would see, the
evidence for and against, and **the single cheapest check that would discriminate it**.
Rank them. Then decide - in writing - whether Phase 4 is needed at all, and if so:

- Which question does JFR answer, over which time window, using which event types?
- Which exact log line do you need the source for?

If the cause is already established with the evidence you have, say so and skip to
Phase 5. Opening JFR "to be thorough" is how an analysis stalls.

## Phase 4 - Targeted deep dive

Only against the written questions from Phase 3.

**Source lookup** (see `references/60-source-lookup.md`):

```sh
grep -F 'Unable to await partitions release latch' "$KIT/indexes/<version>/messages.tsv"
# -> literal <TAB> kind <TAB> file <TAB> line
```

Read only the enclosing method. Check the governing knob in `timeouts.tsv` /
`sysprops.tsv` and compare its default against the incident's config.

**JFR** (see `references/50-jfr-playbook.md`):

```sh
python "$KIT/skills/ignite-incident/scripts/jfr_query.py" <recording.jfr> \
    --window "10:22:00" "10:23:30" --events execution,monitor,gc
```

Write `40-source-evidence.md`.

## Phase 5 - Report

Write `50-report.md` using `references/80-report-template.md`. It must contain: the
causal chain, what is proven vs inferred with confidence per link, remaining unknowns
with the check that would close each, and concrete recommendations.

Before you submit it, re-read `references/70-antipatterns.md` and check your report
against every item. That list is the specific set of mistakes this analysis invites.

## Reference files

| file | read it when |
|---|---|
| `references/00-workflow.md` | phase details, gates, the unknown-file routine |
| `references/10-evidence-map.md` | deciding what an artifact can and cannot prove |
| `references/20-ignite-log-anatomy.md` | interpreting Ignite log messages |
| `references/30-failure-modes.md` | the mechanics: segmentation, blocked threads, PME, checkpoints |
| `references/40-jvm-and-os.md` | GC, safepoints, nmon, dmesg, messages |
| `references/50-jfr-playbook.md` | Phase 4, if and only if JFR is justified |
| `references/60-source-lookup.md` | going from a log line to Ignite source cheaply |
| `references/70-antipatterns.md` | before writing the report - mandatory |
| `references/90-when-scripts-fail.md` | any parse problem: the repair ladder and its limits |
| `references/80-report-template.md` | writing the report |
