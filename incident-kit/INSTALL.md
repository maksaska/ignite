# Installing the kit

These instructions are written so an LLM CLI can execute them. If you are that CLI: work
through the sections in order, run the verification at the end, and report which install
path you took.

---

## 0. Prerequisites

```sh
python3 --version     # 3.8 or newer; on Windows the launcher may be `python` or `py`
```

Nothing else is required. Every script is stdlib-only - **do not run `pip install`.**

`jfr` (from the JDK) is needed only for Phase 4, and only if the incident bundle contains
a recording. Its absence does not block anything else.

---

## 1. Get the kit onto the machine

```sh
git clone <repo-url> ignite-src
cd ignite-src
git checkout <branch>
export KIT="$PWD/incident-kit"
```

Only `incident-kit/` matters. The surrounding repository is also useful as the Ignite
source tree for `tools/build_index.py`, but the kit itself is self-contained.

---

## 2. Verify the kit before installing it

```sh
python "$KIT/skills/ignite-incident/scripts/selftest.py"
```

Expect `88 checks, 0 failed`. If anything fails, fix that before continuing - a failing
parser will silently produce a wrong digest later. `--keep` preserves the generated
output for inspection.

---

## 3. Install the skill

The skill is a directory containing `SKILL.md` (with YAML frontmatter), `references/`,
`scripts/` and `samples/`. Install it wherever your CLI discovers skills.

### Claude Code

```sh
mkdir -p ~/.claude/skills
cp -r "$KIT/skills/ignite-incident" ~/.claude/skills/
# or, to track updates from the repo:
ln -s "$KIT/skills/ignite-incident" ~/.claude/skills/ignite-incident
```

Project-scoped alternative: `.claude/skills/ignite-incident` inside the working directory.

### opencode

opencode discovers skills in its config directory and in the project. Try, in order:

```sh
mkdir -p ~/.config/opencode/skill
cp -r "$KIT/skills/ignite-incident" ~/.config/opencode/skill/
```

or project-scoped: `.opencode/skill/ignite-incident/`.

**Verify** that it was actually picked up - list the available skills in your CLI and
look for `ignite-incident`. If it is not there, the layout for your version differs; use
the universal fallback in section 4 instead of guessing.

### gigacode

**I do not know gigacode's skill-discovery convention.** Do not guess. Do this:

1. Check its documentation or `--help` for how it loads skills, custom commands, or
   project instructions (look for `skill`, `command`, `AGENTS.md`, or a config directory).
2. If it has a skills mechanism, install `skills/ignite-incident/` the same way as above
   and verify it is listed.
3. If it does not, or you cannot confirm, **use the universal fallback below.** The
   fallback works everywhere and loses nothing except the automatic triggering.

---

## 4. Universal fallback (works with any CLI)

Skills are a convenience; the content works as plain files. Put an instruction file at the
root of the incident workspace so the CLI reads it as project context:

```sh
cd /path/to/incident
cp "$KIT/skills/ignite-incident/SKILL.md" ./AGENTS.md   # or CLAUDE.md, per your CLI
```

Then edit the copy so the reference paths are reachable from the workspace - either point
them at `$KIT/skills/ignite-incident/references/...`, or copy that directory in:

```sh
cp -r "$KIT/skills/ignite-incident/references" .
cp -r "$KIT/skills/ignite-incident/scripts" .
```

Starting a session with an explicit instruction also works with no installation at all:

> Read `<KIT>/skills/ignite-incident/SKILL.md` and follow it. Start with Phase 0.

---

## 5. Build the indexes for the version you are analysing

The kit ships indexes for whatever version they were last built against. Check:

```sh
ls "$KIT/indexes"
```

If the incident's Ignite version is not there, build it. Check out the matching release
branch first - message wording and line numbers drift between versions:

```sh
git -C /path/to/ignite checkout 2.16.0
python "$KIT/tools/build_index.py" --repo /path/to/ignite --version 2.16.0 --out "$KIT/indexes"
```

