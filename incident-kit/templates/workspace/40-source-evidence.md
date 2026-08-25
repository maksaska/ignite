# 40 - Source and JFR evidence (Phase 4)

> Only the questions written in Phase 3. If you are exploring, the question was not
> sharp enough - go back.

## Source lookups

### Log line: `<the exact line>`

- Index hit: `<file>:<line>` (kind: `<log_warn|util_error|...>`)
- Index version: `<version>` / bundle version: `<version>` - **match? yes/no**
- Condition that produces it:
- Governing threshold / knob:
- Default vs configured value in this incident:
- What this tells us:

## Configuration knobs checked

| knob | default | configured | source of the configured value |
|---|---|---|---|

## JFR queries

### Question: `<verbatim from Phase 3>`

- Window:
- Event types:
- Result:
- **Confirms / refutes / fails to discriminate:**

## Gate

- [ ] Every lookup traces back to a Phase 3 question
- [ ] Version match between index and bundle stated
- [ ] Nil results recorded as results
