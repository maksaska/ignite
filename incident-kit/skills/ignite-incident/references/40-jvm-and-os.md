# Reading the JVM and OS evidence

---

## 1. Safepoints: the distinction that decides the analysis

Every stop-the-world event has two parts:

```
        request                all threads stopped              resumed
           |------------------------|--------------------------------|
           |   reaching safepoint   |         at safepoint            |
           |        (TTSP)          |     (the VM operation runs)     |
```

- **Reaching safepoint (TTSP)** - the JVM has asked every application thread to stop at
  its next safepoint poll. This takes as long as the *slowest* thread needs to get there.
- **At safepoint** - the actual work: a GC pause, a deoptimisation, a biased-lock
  revocation, a thread dump, a heap inspection.

`gc_digest.py` reports both and never merges them. The interpretation:

| pattern | meaning | where to look next |
|---|---|---|
| At-safepoint large, TTSP small | a genuine GC (or VM operation) pause | heap sizing, collector tuning, live-set growth, allocation rate |
| **TTSP large, at-safepoint small** | **threads could not be stopped - not a GC problem** | memory pressure / swap, CPU starvation, native calls, JIT loops |
| Both small but many events | death by a thousand cuts | safepoint operation breakdown - something is triggering constantly |
| No safepoint log | you cannot tell | say so in the report; do not guess |

### Causes of a large TTSP, most common first

1. **Swapping / page reclaim.** A thread's stack or JIT-compiled code has been paged out.
   To reach a safepoint it must fault those pages back in, from disk, while under memory
   pressure. This is the classic cause on an Ignite node with persistence enabled, where
   the page cache competes with heap and off-heap.
   Confirm with: nmon swap-in and free memory; `dmesg` allocation stalls, `kswapd`,
   `page allocation failure`, `blocked for more than N seconds`.

2. **CPU starvation.** Run queue longer than the core count, or steal time on a VM. The
   thread is runnable but not scheduled.
   Confirm with: nmon CPU (idle near zero, high `Wait%` or `Steal%`), run queue.

3. **A long-running counted loop in JIT-compiled code.** HotSpot may omit safepoint polls
   from counted loops. Rare, and usually reproducible rather than incidental.
   Confirm with: JFR `jdk.ExecutionSample` showing one thread in the same method
   throughout the stall.

4. **A slow native call.** The thread is in native code and only polls on return.
   Confirm with: thread dump showing a thread in a native I/O frame.

### Time-to-safepoint versus `failureDetectionTimeout`

This is the arithmetic that connects the JVM to the cluster outcome:

```
if (total stop time) > failureDetectionTimeout  ->  eviction is expected behaviour
```

State it that way in the report. The eviction is then not a bug to be fixed by raising
the timeout; it is the cluster correctly noticing an unresponsive node.

---

## 2. GC logs

`identify.py` detects the collector; `gc_digest.py` parses accordingly.

**G1** - `Pause Young`, `Pause Young (Mixed)`, `Pause Full`, `Pause Remark`,
`Pause Cleanup` are stop-the-world. Watch for:
- `To-space exhausted` / `Evacuation Failure` - the collector ran out of room to copy
  into. Pauses spike. Usually means heap is too small or allocation too fast.
- `G1 Humongous Allocation` - objects larger than half a region. On Ignite, often large
  byte arrays. Frequent humongous allocations fragment the heap.
- `Pause Full` - a full compacting collection. On a 32 GB heap this is seconds. Any
  occurrence is significant.

**ZGC** - almost everything is concurrent; the `Pause Mark Start`, `Pause Mark End`,
`Pause Relocate Start` phases are sub-millisecond by design. If you see multi-second
stops with ZGC, they are almost certainly **not** collector pauses - look at TTSP, or at
`Allocation Stall` messages, which mean allocation outran the collector.

**Shenandoah** - similar: `Pause Init Mark`, `Pause Final Mark` are short.
`Degenerated GC` or `Full GC` means the concurrent cycle could not keep up.

