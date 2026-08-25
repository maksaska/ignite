# Anatomy of an Ignite node log

## Line layout

The default log4j2 layout is:

```
[2024-03-14T10:22:41,690][INFO ][disco-event-worker-#61][GridDiscoveryManager] Node FAILED: ...
 ^ timestamp             ^level  ^ thread                ^ logger = the class    ^ message
```

Two things to use constantly:

- **The logger field is the emitting class.** When `messages.tsv` returns several hits for
  a phrase, this field disambiguates immediately.
- **The thread name tells you the subsystem** (see the table below). A message from
  `tcp-disco-msg-worker` is about the discovery ring; the same words from another thread
  might mean something else entirely.

Layouts are configurable and sites change them. `ignite_timeline.py` falls back to a
timestamp-only parse when the layout differs. Lines with no timestamp are continuations
(stack traces, multi-line dumps) and belong to the line above.

**The timestamp is the JVM's default timezone and the log does not record which one.**

## Thread names worth recognising

| thread | subsystem |
|---|---|
| `tcp-disco-msg-worker`, `tcp-disco-msg-worker-[crd]` | discovery ring message worker; `[crd]` marks the coordinator |
| `tcp-disco-sock-reader`, `tcp-disco-srvr` | discovery sockets |
| `disco-event-worker` | applies discovery events locally (topology snapshots, SEGMENTED) |
| `exchange-worker` | partition map exchange |
| `db-checkpoint-thread` | checkpointing |
| `wal-write-worker`, `wal-file-archiver` | write-ahead log |
| `sys-stripe-N` | the striped system pool - cache operations |
| `grid-nio-worker-tcp-comm` | communication SPI I/O |
| `jvm-pause-detector-worker` | `LongJVMPauseDetector` |
| `ttl-cleanup-worker`, `grid-timeout-worker` | housekeeping; useful as a liveness heartbeat |

A stalled JVM stops *all* of these at once. If only one is missing from an otherwise busy
log, that is a per-thread problem.

---

## Startup: what to harvest

The banner and the lines just after it give you most of Phase 0's environment section:

```
>>> ver. 2.16.0#20231215-sha1:d2c82c0f      <- exact build; use it to pick the index
>>> OS name: ...   >>> CPU(s): ...   >>> Heap: ...GB   >>> VM name: ...
>>> Local node [ID=<uuid>, order=N, clientMode=false]
>>> Local node addresses: [...]
>>> Local ports: ...
```

Also logged at startup: the effective `IgniteConfiguration [...]`. **This is the
authoritative record of what the node actually ran with** - better than the config file
in the bundle, which may not be the one that was loaded. Harvest
`failureDetectionTimeout`, `clientFailureDetectionTimeout`, `systemWorkerBlockedTimeout`,
the failure handler and the segmentation policy from it.

Node identity: the `ID` is a UUID; logs elsewhere abbreviate it to the first 8 characters
(`locNode=c3d4e5f6`). `consistentId` is the stable name across restarts - use it when
talking about "the node" and the UUID when matching log lines.

---

## Topology snapshots

```
Topology snapshot [ver=5, locNode=a1b2c3d4, servers=3, clients=0, state=ACTIVE, CPUs=96, offheap=144.0GB, heap=96.0GB]
  ^-- Baseline [id=0, size=4, online=3, offline=1]
```

- `ver` increments on every membership change; the sequence is the cluster's own account
  of who joined and left.
- `servers` dropping by one is a node leaving - cross-reference with the `Node FAILED` /
  `Node LEFT` line immediately before it.
- **`Node FAILED` vs `Node LEFT`**: FAILED means detected as unresponsive; LEFT means it
  said goodbye. A clean shutdown produces LEFT. This distinction is often the first
  branch in an investigation.
- The `Baseline` line with `offline > 0` means baseline nodes are missing - relevant to
  whether rebalancing will occur and whether the cluster still accepts writes.
- `state=INACTIVE` means the cluster is not serving; check for a deactivation event.

Every node prints its own snapshot for the same version. Comparing them across nodes
detects split-brain: two different memberships at the same `ver`.

---

## Message groups you will meet

| message | class | see |
|---|---|---|
| `Local node SEGMENTED` | `GridDiscoveryManager` | `30-failure-modes.md` section 1 |
| `Node is out of topology` | `ServerImpl` | section 1 - a self-diagnosis, not network evidence |
| `Socket write has timed out` | `TcpDiscoverySpi` | section 1 |
| `Failed to send message to next node` | `ServerImpl` | section 1 |
| `Local node has detected failed nodes` | `ServerImpl` | section 1 |
| `Critical system error detected` | `FailureProcessor` | section 1, section 2 - read the `hnd=` and `failureCtx=` fields |
| `JVM will be halted immediately` | `StopNodeOrHaltFailureHandler` | section 1 |
| `Blocked system-critical thread` | `WorkersRegistry` | section 2 |
| `Possible too long JVM pause: N milliseconds` | `LongJVMPauseDetector` | section 3 - **not a GC measurement** |
| `Unable to await partitions release latch` | `GridDhtPartitionsExchangeFuture` | section 4 - read `pendingAcks` |
| `Failed to wait for partition map exchange` | `GridCachePartitionExchangeManager` | section 4 |
| `Checkpoint started` / `finished` | `Checkpointer` | section 5 - read the phase breakdown |
| `Throttling is applied to page modifications` | `PagesWriteSpeedBasedThrottle` | section 5 |

---

## Reading gaps

A stalled JVM writes nothing, so the evidence of a stall is the **absence** of lines.
`ignite_timeline.py` reports every silence above the threshold.

Judge a gap against the node's own baseline chatter. A node that normally logs a metrics
line every 60 seconds has a meaningful gap at 90 seconds; a quiet node may legitimately
say nothing for ten minutes. Compare the gap with:

- the same node's gaps earlier in the log (its normal rhythm),
- other nodes over the same wall-clock period (did they keep logging?),
- `failureDetectionTimeout` (did the gap exceed it?).

A gap on **all** nodes at once is not a node stall - look for a clock jump, a log
collection artifact, or a whole-cluster event.

---

## Log rotation and truncation

Check `00-inventory.md` for each file's time range. Watch for:

- Ranges that do not abut - a segment is missing, and it may be the interesting one.
- A file that ends abruptly with no shutdown sequence - the process died hard (see
  `10-evidence-map.md`, "Absence as evidence").
- `IGNITE_QUIET=true` (the default when not overridden) sends much of the startup output
  to stdout rather than the log file. If the banner is missing, look for a
  `console`/`stdout` capture in the bundle.
