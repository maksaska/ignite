---
name: ignite-incident
description: Analyse an Apache Ignite production incident from a bundle of logs (ignite logs, GC and safepoint logs, dmesg, messages, nmon, configs, JFR, thread dumps) and find the root cause, using indexes built from the product's source repositories. Use this whenever asked to investigate an Ignite outage, node failure, segmentation, cluster hang, long pause, or "why did this node die".
---

# Ignite incident analysis

You are analysing a production incident. Work through the phases **in order**. Each phase
ends at a gate where you **stop and hand back to the user**. The findings files are your
memory — later phases read them instead of you holding everything in context.

## Rules that override your instincts

1. **Never read a raw log file** as a substitute for analysis. Run the digest for that
   artifact type and read its output. If a digest line needs following up, it carries
   `file:line` — read *those* lines only, with a narrow range.
2. **Never grep the source repositories to find where a log message comes from.** Look it
   up in `analysis/indexes/messages.tsv`. That file exists so you don't explore.
3. **Never open the JFR recording, and never read source, before Phase 4** — and then only
   against a written question that names what you expect to find. "Let me look at the JFR
   to see if anything stands out" is not a question.
4. **The node that died is usually not where the cause is visible.** A segmented or halted
   node logs the *consequence*. The *decision* is in the coordinator's log and the ring
   neighbour's log. Always digest all nodes together.
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
   `references/90-when-scripts-fail.md`. Reading raw log lines IS allowed there, bounded —
   that is the one place in this workflow where it is the right move.
9. **Stop at every gate.** Do not run several phases back to back. At each gate: write the
   findings file, summarise what the phase established in 2–4 sentences, say what the next
   phase will examine and what it needs, name anything you are unsure about, and wait. A
   bare "shall I continue?" wastes the review these gates exist for.

## Setup

Two variables. Everything else is derived from them.

```sh
SKILL=~/.claude/skills/ignite-incident     # wherever the skill is installed
BUNDLE=/path/to/incident                   # the directory holding the incident data
```

If the user has not run `init`, do it — naming every repository that makes up their
product. That is usually Apache Ignite plus `ignite-extensions` plus private code:

```sh
python "$SKILL/kit.py" init "$BUNDLE" \
    --repo ~/src/ignite \
    --repo ~/src/ignite-extensions \
    --repo ~/src/private
```

`init` creates `$BUNDLE/analysis/`, copies the workspace templates, and builds the source
indexes into `$BUNDLE/analysis/indexes/`. The indexes are rebuilt from scratch every time,
because a stale index means every line number you cite is wrong.

The skill needs nothing outside its own directory. It does not care which branch any
repository is on — except that the branches the user checked out are what gets indexed,
which is why the gate after `init` asks them to confirm.

`python "$SKILL/kit.py" status "$BUNDLE"` shows what is done and what is next at any time.

## Phase 0 — Inventory

```sh
python "$SKILL/kit.py" phase0 "$BUNDLE"
```

Classifies every file by content, never by name. Read `analysis/00-inventory.md`, then
fill in, in that file: the incident window, the reference timezone, and the clock offsets
between artifacts.

**Gate.** Every file classified. If anything is `unknown`, work
`references/90-when-scripts-fail.md` first — do not skip a file because it "looks
unimportant". Then stop and report per rule 9.

## Phase 0.5 — Can the parsers actually read it?

```sh
python "$SKILL/kit.py" preflight "$BUNDLE"
```

Runs the real parsers over the real files and reports how much of each one they
understood, plus a completeness cross-check and the provenance of the source indexes. It
performs no analysis.

**Gate.** Verdict `OK`, **or** every `DEGRADED`/`FAILED` file recorded in
`00-inventory.md` with what you will therefore not claim from it. Check the provenance
table too: a version mismatch or a dirty repository means you cite classes and methods,
not line numbers. Then stop and report.

Skipping this phase is how an analysis ends up confidently describing a cluster from a
digest that read a third of the evidence.

## Phase 1 — What the cluster did

```sh
python "$SKILL/kit.py" phase1 "$BUNDLE"
```

Ignite logs **only**. Read the digest, then write the Narrative section of
`10-cluster-timeline.md`: the ordered sequence across nodes, who decided what, the
earliest anomaly, and what the Ignite logs cannot tell you.

Consult `references/20-ignite-log-anatomy.md` for what the messages mean and
`references/30-failure-modes.md` for the mechanics behind the sequence.

**Gate.** The narrative is written and ends with the open questions Phase 2 must answer.
Then stop and report.

## Phase 2 — What the machine was doing

```sh
python "$SKILL/kit.py" phase2 "$BUNDLE"
```

Runs the GC/safepoint, OS, nmon and thread-dump digests — GC per node when nodes use
different collectors. Read `references/40-jvm-and-os.md` **before** interpreting any of
it, especially the time-to-safepoint versus at-safepoint distinction.

Write `20-resource-findings.md`, ending with an explicit statement:

> Does the resource evidence explain the Phase 1 timeline? yes / no / partly — and why.

**Gate.** That statement is written. "Partly" is legitimate and must be followed by what
is still unexplained. Then stop and report.

## Phase 3 — Hypotheses

```sh
python "$SKILL/kit.py" phase3 "$BUNDLE"
# clock offsets from Phase 0, if any:
python "$SKILL/kit.py" phase3 "$BUNDLE" --offset gc=-10800 --year 2024
```

Write `30-hypotheses.md`: for each hypothesis, its statement, what it predicts, evidence
for and against, and the single cheapest check that would discriminate it. Rank them.
Then write the Phase 4 decision explicitly — including "no further evidence needed", which
is a good outcome, not a shortcut.

**Gate.** Stop and get the user's agreement before anything expensive is opened.

## Phase 4 — Targeted deep dive

```sh
python "$SKILL/kit.py" phase4 "$BUNDLE"
```

Runs nothing on its own; it answers only the questions written in Phase 3.

```sh
grep -F 'Unable to await partitions release latch' "$BUNDLE/analysis/indexes/messages.tsv"
# -> repo <TAB> literal <TAB> kind <TAB> file <TAB> line
```

See `references/60-source-lookup.md` and `references/50-jfr-playbook.md`. Write
`40-source-evidence.md`.

**Gate.** Stop and report what each lookup established — including nil results, which are
real results.

## Phase 5 — Report

```sh
python "$SKILL/kit.py" phase5 "$BUNDLE"
```

Write `50-report.md` using `references/80-report-template.md`, then check it against every
item in `references/70-antipatterns.md` before handing it over.

## When something will not parse

```sh
python "$SKILL/kit.py" diagnose "$BUNDLE" ignite|gc|os|nmon|threads
```

Then work `references/90-when-scripts-fail.md`.

## Reference files

| file | read it when |
|---|---|
| `references/00-workflow.md` | phase details, gates, clock alignment |
| `references/10-evidence-map.md` | deciding what an artifact can and cannot prove |
| `references/20-ignite-log-anatomy.md` | interpreting Ignite log messages |
| `references/30-failure-modes.md` | the mechanics: segmentation, blocked threads, PME, checkpoints |
| `references/40-jvm-and-os.md` | GC, safepoints, nmon, dmesg, messages |
| `references/50-jfr-playbook.md` | Phase 4, if and only if JFR is justified |
| `references/60-source-lookup.md` | going from a log line to source across all indexed repos |
| `references/70-antipatterns.md` | before writing the report — mandatory |
| `references/80-report-template.md` | writing the report |
| `references/90-when-scripts-fail.md` | any parse problem: the repair ladder and its limits |
