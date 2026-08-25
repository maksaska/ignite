# Ignite failure modes: the mechanics

This is the document you cannot derive from the logs. It explains *why* Ignite produces
the sequences you are looking at, so you can tell a cause from a consequence.

Line references are to Apache Ignite `master` at the time the kit was built; use
`messages.tsv` for the exact line in the version you are analysing. Class names are
stable across 2.x even when line numbers are not.

---

## 1. Discovery ring segmentation

**This is the most commonly misread sequence in Ignite incident analysis.**

### How the ring works

`TcpDiscoverySpi` arranges server nodes in a logical ring ordered by node order. Each node
has exactly one *next* node. Messages (including metrics updates, which act as heartbeats)
travel around the ring. The implementation is `ServerImpl$RingMessageWorker`
(`modules/core/.../spi/discovery/tcp/ServerImpl.java`).

### What happens when a node stops responding

1. The **previous** node in the ring tries to send to its next node and the socket write
   blocks. It logs:

   > `Socket write has timed out (consider increasing 'IgniteConfiguration.failureDetectionTimeout' ...) [failureDetectionTimeout=..., rmtAddr=..., sockTimeout=...]`
   > - `TcpDiscoverySpi`

2. It retries, then tries to route around:

   > `Failed to send message to next node [msg=..., next=...]`
   > `Failed to send message to next node, try previous [...]`
   > - `ServerImpl`

3. Having failed, it starts the cluster-wide failure procedure:

   > `Local node has detected failed nodes and started cluster-wide procedure. To speed up failure detection please see 'Failure Detection' section under javadoc for 'TcpDiscoverySpi'`
   > - `ServerImpl`

4. The coordinator publishes the node-failed event; **every surviving node** logs:

   > `Node FAILED: TcpDiscoveryNode [id=..., consistentId=..., order=...]`
   > `Topology snapshot [ver=N+1, ..., servers=<one fewer>, ...]`
   > - `GridDiscoveryManager`

5. Meanwhile the **victim**, when (and only when) it comes back to life, discovers it has
   been evicted:

   > `Node is out of topology (probably, due to short-time network problems).`
   > - `ServerImpl`
   > `Local node SEGMENTED: TcpDiscoveryNode [...]`
   > - `GridDiscoveryManager`

### The crucial inference

**Steps 1-4 happen on other nodes while the victim is doing nothing at all.** If the
victim was stalled - swapping, in a long safepoint, CPU-starved - its own log shows a
*silent gap* and then, abruptly, the segmentation messages. The gap is the evidence.
`ignite_timeline.py` reports gaps for exactly this reason.

So:

- Victim's log shows a gap, then SEGMENTED -> **the victim stalled.** The network was fine.
  Find out why it stalled (Phase 2).
- Victim's log shows continuous, healthy activity right up to SEGMENTED -> **the network
  or the peer failed**, not the victim. Look at NIC errors, TCP retransmits, switch logs.
- Neither -> look harder before concluding; check whether the victim's log was truncated
  or rotated at that moment.

`Node is out of topology` is a *diagnosis by the victim of its own eviction*, not an
observation of a network problem, despite the parenthetical in the message text. Do not
quote that message as evidence that there was a network problem.

### What happens after segmentation

`GridDiscoveryManager.onSegmentation()` disconnects the SPI and then dispatches on
`IgniteConfiguration.segmentationPolicy`:

| policy | effect |
|---|---|
| `RESTART_JVM` | failure processor with the restart handler |
| `STOP` | failure processor with the stop-node handler |
| `USE_FAILURE_HANDLER` | goes to the configured `FailureHandler` (default) |
| `NOOP` | nothing - node stays up, segmented and useless |

With the common `StopNodeOrHaltFailureHandler` you then see:

> `Critical system error detected. Will be handled accordingly to configured handler [hnd=StopNodeOrHaltFailureHandler [...], failureCtx=FailureContext [type=SEGMENTATION, err=null]]`
> - `FailureProcessor`
> `JVM will be halted immediately due to the failure: [failureCtx=FailureContext [type=SEGMENTATION, err=null]]`
> - `StopNodeOrHaltFailureHandler`

