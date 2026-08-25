# Report template

Copy this structure into `50-report.md`. Keep it factual; the reader is an engineer who
will act on it.

Confidence labels, used on every link in the chain:

- **proven** - a specific artifact shows it directly; cite the reference.
- **inferred** - follows from proven facts plus documented Ignite mechanics; say which.
- **speculative** - consistent with the evidence but not established; say what would test it.

---

```markdown
# Incident report: <cluster> <date>

## Summary

<Three to five sentences. What failed, what the root cause was, what the impact was,
and the single most important corrective action. Someone who reads only this should be
able to act.>

## Impact

- Window: <start> - <end> (<reference timezone>)
- Nodes affected: <list, with roles - coordinator, victim, unaffected>
- Cluster effect: <writes blocked / partial outage / rebalance / full outage>
- How it ended: <self-recovery, restart, manual intervention>

## Causal chain

| # | step | evidence | confidence |
|---|---|---|---|
| 1 | <root cause> | <file:line or digest section> | proven / inferred |
| 2 | <consequence> | ... | ... |
| 3 | ... | ... | ... |

<Then the same chain in prose, one short paragraph. The table is for scanning; the
paragraph is for understanding.>

## Timeline

| time | node | event | source |
|---|---|---|---|
| | | | |

<Normalised to the reference timezone. Note any artifact whose clock had to be offset,
and by how much.>

## Analysis

### What the cluster did
<From Phase 1. The membership story and who decided what.>

### What the machine was doing
<From Phase 2. GC vs TTSP split, memory, CPU, disk, kernel events.>

### Why this produced that
<The mechanism. Reference 30-failure-modes.md where it applies, and the Ignite source
where you verified a threshold or condition - class and method, with line if the index
version matches the incident version.>

## Ruled out

| hypothesis | ruled out by |
|---|---|
| <e.g. network fault> | <the specific evidence that excludes it> |

<This section is not optional. It is what tells the reader the conclusion was chosen
rather than assumed.>

## Remaining unknowns

| unknown | why it matters | what would settle it |
|---|---|---|
| | | |

## Recommendations

Ordered by expected effect, root cause first.

| # | action | addresses | type |
|---|---|---|---|
| 1 | <change> | <chain step #> | root cause / mitigation / detection |

<Label each honestly. Raising a timeout when the node froze for 22 seconds is a
mitigation, not a fix - say so.>

## Collection gaps

<What was missing from the bundle that would have made this analysis conclusive - e.g.
"no safepoint log on node02", "nmon interval 60 s cannot resolve a 20 s event". This
section improves the next incident.>

## What was examined

| artifact | used | note |
|---|---|---|
| ignite logs (n nodes) | yes | |
| gc / safepoint | yes | |
| nmon | yes | resolution 60 s |
| dmesg / messages | yes | |
| thread dump | yes | single instant at <time> |
| JFR | no | not needed - safepoint log answered the question |
| config | yes | effective values taken from the startup echo |

<Anything not examined must appear here with the reason.>
```

---

## Before you submit

1. Every row in the causal chain has an evidence reference and a confidence label.
2. The "Ruled out" section is filled in.
3. Every recommendation is labelled root cause / mitigation / detection.
4. You have re-read `70-antipatterns.md` and checked each item.
5. Nothing in the report asserts more than the evidence supports. If you had to write
   "probably" three times in one paragraph, mark that link speculative and say what would
   settle it.
