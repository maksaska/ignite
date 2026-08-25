# Going from a log line to source, cheaply

**Never grep the source repositories.** The index exists so you don't have to.

There is exactly one index set per analysis, built by `kit.py init` from the repositories
the user named, and it lives beside the incident:

```sh
IDX="$BUNDLE/analysis/indexes"
```

It covers **all** the repositories that make up the product — typically Apache Ignite,
`ignite-extensions`, and private code — merged into one table. A message that exists only
in the private tree resolves here just like an Apache one.

---

## The lookup

```sh
grep -F 'Unable to await partitions release latch' "$IDX/messages.tsv"
```

Output is tab-separated:

```
repo <TAB> literal <TAB> kind <TAB> file <TAB> line
```

`kind` tells you how the string reaches the log:

| kind | meaning |
|---|---|
| `log_info`, `log_warn`, `log_warning`, `log_error`, `log_debug` | direct logger call. `IgniteLogger` declares `warning()`; slf4j-style `warn()` also appears |
| `util_warn`, `util_error`, `util_quietAndWarn`, ... | via `U.` / `LT.` helpers. `LT.` throttles repeats — a message you see once may have occurred many times |
| `exception:XxxException` | the message is an exception's text; it reaches the log through a stack trace |
| `format` | built with `String.format` — the literal is a template, so match the fixed part before the first `%` |
| `assign:<var>` | assigned to a variable/constant, logged elsewhere. The `file:line` is the *declaration*; the logging call is nearby |

### Picking the search string

Use a fragment with **no runtime values in it**:

```
Failed to send message to next node [msg=TcpDiscoveryMetricsUpdateMessage [...], next=...]
^--------- literal ----------------^ ^-- runtime --^
```

Search `Failed to send message to next node`, not the whole line.

### Multiple hits

Two different situations, and the difference matters:

**Same repo, several places.** Ignite logs the same phrase from more than one class (e.g.
`Checkpoint started` appears in `Checkpointer` and `IgniteSnapshotManager`). Resolve it
with the log line's own `[Category]` field — that field *is* the emitting class.

**Different repos, same literal.** The private tree or an extension has its own copy of
the message. That is a signal, not noise: it usually means the fork overrode an Apache
class, and the code that actually ran is the fork's. Check which one is on the classpath
before citing either. If you cannot tell, say so and cite both.

---

## Before you cite a line number

Read `$IDX/INDEX-INFO.md`. Phase 0.5 already summarises it, but check it yourself:

- **Version mismatch** between the indexed source and the bundle's banner → class and
  method names are usually stable across 2.x, **line numbers are not**. Cite the class and
  method. Better: ask the user to check out the matching release and re-run `kit.py init`.
- **Dirty repository** → the line numbers describe someone's working copy, not any build
  that ran in production. Say so, or have it committed/stashed and rebuilt.

This is not pedantry. The same message in `GridDiscoveryManager` sits at a different line
on `master` than on a release branch; a citation from the wrong checkout points at
unrelated code and reads perfectly plausibly.

---

## What to read once you have `file:line`

Read the **enclosing method only**:

```sh
sed -n '1950,2000p' "<repo>/<file from the index>"
```

Looking for, in order of usefulness:

1. **The condition that produced the message** — what had to be true. Often the whole answer.
2. **The threshold or timeout involved**, and where its value comes from.
3. **What happens next** in the code — which tells you what to expect in the log after
   this line, and whether its absence is meaningful.

You almost never need the whole class. Resist reading upward.

---

## Configuration knobs

```sh
grep -i 'failuredetection' "$IDX/timeouts.tsv"
grep -i 'IGNITE_DISCOVERY' "$IDX/sysprops.tsv"
```

`timeouts.tsv`: `repo, name, kind (constant|setter), default, file, line`
`sysprops.tsv`: `repo, name, value, type, default, description, file, line`

**Always compare the default against what the incident actually ran.** Sources for the
effective value, best first:

1. The Ignite log's startup echo of `IgniteConfiguration [...]` — what the node really used.
2. The config XML/properties in the bundle.
3. JVM flags (`-DIGNITE_*` system properties override).
4. The index default — only when none of the above is available, and say so.

A recurring trap: `IgniteConfiguration.failureDetectionTimeout` is an umbrella that applies
only when the SPI-specific timeouts are **not** set explicitly. If
`TcpDiscoverySpi.socketWriteTimeout` is set, that value wins for socket writes and the
umbrella figure quoted in the log message is misleading.

---

## If a message is not in the index at all

In order of likelihood:

1. **The repository it lives in was not indexed.** Check `INDEX-INFO.md` for which repos
   were included; ask the user to re-run `kit.py init` with the missing one.
2. **It is assembled at runtime** from fragments too short to index (the minimum literal
   length is 12 characters). Search a longer neighbouring fragment from the same line.
3. **It comes from a library, not the product** — Spring, Log4j, the JDK.

Say which of these you concluded. "Not found in the index" on its own tells the reader
nothing.