`err=null` and `type=SEGMENTATION` mean **this halt is the deliberate, configured
response to segmentation** - not an independent crash. Do not report it as a second
failure, and do not go looking for an exception that does not exist.

### Knobs that decide the outcome

Look these up in `timeouts.tsv` and compare with the incident's config:

- `IgniteConfiguration.failureDetectionTimeout` (default 10 000 ms) - the umbrella
  timeout when the SPI-specific ones are not set explicitly.
- `TcpDiscoverySpi.socketWriteTimeout`, `ackTimeout`, `reconnectCount` - used instead of
  the umbrella if set explicitly. **If any of these is set, `failureDetectionTimeout` is
  ignored for that operation** - a frequent source of confusion when the numbers in the
  logs do not match the configured value.
- `clientFailureDetectionTimeout` (default 30 000 ms) - clients only.

Arithmetic that matters: if the JVM was stopped for longer than
`failureDetectionTimeout`, eviction is *expected behaviour*, not a bug. Say so, and move
the question to why the JVM was stopped.

---

## 2. Blocked system-critical thread

`WorkersRegistry` (`modules/core/.../internal/worker/WorkersRegistry.java`) monitors
registered critical workers by heartbeat. If one has not updated its heartbeat within
`IgniteConfiguration.systemWorkerBlockedTimeout` (falling back to
`failureDetectionTimeout` when unset), it logs:

> `Blocked system-critical thread has been detected. This can lead to cluster-wide undefined behaviour [workerName=..., threadName=..., blockedFor=Ns]`

and raises `FailureType.SYSTEM_WORKER_BLOCKED` through `FailureProcessor`.

Read it carefully:

- `blockedFor=Ns` is **time since last heartbeat**, not time spent holding a lock.
- A *whole-JVM* stall (safepoint, swap, CPU starvation) blocks every worker at once, so
  you will see several of these at the same instant. Many simultaneous blocked workers
  is a machine-level symptom, not several independent bugs.
- One worker blocked while others heartbeat normally is a real per-thread problem - a
  lock, a slow disk call, an unbounded loop. Then the thread dump matters.

`AbstractFailureHandler.ignoredFailureTypes` commonly includes `SYSTEM_WORKER_BLOCKED`
and `SYSTEM_CRITICAL_OPERATION_TIMEOUT`, in which case you see
`Possible failure suppressed accordingly to a configured handler` instead of a node stop.
Check the `hnd=` part of the `FailureProcessor` line for the actual configuration -
do not assume the default.

---

## 3. Long JVM pause

`LongJVMPauseDetector` (`modules/core/.../internal/LongJVMPauseDetector.java`) runs a
thread that sleeps in a loop and measures how much wall-clock time actually elapsed. When
the overshoot exceeds its threshold:

> `Possible too long JVM pause: N milliseconds.`

**This measures that the JVM stopped making progress. It says nothing about the cause.**
The causes are, roughly in order of frequency in production:

1. Time-to-safepoint stall (threads could not be stopped) - usually swapping or CPU
   starvation. **Not GC**, even though it appears next to GC in every dashboard.
2. An actual long GC pause.
3. The whole machine froze - hypervisor steal, storage controller stall, kernel issue.
4. The detector thread itself was descheduled (CPU oversubscription).

Confirm which by reading the safepoint log - `gc_digest.py` reports the split explicitly.
See `40-jvm-and-os.md`.

---

## 4. Partition map exchange (PME) hang

Every topology change triggers an exchange. All nodes must finish local work and
acknowledge before the exchange completes; one slow node stalls the whole cluster, and
**cache operations block cluster-wide while it does**. This is why a single sick node
produces a cluster-wide outage.

Sequence in the logs:

> `Started exchange init [topVer=AffinityTopologyVersion [topVer=N, minorTopVer=0], crd=true, evt=NODE_FAILED, evtNode=...]`
> - `GridDhtPartitionsExchangeFuture`

Then, if it does not complete:

> `Unable to await partitions release latch within timeout. Waiting for latch [latch=ServerLatch [permits=1, pendingAcks=HashSet [<node-id>], ...]]`
> - `GridDhtPartitionsExchangeFuture`

