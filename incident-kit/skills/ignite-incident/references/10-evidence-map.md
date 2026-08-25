# What each artifact can and cannot prove

Match the claim to an artifact that can actually support it. Most wrong conclusions come
from asking an artifact a question it cannot answer.

| artifact | can establish | cannot establish | resolution limit |
|---|---|---|---|
| **Ignite logs** | what the cluster believed: topology, who left, who decided, exchange and checkpoint timings, configured values as actually used | why the JVM or machine misbehaved; anything during a stall (the node writes nothing) | millisecond, but only while the node is alive |
| **GC log** | collector work: pause durations, heap occupancy, allocation pressure, collector failures | whether a pause was GC's fault (needs safepoint data); anything outside the JVM | per GC event |
| **Safepoint log** | the TTSP vs at-safepoint split - i.e. *whether* GC is to blame; total application stop time | *why* TTSP was long | per safepoint |
| **nmon** | machine resource trend: CPU, memory, swap, disk busy, network throughput | anything shorter than the sample interval; per-process attribution (unless TOP is enabled) | **sample interval, typically 30-60 s** |
| **dmesg** | kernel-level events: OOM kill, reclaim stalls, hung tasks with call traces, NIC/storage errors, clock changes | wall-clock time (monotonic only, must be aligned) | microsecond, monotonic |
| **messages / syslog** | the same kernel events *with wall clock*, plus systemd service lifecycle | anything the kernel or systemd did not log | second, no year, no zone |
| **Thread dump** | what every thread was doing at **one instant**; lock ownership and contention | anything before or after that instant; duration of anything | a single point in time |
| **JFR** | sampled execution, lock waits, I/O and socket durations, GC phases, CPU load | events during a JVM freeze (sampling is impaired); anything outside the recording window | sampling interval; a hole during a stall |
| **Configs** | what was intended | what was actually in effect - prefer the Ignite log's startup echo of `IgniteConfiguration` | n/a |
| **Ignite source (via index)** | the condition that produced a message, the governing threshold, what happens next | anything about this particular run | n/a |

---

## Claims and the evidence they require

| claim | minimum evidence |
|---|---|
| "The node was evicted for being unresponsive" | peer's `Socket write has timed out` / `Failed to send message to next node`, peer's `Node FAILED`, victim's log gap covering the same window |
| "A GC pause caused it" | safepoint record with a large **at-safepoint** value, in the window, on that node |
| "A time-to-safepoint stall caused it" | safepoint record with a large **Reaching safepoint** value, plus a machine-level cause (swap/CPU) from nmon or dmesg |
| "It was a network problem" | NIC/TCP errors in dmesg or messages on at least one side, *and* the victim's log showing normal activity through the window (i.e. the victim was alive and healthy) |
| "The disk was the bottleneck" | checkpoint `fsync`/`pagesWrite` dominating, plus nmon DISKBUSY near saturation in the same minutes |
| "The JVM was killed by the kernel" | OOM killer line in dmesg/messages, and an Ignite log that simply stops with no shutdown sequence |
| "One node stalled the whole cluster" | `pendingAcks` in the partitions-release-latch warning naming that node, plus that node's own resource evidence |
| "Memory pressure caused the stall" | nmon swap/free collapse **and** dmesg reclaim stalls; nmon alone is too coarse for a short event |

---

## Absence as evidence

**Precondition, no exceptions:** an absence claim is only valid for a file whose parse
health is `OK`. Check the parse-health table in the digest, or `00.5-preflight.md`, before
writing any of the statements below. "The parser found nothing" and "the parser could not
read the file" produce the same empty output; only the parse rate separates them, and
mistaking one for the other turns a tooling failure into a confident wrong conclusion.

With that established, missing signals constrain hypotheses, so state them:

- No `Blocked system-critical thread` during a long stall -> either the watchdog thread was
  stopped too (whole-JVM freeze), or `systemWorkerBlockedTimeout` is higher than the stall.
- No shutdown sequence at the end of a node's log -> the process did not exit cleanly:
  kernel kill, `Runtime.halt()` from the failure handler, or a hard machine failure.
- No GC events in a window where the JVM was clearly stopped -> the stop was not GC.
- No JFR samples in a window -> the JVM was not running threads.
- nmon shows nothing -> **if the event was shorter than the sample interval, this proves
  nothing at all.** Say so explicitly rather than reporting "resources were normal".
- A digest section is empty and its file's parse health is not `OK` -> this is not an
  absence at all. See `90-when-scripts-fail.md`; report it under "Collection gaps", not
  under findings.

---

## Attribution rules

1. A message describes the state of **the node that wrote it**, not the cluster. `Node
   FAILED` on node01 means node01 believes node03 failed.
2. A node's view of another node is delayed by discovery propagation. Small ordering
   differences between nodes' logs are normal; large ones mean a clock problem.
3. `LT.warn`-throttled messages appear once even when the condition occurred many times.
   Frequency in the log is not frequency in reality for those.
4. Any timestamp comparison across artifacts requires the Phase 0 offsets. Without them,
   do not compare.
