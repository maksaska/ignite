# 20 - Resource findings (Phase 2)

> Paste in the relevant parts of the gc / os / nmon / thread-dump digests, then answer
> the questions each digest's gate section asks. Read `references/40-jvm-and-os.md`
> before interpreting anything here.

## JVM: GC and safepoints

- Collector / JDK:
- Total application stop time in the window:
- TTSP vs at-safepoint split:
- **Was this a GC pause or a time-to-safepoint stall?**

## Machine: CPU, memory, disk, network

- nmon sample interval (and whether it can resolve the event):
- Memory / swap:
- CPU:
- Disk:
- Network:

## Kernel and system logs

- OOM / reclaim / hung tasks:
- Network errors:
- Storage errors:
- Clock changes:
- Service lifecycle:

## Thread dump

- Taken at (relative to the incident window):
- Contention and lock owners:
- Discovery threads healthy?

## Verdict

> **Does the resource evidence explain the Phase 1 timeline?**
> yes / no / partly - and why:

<If "partly", state precisely what remains unexplained.>

## Gate

- [ ] Verdict written
- [ ] GC vs TTSP question answered explicitly
- [ ] Resolution limits noted where they matter
