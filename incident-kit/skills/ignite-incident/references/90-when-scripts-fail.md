# When a script cannot read a file

Site log formats vary and this kit cannot anticipate all of them. When `preflight.py`
reports `DEGRADED` or `FAILED`, or a digest shows a parse problem, work this ladder in
order. Stop as soon as the verdict is `OK`.

## The rule that governs everything here

**A low parse rate is a tooling problem. It is never a finding.**

If a file did not parse, you may not write any of these:

- "no kernel events were found"
- "nmon shows nothing unusual"
- "the GC log contains no long pauses"
- "this rules out a memory problem"

You may only write: *"`<file>` could not be parsed, so nothing is claimed from it."*
That sentence belongs in the report, in the "Collection gaps" section.

---

## Step 1 - Ask the script what it saw

```sh
python <script>.py --inventory analysis/inventory.json --diagnose
```

Every digest supports `--diagnose`. It prints, per file: lines read, lines parsed, the
parse rate, and **the first few lines that did not match, verbatim**. Read those first.
They are usually enough to see the difference, and they cost you almost nothing.

Two different problems, distinguished by what `--diagnose` says:

| what you see | what it means | what to fix |
|---|---|---|
| low parse rate, unparsed lines shown | the LINE GRAMMAR differs - different log4j layout, different syslog framing | `ignite_line_layouts` / the file's line format |
| parse rate 100%, but preflight's completeness check shows gaps, or the digest is thin | the lines parse but the CONTENT patterns miss this site's wording | `ignite_events` / `os_patterns` |

Fixing the wrong one of those wastes a cycle, so check which it is before writing anything.

## Step 2 - Read the file, within limits

Only if Step 1 was not enough.

```sh
head -200 <file>
tail -200 <file>
sed -n '5000,5200p' <file>     # a slice from the middle
```

**Cap: about 1000 lines per file.** Sample the middle as well as the ends - a rotated or
concatenated log can change format part-way through, and the ends will not show you that.

This is the one place in the whole workflow where reading raw log lines is correct. It is
bounded, it has a clear stopping condition (you can describe the grammar), and it is not
a substitute for the digests. When you can write the regex, stop reading.

## Step 3 - Write the fix into the overlay, not the script

Create or edit `site-patterns.json` in the analysis workspace:

```json
{
  "ignite_line_layouts": [
    "^(?P<date>\\d{4}-\\d{2}-\\d{2}) (?P<time>\\d{2}:\\d{2}:\\d{2}),(?P<ms>\\d{3}) +(?P<level>[A-Z]+) +\\[(?P<thread>[^\\]]*)\\] +(?P<cat>\\S+) - (?P<msg>.*)$"
  ],
  "os_patterns": [
    ["memory_reclaim", 3, "under sustained pressure, reclaim latency", "vendor wording for reclaim"]
  ],
  "nmon_aliases": {"CPU_ALL": ["PCPU_ALL"], "MEM": ["MEMNEW"]}
}
```

Sections and what each extends:

| section | shape | extends |
|---|---|---|
| `file_signatures` | `{kind: [[regex, weight], ...]}` | how `identify.py` classifies files; weights sum, 6 classifies |
| `ignite_line_layouts` | `[regex]` with named groups `date time ms level thread cat msg` | the Ignite log line grammar |
| `ignite_events` | `[[category, severity 1-3, regex]]` | which messages count as events |
| `os_patterns` | `[[label, severity, regex, why]]` | which kernel/syslog lines matter |
| `nmon_aliases` | `{canonical: [alias, ...]}` | non-standard nmon section names |
| `safepoint_formats` | `[regex]` with named groups `ttsp at total` (optional `op`, `unit`) | extra safepoint grammars |

Overlay patterns are tried **before** the built-ins, and every digest names the overlay in
its header so a reader always knows a site-local rule was in play.

A worked example lives in `samples/alien/site-patterns.json`: it repairs a bundle with a
different log4j layout, RFC5424 syslog framing and renamed nmon sections. Copy its shape.

Then re-run with `--patterns`:

```sh
python preflight.py --inventory analysis/inventory.json --patterns analysis/site-patterns.json
```

(Scripts also pick up `site-patterns.json` automatically if it sits beside
`inventory.json`. They will not search anywhere else - an overlay that loaded depending on
where you ran from would change results invisibly.)

## Step 4 - Verify. Both checks, every time.

```sh
python preflight.py --inventory analysis/inventory.json --patterns analysis/site-patterns.json
python selftest.py
```

1. **Preflight verdict must improve.** If it did not, your regex does not match; go back
   to Step 1 rather than adding more patterns on top.
2. **`selftest.py` must still pass with 0 failures.** This is what proves your fix did not
   break the paths that were already working. A regex that repairs one site's log while
   silently breaking the standard layout is worse than the original problem, because
   nothing downstream would notice.

If the overlay is malformed, the scripts stop with an error naming the bad pattern. They
never ignore an overlay silently - an overlay you believe is active but is not is the
worst of both worlds.

## Step 5 - Only now, edit a script

If the overlay genuinely cannot express the format (a new artifact type, a structural
difference rather than a pattern difference):

1. Make the smallest change that handles the new case **in addition to** the existing ones.
2. Run `selftest.py`. If it drops below its previous count, **revert your edit**. Do not
   adjust the test to match your change - the test encodes behaviour that was verified
   against known-good fixtures.
3. Note in the report that a script was modified, and which one.

## Step 6 - Degraded mode

If the ladder runs out:

1. Analyse that artifact **by hand**, reading it within the Step 2 limits.
2. In `50-report.md`, under "Collection gaps", record: which file, why it could not be
   parsed, what you read manually, and what you did **not** claim as a result.
3. Continue with the other artifacts. One unreadable file does not block the analysis - it
   narrows what the analysis can conclude, which is a different thing and must be stated.

---

## Known-unsupported formats

These are not bugs and no overlay will fix them:

| format | why | what to do |
|---|---|---|
| JDK 8 GC logs (`-XX:+PrintGCDetails`) | multi-line, structurally different from unified logging | state that GC could not be analysed; the safepoint line (`Total time for which application threads were stopped`) may still be readable by hand |
| Binary or compressed files | not text | expand them (`zcat`, `unzip`) and re-run `identify.py` |
| JFR | not a text format | use `jfr_query.py` in Phase 4 |

## If preflight itself will not run

It imports the digest modules, so it needs the whole `scripts/` directory together. If it
fails on import, the install is incomplete - re-copy the skill directory rather than
working around it.