**Heap trend.** `gc_digest.py` prints occupancy after each collection. A rising floor
means the live set is growing (leak, cache growth, or simply more data). A flat floor
with rising pause times points at the collector or the machine.

**Not supported:** JDK 8 `-XX:+PrintGCDetails` format. If the bundle has that, say so;
do not attempt to read the numbers by eye and mix them with unified-log figures.

---

## 3. nmon

`nmon_digest.py` summarises the standard sections. What matters, and why:

| section | field | significance for Ignite |
|---|---|---|
| `CPU_ALL` | `Idle%` near 0 | starvation; safepoints get slow |
| `CPU_ALL` | `Wait%` high | I/O bound - checkpoint or WAL is the usual source |
| `CPU_ALL` | `Steal%` > 0 | the hypervisor is taking time; the JVM cannot see this |
| `MEM` | `memfree` collapsing | page cache and heap competing |
| `MEM` | `swapfree` dropping | **swap in use - the strongest predictor of a TTSP stall** |
| `MEM` | `swapcached` rising | pages actively moving to/from swap |
| `DISKBUSY` | near 100% | the device is saturated; fsync latency follows |
| `NET` | throughput collapsing | correlates with, but does not prove, a network fault |

The sampling interval (`AAA,interval`) is usually 30-60 s. **A 22-second stall can hide
between two samples.** Never conclude "nmon shows nothing" for an event shorter than the
interval; state the resolution limit instead.

---

## 4. dmesg and messages

`os_digest.py` extracts the patterns that matter:

- **OOM killer** - `Out of memory: Kill process`, `oom-kill:`, `Killed process`. If the
  JVM was killed by the kernel, the Ignite log simply stops - no shutdown messages, no
  failure handler. A truncated log with no farewell is the signature.
- **Memory reclaim pressure** - `page allocation stalls for Nms`, `kswapd`,
  `page allocation failure`. Direct evidence for the TTSP explanation.
- **Hung tasks** - `INFO: task java:NNN blocked for more than N seconds`, with a call
  trace. If the trace contains `swap_readpage`, `folio_wait_bit`, or `io_schedule`, the
  thread was waiting on I/O - frequently swap.
- **Network** - NIC resets, `Link is Down`, `TX unit hang`, ring buffer overruns, TCP
  `Possible SYN flooding`, retransmit counters. Needed to support any "network problem"
  claim.
- **Storage** - `I/O error`, controller resets, multipath failover, `blk_update_request`.
  These explain checkpoint fsync spikes.
- **Clock** - NTP steps and `clocksource` changes. A clock jump invalidates cross-node
  timestamp comparison.
- **systemd** - service exit and restart lines tell you how the process ended and whether
  something restarted it, which the Ignite log cannot.

Remember `dmesg` is monotonic-since-boot; `messages` usually carries the same kernel
lines with wall clock. When they overlap, use `messages` for timing and `dmesg` for the
full call traces.

---

## 5. Thread dumps

One instant in time. Check its timestamp against your window before using it.

`threaddump_digest.py` groups threads by state and by the top frames, and identifies lock
owners. What to look for:

- **Many threads `BLOCKED` on one monitor** - find the owner; that thread's stack is the
  bottleneck. On Ignite this is often a checkpoint or page-memory lock.
- **Ignite pool saturation** - all `sys-stripe-*`, `pub-*` or `data-streamer-*` threads
  busy or blocked means the pool is exhausted; work is queueing behind whatever they are
  stuck on.
- **The discovery threads.** `tcp-disco-msg-worker` blocked or absent is directly
  relevant to segmentation. A healthy `tcp-disco-msg-worker` in `Net.poll` is normal.
- **`db-checkpoint-thread`** in a `force`/`fsync` native frame confirms an I/O stall.
- Threads in `Unsafe.park` inside `GridFutureAdapter.get0` are *waiting for something
  else* - follow what they wait on, do not report them as the problem.

A single dump cannot show a stall that had already ended. If the dump was taken after the
JVM resumed, it will look healthy - which is itself worth stating.
