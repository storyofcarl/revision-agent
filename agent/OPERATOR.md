# OPERATOR.md — placeholder (Brief 01)

The operator charter is written in Brief 02. Until then:

- The revision-pipeline skill's operating contract governs every round.
- The vendored references in `agent/references/` are the authority at the
  steps the skill designates (intake-guide, note-schema, routing-and-costs,
  prompt-patterns).
- Tools in `tools/` are independently runnable CLIs — `--help` on each
  documents its contract. The operator runs tools; it never reimplements
  them.
