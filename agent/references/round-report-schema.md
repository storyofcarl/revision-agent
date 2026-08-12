# round_report.json schema

`out/round_report.json` is the machine-readable disposition of one revision
round (spec §8.3). It is **rendered** by `tools/build_round_report.py` from
`revision/notes.json` + `state.json` (+ `job.yaml` for the gate dial) and is
never hand-written or hand-edited. `out/round_report.md` is rendered *from
the JSON*, so the two can never disagree.

## Contents
- Stability contract
- Envelope
- Note entry
- Gate entry
- Verification entry
- Escalation entry
- Totals and cost attribution
- Terminal state
- Validation (`--check`)

## Stability contract

This file is the agency-system consumption contract (spec §10): the producer
workflow reads it for progress, cost actuals, escalations and gate events.
Treat it as an **external API**.

- `report_version` is semver, currently **`1.0.0`**, and is emitted on every
  report.
- **Additive changes only.** New optional keys may be added at any level in a
  MINOR bump. Renaming a key, removing a key, narrowing an enum, or changing
  a value's type is a **MAJOR** bump and requires a consumer migration —
  don't do it inside v1.
- Enums (`terminal_state`, `classification`, `decider`, `outcome`) may gain
  members in a MINOR bump; consumers must tolerate an unknown member rather
  than crash.
- Consumers must ignore unknown keys. Producers must not rely on key order.
- Optional keys marked *(optional)* below may be absent entirely — never
  present as `null` to mean "absent" unless the schema says the field is
  nullable.

## Envelope

```json
{
  "report_version": "1.0.0",
  "job_id": "rev-2026-08-12-blastoff",
  "round": 2,
  "mode": "standalone",
  "terminal_state": "resubmitted",
  "generated_ts": "2026-08-12T14:03:11Z",
  "gate_config": {"lock": "human", "foundation": "human", "stills": "agent"},
  "budget": {"ceiling_usd": 50.0, "estimated_usd": 9.32, "actual_usd": 4.90},
  "notes": [ /* Note entries */ ],
  "round_gates": [ /* Gate entries not attributable to a single note */ ],
  "delegated_gate_approvals": [ /* Gate entries where decider == "agent" */ ],
  "escalations": [ /* Escalation entries */ ],
  "totals": { /* see below */ }
}
```

| Key | Type | Required | Source |
|---|---|---|---|
| `report_version` | semver string | yes | constant in the tool |
| `job_id` | string | yes | `state.job_id` |
| `round` | int | yes | `notes.json.round` |
| `mode` | string | *(optional)* | `state.mode` |
| `terminal_state` | `"resubmitted"` \| `"escalated"` | yes | derived (below) |
| `generated_ts` | ISO-8601 Z string | yes | render time |
| `gate_config` | object | *(optional)* | `job.yaml` `gates:` block |
| `budget` | object | yes | `job.yaml` ceiling + `state.budget` + notes totals |
| `notes` | array | yes | one entry per note in `notes.json` |
| `round_gates` | array | yes | `state.gates` with no note attribution |
| `delegated_gate_approvals` | array | yes | every gate entry with `decider: "agent"` |
| `escalations` | array | yes | `state.escalations` |
| `totals` | object | yes | derived |

## Note entry

```json
{
  "note_id": "N-001",
  "classification": "local",
  "method": 3,
  "method_label": "direct video edit",
  "status": "verified",
  "raw_text": "At 0:05 the jacket should be red, not blue.",
  "interpretation": "Change the jacket color from blue to red in SH001.",
  "resolved_shots": ["SQ001-SC001-SH001"],
  "assets_touched": [],
  "cycles_used": 1,
  "bundle": null,
  "rationale": "cheapest method satisfying motion preservation",
  "gates": [ /* Gate entries attributed to this note */ ],
  "verification": [ /* Verification entries */ ],
  "qc_refs": ["qc/round2/N-001"],
  "escalations": ["repair_exhaustion"],
  "cost": {"est_usd": 3.92, "actual_usd": 1.96}
}
```

| Key | Type | Required | Source |
|---|---|---|---|
| `note_id` | string | yes | note |
| `classification` | `"local"` \| `"foundational"` | yes | `note.scope` |
| `method` | int 1–4 | yes | `note.method` |
| `method_label` | string | yes | derived from `method` (routing-and-costs) |
| `status` | string | yes | `note.status` (note-schema lifecycle) |
| `raw_text`, `interpretation` | string | *(optional)* | note |
| `resolved_shots`, `assets_touched` | array | *(optional)* | note |
| `cycles_used` | int | yes | `note.lineage.revision_cycle`, else candidate count |
| `bundle` | any, nullable | *(optional)* | `note.lineage.bundle` (§5.3 bundling record) |
| `rationale` | string or object | *(optional)* | `note.rationale` pass-through |
| `gates` | array | yes | gate entries attributed to this note |
| `verification` | array | yes | `note.lineage.candidates[]` |
| `qc_refs` | array of strings | *(optional)* | `note.lineage.qc_refs` |
| `escalations` | array of trigger strings | *(optional)* | escalations naming this note |
| `cost.est_usd` | number | yes | `note.est_cost.expected_usd` |
| `cost.actual_usd` | number | yes | ledger attribution (below) |

