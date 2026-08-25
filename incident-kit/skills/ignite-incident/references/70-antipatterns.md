# Antipatterns

Check your report against every item here before submitting it. These are the specific
ways this analysis goes wrong, not general advice.

---

## Reasoning

**1. Taking a symptom from the middle of the chain as the cause.**
"The node segmented because of a network problem." Segmentation is the *last* step of a
long chain (see `30-failure-modes.md` section 6). Ask what produced the thing you named, and
keep asking until the answer is outside Ignite - load, capacity, configuration, hardware.

**2. Blaming GC for a JVM pause.**
`Possible too long JVM pause` measures lost wall-clock, not collector work. If
`gc_digest.py` shows the time went into *reaching* the safepoint, GC is innocent. Never
write "GC pause" unless the at-safepoint figure supports it.

**3. Reading only the node that died.**
Its log contains the consequence. The decision is on the coordinator and the ring
predecessor. If your timeline has one node in it, you have not done Phase 1.

**4. Treating a silent log as an uneventful period.**
A gap is evidence - often the strongest evidence available, because a stalled process
writes nothing. `ignite_timeline.py` reports gaps explicitly; do not skip that table.

**5. Correlation stated as causation.**
A GC pause 40 seconds before the failure, with no mechanism connecting them, is a
coincidence. State the mechanism or state the uncertainty.

**6. Stopping at the first plausible cause.**
Once you have a candidate, ask what else it predicts you would see, and check whether you
see it. A cause that explains one line and contradicts three others is wrong.

**7. Forcing the evidence into a known failure mode.**
`30-failure-modes.md` is a catalogue, not a constraint. If the incident does not match
anything in it, say so. A wrong named diagnosis is worse than an accurate description.

**8. Ignoring what is absent.**
No `Blocked system-critical thread` during a 22-second stall is informative: either the
watchdog was also stopped, or the timeout is higher than you assume. Absence constrains
hypotheses.

---

## Time

**9. Comparing timestamps across clock domains.**
Ignite logs the JVM default timezone and does not print it. `dmesg` is monotonic since
boot. syslog has no year and no zone. nmon uses the collecting host's local time. JFR is
epoch-based. Establish offsets in Phase 0. A 3-hour timezone difference has been mistaken
for "nothing in the logs at that time" more than once.

**10. Assuming clocks across nodes agree.**
Check for NTP steps in `messages`/`dmesg`. If node A's clock jumped, its ordering
relative to node B is not trustworthy, and the discovery timeouts you compute from it
are wrong.

**11. Using a rotated log's start as the incident start.**
The interesting period may be in the previous file. Check `00-inventory.md` for the time
ranges and whether they abut.

---

## Process

**12. Reading raw logs into context.**
Use the digests. If you find yourself paging through a log, you have skipped a script.

**13. Grepping the source repositories.**
`analysis/indexes/messages.tsv` maps the message to repo, file and line directly, across
every repository that makes up the product. Exploring burns context and finds the wrong
overload.

**14. Citing a line number from the wrong checkout.**
Line numbers drift between branches - the same `Local node SEGMENTED` call sits ten lines
apart on master and on a feature branch, and a citation from the wrong one points at
unrelated code while reading perfectly plausibly. Check `INDEX-INFO.md`: if the indexed
version differs from the bundle's banner, or the repository is dirty, cite the class and
method instead. Phase 0.5 flags both, so there is no excuse for missing it.

**14a. Assuming a message came from Apache Ignite.**
The product is several repositories. If a literal appears in more than one, the fork may
have overridden the Apache class and the code that ran is the fork's. Check the `repo`
column before attributing behaviour to upstream.

**15. Opening JFR without a question.**
JFR is large and will fill your context with samples that answer nothing. Phase 3 must
name the window, the event types, and what result would confirm or refute the hypothesis.

**16. Quoting a default value instead of the configured one.**
`failureDetectionTimeout` defaults to 10 s, but the incident's config may set it, and
setting `TcpDiscoverySpi.socketWriteTimeout` explicitly makes the umbrella value
irrelevant for that operation. Read the config in the bundle; the Ignite log also echoes
the effective `IgniteConfiguration` at startup.

---

## Writing

**17. Confidence not marked.**
Every link in the causal chain needs proven / inferred / speculative. A reader who cannot
tell which is which cannot act on the report.

**18. No falsification path.**
For each remaining unknown, name the check that would settle it. "Needs further
investigation" is not a finding.

**19. Recommendations that do not follow from the analysis.**
"Increase failureDetectionTimeout" when the node was stopped for 22 seconds treats the
detector, not the stall. Recommend against the root cause; if you also suggest a
mitigation, label it as one.

**20. Silently dropping an artifact.**
If you did not use the JFR, the thread dump or a node's logs, say so and why. A reader
must know what was examined and what was not.