Expect roughly 12 000 message entries, 200+ system properties, and 80 timeout knobs, in
about ten seconds. Verify with a message you know exists:

```sh
grep -F 'Local node SEGMENTED' "$KIT/indexes/2.16.0/messages.tsv"
```

That should print one row ending in `GridDiscoveryManager.java` and a line number. If it
prints nothing, the scan did not cover the right source roots - check `--repo` points at
the tree containing `modules/`.

For a fork with private plugins, run it against the fork's tree so plugin messages are
indexed too.

---

## 6. Set up a workspace for an incident

```sh
BUNDLE=/path/to/incident
mkdir -p "$BUNDLE/analysis"
cp -r "$KIT/templates/workspace/." "$BUNDLE/analysis/"
```

---

## 6b. First run against a real bundle: check the parsers can read it

Before any analysis, and before trusting a single digest:

```sh
python "$KIT/skills/ignite-incident/scripts/identify.py" "$BUNDLE" \
    --out "$BUNDLE/analysis/00-inventory.md" \
    --json "$BUNDLE/analysis/inventory.json"

python "$KIT/skills/ignite-incident/scripts/preflight.py" \
    --inventory "$BUNDLE/analysis/inventory.json" \
    --out "$BUNDLE/analysis/00.5-preflight.md"
```

Exit 0 = every file parsed. 2 = something parsed only partially. 1 = something failed, or
files are still unclassified.

**Expect a non-zero exit on the first real bundle.** Site formats vary, and this kit has
only ever seen synthetic fixtures. That is what the overlay mechanism is for: read
`$KIT/skills/ignite-incident/references/90-when-scripts-fail.md` and work the ladder -
`--diagnose`, read the file within the stated limits, write a regex into
`site-patterns.json`, then re-run preflight **and** `selftest.py`.

`$KIT/skills/ignite-incident/samples/alien-site-patterns.json` is a complete worked
example that repairs a bundle with a different log4j layout, RFC5424 syslog framing and
renamed nmon sections. Copy its shape.

The rule that matters: **a file that did not parse cannot support any claim.** Not "no
errors were found", not "this rules out memory pressure" - only "this file could not be
read", recorded under Collection gaps.

## 7. Post-install smoke test

Run the skill against the bundled fixtures, end to end. This exercises the real path
without touching any sensitive data:

```sh
S="$KIT/skills/ignite-incident"
T=$(mktemp -d)
python "$S/scripts/identify.py"       "$S/samples/incident" --out "$T/00-inventory.md" --json "$T/inventory.json"
python "$S/scripts/ignite_timeline.py" --inventory "$T/inventory.json" --out "$T/10.md" --json "$T/timeline.json"
python "$S/scripts/gc_digest.py"       --inventory "$T/inventory.json" --node node03 --out "$T/20-gc.md" --json "$T/gc.json"
python "$S/scripts/preflight.py"        --inventory "$T/inventory.json" --out "$T/00.5.md"
python "$S/scripts/correlate.py"       --analysis "$T" --out "$T/30.md" --year 2024
grep -c "READ THIS BEFORE BLAMING GC" "$T/20-gc.md"     # expect 1
grep -c "100.0%" "$T/00.5.md"                           # expect 11 (all fixtures parse)
```

`identify.py` exits **2** here on purpose - the fixtures include one deliberately
unclassifiable file to exercise the Phase 0 gate.

If you want to check the whole procedure, point your CLI at `$S/samples/incident` and ask
it to run the skill from Phase 0. The correct conclusion is: a slow checkpoint drove
memory pressure, which caused a 21-second time-to-safepoint stall (not a GC pause), which
stopped discovery heartbeats, which caused the coordinator to evict node03, which then
halted itself per the configured segmentation policy. A run that blames GC or the network
means the skill was not loaded, or was not followed.

---

## 8. Report what you did

State: which install path was used (skill directory or fallback), whether the CLI lists
the skill, the selftest result, which index versions are available, and whether `jfr` is
on PATH.
