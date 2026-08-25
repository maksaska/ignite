# Going from a log line to Ignite source, cheaply

**Never grep the Ignite repository.** The indexes exist so you don't have to.

---

## The lookup

```sh
IDX="$KIT/indexes/<version>"
grep -F 'Unable to await partitions release latch' "$IDX/messages.tsv"
```

Output is tab-separated:

```
literal <TAB> kind <TAB> file <TAB> line
```

`kind` tells you how the string reaches the log:

| kind | meaning |
|---|---|
| `log_info`, `log_warn`, `log_warning`, `log_error`, `log_debug` | direct logger call. `IgniteLogger` uses `warning()`; slf4j-style `warn()` also appears |
| `util_warn`, `util_error`, `util_quietAndWarn`, ... | via `U.` / `LT.` helpers. `LT.` throttles repeats - a message you see once may have occurred many times |
| `exception:XxxException` | the message is an exception's text; it reaches the log through a stack trace |
| `format` | built with `String.format` - the literal is a template, so match on the fixed part before the first `%` |
| `assign:<var>` | assigned to a variable/constant then logged elsewhere. The file:line is the *declaration*; the logging call is nearby |

### Picking the search string

Use a fragment with **no runtime values in it**. Log lines interleave literal and
variable text:

```
Failed to send message to next node [msg=TcpDiscoveryMetricsUpdateMessage [...], next=TcpDiscoveryNode [id=...]]
^--------- literal ----------------^ ^-- runtime --^
```

Search `Failed to send message to next node`, not the whole line.

If a message is assembled with `String.format`, the literal in the index still contains
the `%s`/`%d` placeholders, so match the leading fixed text:

```sh
grep -F 'Throttling is applied to page modifications' "$IDX/messages.tsv"
```

### Multiple hits

Common. Ignite logs the same phrase from several places (e.g. `Checkpoint started`
appears in `Checkpointer` and in `IgniteSnapshotManager`). Read all hits, and pick the
one whose class matches the **logger name in the log line itself** - the log line's
`[Category]` field is the class. That resolves it immediately.

---

## What to read once you have file:line

Read the **enclosing method only**. A narrow range is usually enough:

```sh
sed -n '1950,2000p' "$REPO/modules/core/src/main/java/org/.../GridDhtPartitionsExchangeFuture.java"
```

What you are looking for, in order of usefulness:

1. **The condition that produced the message** - what had to be true. This is often the
   whole answer.
2. **The threshold or timeout involved**, and where its value comes from.
3. **What happens next** in the code - which tells you what to expect in the log after
   this line, and whether its absence is meaningful.

You almost never need to understand the whole class. Resist reading upward.

---

## Configuration knobs

```sh
grep -i 'failuredetection' "$IDX/timeouts.tsv"
grep -i 'IGNITE_DISCOVERY' "$IDX/sysprops.tsv"
```

`timeouts.tsv`: `name, kind (constant|setter), default, file, line`
`sysprops.tsv`: `name, value, type, default, description, file, line`

**Always compare the default against what the incident actually ran.** Sources for the
effective value, best first:

1. The Ignite log's startup echo of `IgniteConfiguration [...]` - this is what the node
   really used.
2. The config XML/properties in the bundle.
3. JVM flags (`-DIGNITE_*` system properties override).
4. The index default - only when none of the above is available, and say so.

A recurring trap: `IgniteConfiguration.failureDetectionTimeout` is an umbrella that only
applies when the SPI-specific timeouts are **not** set explicitly. If
`TcpDiscoverySpi.socketWriteTimeout` is set, that value wins for socket writes and the
umbrella figure in the log message is misleading.

---

## Version matching

The index directory is named for the version it was built from. Check it against the
version in `00-inventory.md`.

- **Same version** - line numbers and wording are exact.
- **Different minor version** - class names and message wording are usually stable across
  2.x; line numbers are not. Cite the class and method, not the line.
- **A fork with private plugins** - a message that is not in the index may come from
  plugin code. Say so rather than forcing a match to an Apache class, and look for the
  plugin's own sources if they are available.

If the index and the bundle disagree on version, state that in the report before citing
any line number. Rebuild the index against the right branch if you can:

```sh
python "$KIT/tools/build_index.py" --repo /path/to/ignite --version 2.16.0 --out "$KIT/indexes"
```