## Gate entry

Rendered from `state.gates[]` as written by `state.record_gate()`.

```json
{
  "gate": "stills",
  "decider": "agent",
  "outcome": "approved",
  "evidence": ["stills/edited/SQ001-SC001-SH001_0000_v2.png"],
  "rationale": "N-001-c1 satisfied: jacket red in both key stills",
  "ts": "2026-08-12T13:41:07Z",
  "note_id": "N-001"
}
```

`gate`, `decider` (`human`|`agent`), `outcome` (`approved`|`rejected`) and
`evidence` (array) are required; `rationale`, `ts` and `note_id` are optional.

**A delegated (agent) approval MUST carry at least one evidence path** —
spec §5.2 makes a delegated gate auditable, never silent. `--check` enforces
this. Every agent-decided entry also appears in the top-level
`delegated_gate_approvals` array so a consumer can audit delegation without
walking the notes.

**Note attribution:** a gate entry is attached to a note when it carries an
explicit `note_id`, or when a note id appears in its `gate` string (e.g.
`"stills:N-001"`). Otherwise it is a round-level gate and lands in
`round_gates`.

## Verification entry

Gate 4 is machine-owned; the report transcribes verdicts, it never forms
them.

```json
{"candidate": "candidates/SQ001-SC001-SH001_v2.mp4",
 "verdict": "PASS", "ts": "2026-08-12T13:52:00Z",
 "qc_refs": ["qc/round2/N-001/verdict_card.json"]}
```

`candidate` and `verdict` are required (`verdict` may be `null` while a
candidate is still polling — a partial batch is representable). `qc_refs`
collects the candidate's `qc_ref`/`qc_refs` fields.

## Escalation entry

Rendered from `state.escalations[]` as written by `state.escalate()`, plus a
derived `parked` flag.

```json
{"trigger": "budget", "note_id": "N-003", "blocked_on": "ceiling breach",
 "evidence": ["out/estimate.json"],
 "options": ["raise ceiling", "drop N-003", "re-scope N-003"],
 "ts": "2026-08-12T13:10:00Z", "parked": true}
```

`trigger`, `blocked_on`, `evidence`, `options` and `parked` are required;
`note_id` is nullable. `parked` is `true` unless the escalation object
carries `resolved: true` — a resolved escalation stays in the report as
history.

## Totals and cost attribution

```json
{"notes": 3, "addressed": 2, "unresolved": 1,
 "est_usd": 9.32, "actual_usd": 4.90, "unattributed_actual_usd": 0.0,
 "escalations": 1, "parked_escalations": 1,
 "delegated_gate_approvals": 1,
 "tool_invocations": 12, "failed_invocations": 0}
```

- `addressed` counts notes with status `done` or `verified`; `unresolved` is
  every other note. `addressed + unresolved == notes` always.
- `est_usd` is the sum of per-note `cost.est_usd` (rounded to cents) — it is
  a sum of the report's own rows, not a copy of `notes.json.totals`, so the
  report is internally consistent by construction.
- `actual_usd` is `state.budget.actual_usd` — the authoritative running total
  the single tool runner accumulates.
- **Per-note actual attribution:** a ledger entry with a non-zero `cost_usd`
  is attributed to every note whose `note_id` appears in the entry's `argv`,
  split evenly when more than one matches (a bundled submission legitimately
  serves several notes). Cost that names no note — plus any drift between
  the ledger and the running budget total — is `unattributed_actual_usd`,
  computed as the residual. Therefore
  `sum(notes[].cost.actual_usd) + unattributed_actual_usd == actual_usd`
  (±0.01 for rounding), which `--check` enforces.
- `tool_invocations` / `failed_invocations` count `state.ledger` entries and
  those with a non-zero `exit`.

## Terminal state

Derived, never asserted by hand:

- **`escalated`** — any escalation is parked, or `state.position.parked` is
  true. The round is blocked on a human decision.
- **`resubmitted`** — otherwise. The cut went back to the client.

`--check` requires at least one escalation entry when `terminal_state` is
`escalated`, and requires no parked escalation when it is `resubmitted`.

## Validation (`--check`)

```
python tools/build_round_report.py --check out/round_report.json
```

Exit 0 with an `OK:` line, or exit 1 with a JSON error object on stderr:

```json
{"error": "invalid_report", "message": "3 error(s) in out/round_report.json",
 "errors": ["notes[1].method: missing",
            "notes[1].cost.actual_usd: wrong type str",
            "totals.est_usd: 9.32 != sum of note est 5.40"]}
```

Errors are itemized and path-addressed. The checks are exactly the required
fields, enums, and the cross-field invariants stated above (totals sum,
addressed + unresolved, cost attribution sum, terminal-state consistency,
delegated-gate evidence).
