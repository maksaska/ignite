# Workflow: phases, gates, and what to do when something does not fit

The phase order exists because each phase is cheaper than the next and constrains it.
Skipping ahead means paying the expensive cost (JFR, source reading, your context window)
on a question you have not yet defined.

---

## Phase 0 - Inventory

**Goal:** know exactly what you have, what clock each artifact uses, and when the
incident happened.

**Do:**
1. Run `identify.py` with `--out` and `--json`.
2. Read the generated `00-inventory.md`.
3. Fill in the *Incident window* table: first symptom, window start, window end,
   reference timezone.
4. Fill in the clock offsets (see below).

**Gate:** exit code 0 (no `unknown` files) **and** the incident window written down.

### Establishing clock offsets

You need every artifact expressed in one reference timezone before Phase 3.

| artifact | what it gives you | how to align |
|---|---|---|
| Ignite log | local wall clock, zone not printed | infer zone from JVM flags (`-Duser.timezone`), the OS config in the bundle, or by matching an event that also appears in the GC log (which *does* print the offset) |
| GC / safepoint log | wall clock **with** UTC offset | this is your anchor - it is the only JVM artifact that states its zone |
| syslog / `messages` | wall clock, no year, no zone | year from the bundle; zone is the host's local zone |
| `dmesg` | seconds since boot | find a line that appears in both `dmesg` and `messages` (kernel messages are usually in both) and compute the offset; or use boot time if the bundle has `uptime` output |
| nmon | wall clock, collecting host's local zone | `AAA,date` and `ZZZZ` records |
| JFR | epoch millis | `jfr summary` prints the recording start |
| thread dump | one wall-clock line at the top | that line only |

Write the offsets into `00-inventory.md`. If you cannot align `dmesg`, say so - and then
use `messages` for kernel events instead, since it carries wall clock.

### When a file is `unknown`

Do not ignore it and do not guess from its name.

1. Look at its start and end:
   ```sh
   head -50 <file>; echo '...'; tail -50 <file>
   ```
2. Decide what it is. Common things the classifier does not know: vendor-specific
   collector output, application logs from a co-located service, JVM flag dumps in an
   unusual format, `jcmd` output, heap histograms, plugin logs.
3. If it is relevant, add a signature so the next run classifies it. Create or edit
   `signatures.local.json` next to the bundle:
   ```json
   {
     "ignite_log": [["MyCompanyIgniteWrapper", 6]],
     "vendor_gc": [["\\[GC concurrent-", 8]]
   }
   ```
   Format: `{"kind": [[regex, weight], ...]}`. Weights add up; 6 is the threshold.
   Re-run with `--signatures signatures.local.json`.
4. If it is not relevant, record in `00-inventory.md` what it is and why you are
   excluding it. "Not examined" must be a decision, not an omission.

---

## Phase 1 - What the cluster did

**Goal:** the ordered story of the cluster, from Ignite logs only, across all nodes.

**Do:** run `ignite_timeline.py`, read the digest, write the narrative.

**Why Ignite logs first:** they are the only artifact that tells you what the *system*
believed was happening - who was in the topology, who decided what. Resource data without
that frame is a pile of numbers.

**Gate:** the narrative section answers all four questions in the digest's gate, and ends
with a list of open questions.

Useful narrowing once you know the window:

```sh
python ignite_timeline.py --inventory inventory.json \
    --start "2024-03-14 10:15:00" --end "2024-03-14 10:30:00" \
    --min-severity 1 --gap-seconds 10
```

`--min-severity 1` includes context events (topology snapshots, checkpoints, exchange
init/finish). Use it once you have narrowed the window, not on the whole log.

---

## Phase 2 - What the machine was doing

**Goal:** determine whether the machine explains the cluster's behaviour.

**Do:** run all four resource digests, read `40-jvm-and-os.md`, write
`20-resource-findings.md`.

**Order within the phase:** GC/safepoint first (it usually decides the shape of the
answer), then nmon and OS logs to explain what the safepoint data showed, then the thread
dump if a specific thread is implicated.

**Gate:** an explicit yes / no / partly on whether resource evidence explains Phase 1,
with the numbers quoted side by side.

The thread dump deserves a caution: it is **one instant**. It shows what threads were
doing at that moment, which may be after the interesting event. Check its timestamp
against your window before drawing anything from it.

---

## Phase 3 - Hypotheses

**Goal:** a ranked set of candidate explanations, and a decision about whether more
evidence is needed.

Each hypothesis needs four things:

1. **Statement** - one sentence.
2. **Prediction** - what else would be true if this were the cause.
3. **Evidence for / against** - with references.
4. **Cheapest discriminating check** - the one action that would move your confidence
   most.

Then write the Phase 4 decision explicitly:

- *No further evidence needed* - go to Phase 5. This is a good outcome, not a shortcut.
- *JFR needed* - state the question, the window (to the second), and the event types.
- *Source needed* - state the exact log line and what you expect the code to reveal
  (a threshold, an ordering, a condition).

A hypothesis you cannot discriminate with the available artifacts should be recorded as
such and carried into the report's "remaining unknowns".

---

## Phase 4 - Targeted deep dive

Only the questions written in Phase 3. If you find yourself exploring, stop and go back
to Phase 3 - the exploration means the question was not sharp enough.

See `50-jfr-playbook.md` and `60-source-lookup.md`.

---

## Phase 5 - Report

See `80-report-template.md`. Then check against `70-antipatterns.md`, item by item.

---

## If the incident does not fit any known failure mode

This happens and it is fine. Do this:

1. Describe precisely what you observe, in time order, with references.
2. State which known modes you considered and the specific evidence that rules each out.
3. List what artifact would have answered the question, and note it as a collection gap
   for next time (e.g. "no safepoint log was captured, so the pause could not be
   attributed").
4. Do not name a root cause you cannot support.

An accurate "here is what happened, here is what we cannot yet explain, here is what to
collect next time" is a genuinely useful report. A confident wrong diagnosis costs the
team a week.