**`pendingAcks` names the node(s) everyone is waiting for. That is the single most
valuable field in a PME hang** - it identifies the culprit directly. Go to that node's
log and its resource digests for the same window.

> `Failed to wait for partition map exchange [topVer=..., node=...]. Dumping pending objects that might be the cause:`
> - `GridCachePartitionExchangeManager`

followed by a large diagnostic dump of pending transactions, locks and futures. That dump
is worth reading *selectively* - the first few entries usually name the blocking
operation. It repeats periodically while the exchange is stuck, so use the **first**
occurrence.

Distinguish:

- Exchange never started -> discovery problem, not exchange.
- Exchange started, latch not acked -> the named node is stuck (look at it).
- Exchange finished but slowly -> check what dominated: rebalance, checkpoint, WAL.

---

## 5. Checkpoint and write throttling

`Checkpointer` (`.../cache/persistence/checkpoint/Checkpointer.java`) logs:

> `Checkpoint started [checkpointId=..., checkpointLockWait=Nms, checkpointLockHoldTime=Nms, pages=N, reason='...']`
> `Checkpoint finished [cpId=..., pages=N, markDuration=Nms, pagesWrite=Nms, fsync=Nms, total=Nms]`

How to read the phases:

| field | meaning | what a large value implies |
|---|---|---|
| `checkpointLockWait` | waiting to take the checkpoint write lock | contention with cache operations |
| `checkpointLockHoldTime` | **cache operations are blocked for this long** | directly visible as application latency |
| `markDuration` | collecting dirty pages | usually small |
| `pagesWrite` | writing pages to disk | I/O bandwidth or page count |
| `fsync` | flushing | storage latency, often the real problem |
| `total` | whole checkpoint | compare with `checkpointFrequency` |

`reason='timeout'` is the scheduled case. `reason='too many dirty pages'` means the write
load outran the checkpoint - a pressure signal.

**A checkpoint whose `total` approaches or exceeds `checkpointFrequency` means
checkpoints overlap conceptually and the node is falling behind.** That is a strong
precursor for everything else: the page cache fills with dirty pages, memory pressure
rises, and (on a machine with swap enabled) the JVM's own pages can be evicted - which
produces the time-to-safepoint stall in mode 3 and then segmentation in mode 1.

Throttling appears as:

> `Throttling is applied to page modifications [fractionOfParkTime=..., markDirty=N pages/sec, checkpointWrite=N pages/sec, ...]`
> - `PagesWriteSpeedBasedThrottle`

Throttling is Ignite deliberately slowing writers to let the checkpoint catch up. It is a
symptom of I/O being too slow for the write rate. It also means application threads are
being parked - which shows up as latency, and can contribute to a safepoint stall.

---

## 6. The composite failure this kit was built around

The most common production sequence, and the one that is most often misdiagnosed:

```
heavy write load
  -> checkpoint takes far longer than usual (fsync/pagesWrite dominate)
  -> dirty pages accumulate, write throttling starts
  -> OS memory pressure (page cache + heap + off-heap)
  -> kernel reclaims / swaps out JVM pages          [dmesg: allocation stalls, kswapd]
  -> a thread cannot reach a safepoint for tens of seconds  [safepoint log: huge TTSP]
  -> JVM makes no progress                          [Ignite: Possible too long JVM pause]
  -> discovery heartbeats stop                      [peer: Socket write has timed out]
  -> coordinator evicts the node                    [peer: Node FAILED, topology ver+1]
  -> victim wakes, finds itself evicted             [victim: Node is out of topology,
                                                             Local node SEGMENTED]
  -> configured handler halts the JVM               [victim: JVM will be halted immediately]
  -> PME on the surviving nodes stalls on rebalance  [Unable to await partitions release latch]
```

Every step here is a *consequence* of the previous one. The naive reading - "network
problem caused segmentation" or "GC pause killed the node" - picks a symptom from the
middle of this chain. The root cause is at the top: checkpoint/IO capacity versus write
load, plus a memory configuration that left no headroom.

To claim this chain you need: the checkpoint phase timings, the TTSP split, the memory
and swap evidence, and the peer's discovery messages. If you are missing one of those,
say which link is inferred rather than proven.
