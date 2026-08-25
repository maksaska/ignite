# JFR: when it earns its cost, and exactly what to ask it

A JFR recording covering the incident is tempting and expensive. It contains hundreds of
thousands of events; dumping it into context answers nothing and leaves no room to think.

**Rule: you may open JFR only with a written question from Phase 3 that names the time
window and the event types, and states what result would confirm or refute a hypothesis.**

---

## When JFR is worth opening

| the question | what to pull |
|---|---|
| Which thread would not reach a safepoint? | `jdk.ExecutionSample` for the stall window - one thread sampled in the same method throughout is the candidate |
| What held the lock that blocked N threads? | `jdk.JavaMonitorEnter`, `jdk.JavaMonitorWait` - gives the owner and duration, which a single thread dump cannot |
| Was the pause really GC? | `jdk.GCPhasePause`, `jdk.YoungGarbageCollection`, `jdk.OldGarbageCollection` - but the safepoint log usually answers this already and more cheaply |
| Was it disk? | `jdk.FileWrite`, `jdk.FileForce` with duration threshold - confirms fsync stalls in the checkpoint window |
| Was it network? | `jdk.SocketRead`, `jdk.SocketWrite` - long blocking socket operations to the peer's discovery port |
| Allocation pressure? | `jdk.ObjectAllocationSample`, `jdk.TLABAllocation` |
| Was the process starved of CPU? | `jdk.CPULoad`, `jdk.ThreadCPULoad` - JVM vs machine load |

## When JFR is *not* worth opening

- The safepoint log already showed the TTSP split. JFR will not add to that.
- The recording does not overlap the stall - check first with `jfr summary`.
- **The stall itself.** JFR events are produced by the JVM; while threads cannot reach a
  safepoint, sampling is also impaired. Expect a *hole* in the recording during the stall.
  That hole is itself evidence, but do not expect samples from inside the freeze.
- You are looking for "anything unusual". That is exploration, not a question.

---

## Getting the recording's shape first

```sh
jfr summary recording.jfr
```

This prints the event types present and their counts, plus the recording start/end. Do
this before anything else - it tells you whether your window is even covered, and which
of the event types above were actually enabled (a `default` profile omits many).

Note the recording start time: **JFR timestamps are epoch-based**. Align them with your
reference timezone from Phase 0.

## Pulling a window

`jfr_query.py` wraps the `jfr` CLI and applies the window and event filters:

```sh
python jfr_query.py recording.jfr --summary
python jfr_query.py recording.jfr \
    --window "2024-03-14 10:22:00" "2024-03-14 10:23:30" \
    --events execution --top 20
```

Event group shorthands: `execution`, `monitor`, `gc`, `safepoint`, `io`, `socket`,
`alloc`, `cpu`. Or pass exact names with `--event-types jdk.ExecutionSample,...`.

Raw `jfr` if you need something the wrapper does not cover:

```sh
jfr print --events jdk.ExecutionSample --stack-depth 12 recording.jfr | head -400
```

Always bound the output. `--stack-depth` above 16 and no `head` will flood you.

---

## Reading execution samples

The useful reduction is: **count samples per top frame, per thread**, and look at the
distribution rather than individual stacks. `jfr_query.py --top N` does this.

- One thread, one method, throughout the window -> that is your stuck thread.
- Many threads in the same framework frame (e.g. page-memory acquire) -> contention, not
  a single culprit.
- Samples stop entirely mid-window -> the JVM was stopped. Confirms the stall, and the
  gap boundaries give you its extent independently of the safepoint log.

## Writing it up

Record in `40-source-evidence.md`:

- The question you asked, verbatim from Phase 3.
- The window and event types used.
- The result - including "no samples in this window", which is a real finding.
- Whether it confirms, refutes, or fails to discriminate the hypothesis. All three are
  legitimate outcomes; the third means you need a different check, not a re-read.
