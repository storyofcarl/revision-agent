# notes-brief-02 — the operator layer (2026-08-12)

Brief 02 complete. `bash tests/run_tests.sh` passes: 17 modules, 93 tests,
no network, no spend, `jobs/` clean afterwards. The `--live` tier was not
re-run this session (Brief 01 verified it; nothing here touches an API).

## What was built

| File | What it is |
|---|---|
| `agent/OPERATOR.md` | the round charter — startup/resume, the 9-step execution map with exact CLIs, the six decision procedures, gate protocol, escalation objects, verification wiring, round close, hard rules |
| `tools/state.py` | the state machine: `load/save`, **the single runner** `run_tool`, `start_step`/`complete_step`/`new_round`, `record_gate`, `escalate`, `check_budget`, `batch_update`/`batch_state`, `job_config`; also a CLI (`run`, `show`) |
| `tools/plan_round.py` | `--plan-only`: intake chain → Gate 1 table → escalations, zero spend. Both the user feature and the §6 test surface |
| `tools/build_round_report.py` | renders `out/round_report.json` + `.md` from notes.json + state.json; `--check` validates |
| `agent/references/round-report-schema.md` | the schema and its stability contract (external API, `report_version` 1.0.0, additive only) |
| `tests/test_state.py`, `test_plan_round.py`, `test_build_round_report.py` | 38 tests; all four §6 acceptance criteria assert against artifacts, never by eye |
| `tests/fixtures/plan_round/{notes.json,shots.json}` | the seeded round: 1 Foundational, Locals across all four methods, 1 ambiguous, 1 sub-4s shot, a budget breach at a $1 test ceiling |

Acceptance §6 mapping (each runs standalone):
`test_plan_round.TestScriptedRound` + `TestBudgetEscalation` (1),
`TestDelegatedStillsGate` (2), `TestResumability` (3),
`TestRoundReportTerminalStates` (4), this file (5).

## Defects found and fixed at close-out

1. **`revise.py` ignored `--config budget_ceiling_usd`** when seeding
   `state.budget.ceiling_usd` (hardcoded 50.0), so state.json disagreed with
   job.yaml on the one number that is absolute. Fixed: `ceiling_of()` reads
   the ceiling back out of the job.yaml text actually written.
   `state.check_budget()`'s re-sync from job.yaml stays — it is now redundant
   for fresh jobs and still correct for hand-edited ones.
2. **`state.complete_step()` silently un-parked a round** (it replaced
   `position` wholesale, dropping `parked`). A park is a property of the
   round, not of the step it fired in; only `start_step` (i.e. a resolution)
   clears it now. `plan_round` had been working around this with load-bearing
   call ordering; that ordering is now belt-and-braces rather than required.
3. **No way to open round N+1.** Spec §8 loops new client notes back to
   intake in the same job dir, and OPERATOR.md §6 said so — but with all nine
   steps `completed`, `start_step` refuses everything and the round was a dead
   end. Added `state.new_round(job_dir, summary)`: archives the closed step
   ledger into `state.rounds[]`, resets the nine steps, returns to step 1.
   Spend, gates, escalations and artifacts accumulate; nothing is deleted.
   It refuses while a step is still open. It also sets `state.ledger_from`,
   and `plan_round.ran_ok()` now slices the ledger from there — otherwise
   round 2 would treat round 1's successful `resolve_timecodes` as its own and
   skip re-resolving the new notes.
4. **OPERATOR.md gaps closed** (audit against brief §2.1–8, spec §5, §6):
   - it told the operator to read `--help` for every tool; six vendored tools
     (`resolve_timecodes`, `estimate_costs`, `validate_notes`,
     `extract_stills`, `reassemble`, `build_changelog`) are bare-positional
     with no argparse — now named explicitly, with "read the docstring";
   - no batch-composition procedure (spec §5.1 last authority, §7): added —
     saturate `max_concurrent`, serialize only real dependencies (Gate 2 asset
     → fan-out; white pass → its bundle), upload hygiene is the tool's job,
     transient failures retry in-tool, **content failures are never retried
     blind**, plus the once-per-account quota probe;
   - the QC adjudication model floor (spec §6/§9, Sonnet minimum, Opus-class
     where video-qc's tiering escalates) was missing from §5.3;
   - the Gate 3 package cited flat `stills/extracted/<f>.png` paths while
     Step 4 mandates `<note_id>/<shot>/` subdirs — reconciled;
   - round close now names `state.new_round()` instead of hand-waving "loop to
     Step 1".

Every tool invocation cited in the charter was checked against the live CLI
(`--help` or source) this session, including the video-qc script chain at
`C:/Users/story/.claude/skills/video-qc/scripts` (`intake` → `prepass` →
`extract_frames` → `checks {manifest,validate,vote}` → `crosshot` →
`threshold --tier {broadcast,standard,social_vertical}` → `report --parent`)
and both runner argv forms (`-- tools/x.py …` and `-- x.py …` both resolve).

## Deviations from the brief/spec (all recorded, none silent)

1. **OPERATOR.md is 410 lines, not the ~200–300 targeted.** The overrun is
   code blocks and tables (step map, diff-gate region spec, escalation
   triggers), not prose.
2. **`extract_stills.py` has no shot prefix** — it writes `first.png` /
   `last.png` / `t<sec>.png`, so spec §3's `stills/extracted/<shot>_<frame>.png`
   is unachievable without editing the tool. The charter puts the identity in
   the path instead (`stills/extracted/<note_id>/<shot>/`), which also matches
   note-schema's layout and feeds per-note cost attribution. Tool unchanged.
3. **`--profile` and `revision_scope` do not exist in the installed video-qc.**
   Spec §6's vocabulary is operator-side: the charter defines `smoke` /
   `standard` as *which shots enter the QC dir and how many observation runs
   are dispatched*, and `revision_scope` as a field the operator writes into
   the QC `shots.json`. Explicit seam, marked in the charter.
4. **`state.py` grew helpers beyond the stated contract** (all additive):
   `job_config()`, `batch_update`/`batch_state`, `new_round`, atomic `save()`,
   `run_tool(capture=, cwd=)`, CLI `--capture` / `--tail`. No specified
   signature changed.
5. **`start_step(n)` clears `position.parked`** — a step starting means the
   escalation that parked the round was answered.
6. **`round_report.json` carries additive keys** beyond spec §8.3's literal
   list: `mode`, `gate_config`, `round_gates`, `qc_refs`, `bundle`,
   `rationale`, `totals.tool_invocations` / `failed_invocations` /
   `unattributed_actual_usd`. Additive-only is the schema's own contract.
7. **Gate→note attribution is mechanical**, because `record_gate()` has no
   `note_id` parameter: explicit `note_id` key, else a note id found inside
   the gate string (`"stills:N-001"`), else the gate is round-level.
8. **Per-note actual cost is attributed by scanning ledger argv for note ids**
   (split evenly across matches). Anything naming no note becomes
   `totals.unattributed_actual_usd`, a residual, so the report's cost rows
   always sum to `state.budget.actual_usd`.
9. **Decision rationales are an additive `decisions[]` array on the Note.**
   `validate_notes.py` checks required keys only, so extras pass; if anyone
   adds strict schema checking, `decisions[]` must be allowed.
10. **`plan_round` resume granularity is per-stage, not per-step** —
    `resolve_timecodes`/`estimate_costs` are skipped when the round's ledger
    already holds a successful run (output durable in notes.json); the free
    read-only stages always re-run. A refinement of the idempotency contract,
    not a departure.
11. **`plan_round` normalizes `notes.json`'s `job_dir`** to the round's own
    directory before pricing, because `estimate_costs.py` reads
    `<doc.job_dir>/shots.json` rather than taking a job_dir argument.
12. **`plan_round` runs `batch_generate --dry-run` conditionally**, not
    unconditionally as brief §5 implies: `batch_generate` exits nonzero when
    no note is at `stills_approved`, which is always true in a plan-only round
    (Gate 3 has not happened). The plan record carries a derived payload
    summary instead, and the dry run joins the chain once payload specs exist.
13. **Strings that land in state.json, stdout or generated .md are ASCII**
    (em dashes and `§` stay in docstrings and stderr JSON). Captured
    subprocess stdout is machine-checked in tests and the Windows console
    codepage mangles non-ASCII.
14. **The budget-breach fixture breaches on the round total against a $1
    ceiling**, not via one deliberately expensive note as brief §6.1 words it.
    Same escalation, same assertion surface.
15. **`tests/run_tests.sh` MODULES was edited by two agents** (adding
    `test_build_round_report`, `test_state`, `test_plan_round`). It is the one
    shared file in the suite — check it after any parallel build.

## Open questions

- **Spec §3 vs §8 on job directories.** §3 says "one directory per revision
  round"; §8 says round N+1 loops into the same job directory. `new_round()`
  implements the §8 reading (one directory per *job*, rounds archived inside
  it). If the intended reading was one directory per round, `new_round` is
  dead code and `revise.py` needs a round-suffixed job id instead.
- **Mode `occ-native` is documented but unexercised.** The charter carries the
  occ command sequence (`occ check` → `preview` → `generate --dry-run` →
  `generate` → `stitch`); nothing in this repo has ever run it.
- **`$VQC` is not configurable.** The charter hardcodes the installed skill
  path in prose. If Brief 03 runs QC, promote it to a `job.yaml` key
  (`video_qc_path`) rather than editing the charter per machine.
- **No tool composes the video-qc revision job dir.** The operator does it by
  hand today (copy candidates, write `shots.json` with `revision_scope`). By
  the prime directive that is a missing tool — `build_qc_jobdir.py` — and the
  first candidate for Brief 03's tooling if the hand-assembly proves fiddly.

## What Brief 03's live round must know

**Untested paths (still open from Brief 01, plus new):**
- Seedance video generation submit/poll/download — dry-run only; `ep-…`
  endpoint IDs vendored from the skill and assumed current. **First live use is
  the pilot round.**
- `gptimage2` live edit; `occ-native` mode against a real occ install.
- **The quota probe has never run live.** Do it once, outside the round:
  `python tools/batch_generate.py --probe-quota --confirm` (paid), then set
  `max_concurrent` in job.yaml from `quota-probe.json` minus headroom.
- Everything from Gate 2 onward in the charter — steps 3–9 — has no test
  coverage beyond the tools' own unit tests. `--plan-only` (step 1 → Gate 1)
  is the only end-to-end-exercised path.
- `diff_gate` has never seen a real generated candidate; `reassemble` has
  never stitched real verified segments.

**The plan-only flow (do this first, it is free):**
```
python tools/revise.py <job-id> --notes <file> --config budget_ceiling_usd=<N> …
# operator completes the judgment fields in jobs/<job-id>/revision/notes.json
python tools/state.py jobs/<job-id> run -- plan_round.py jobs/<job-id>
```
It needs `jobs/<job-id>/shots.json` (shot_id + duration_s in lineup order) and,
for method-1 notes, `clips/<shot_id>.mp4`. It closes step 1, writes
`out/gate1_table.md` and `state.plan`, raises any ambiguity/budget escalation,
and **parks at LOCK**. It never passes Gate 1.

**Sitting the gates:**
- Gates are per-job in `job.yaml` (`gates: {lock, foundation, stills}`), all
  `human` by default; `--config gates.stills=agent` at scaffold time only
  (`--config` is refused on a resumed job — edit job.yaml directly).
- A `human` gate means **stop and wait**. Silence is not approval.
- Every gate outcome — human or agent — goes through
  `state.record_gate(job, "stills:N-003", "agent"|"human", "approved"|"rejected",
  evidence, rationale)`. Use the `<gate>:<note_id>` form so the report attributes
  it to the note. **A delegated (agent) gate with an empty `evidence` list fails
  `build_round_report.py --check`** — by design, spec §5.2.
- Gate 4 is machine and never delegable to the generating context.

**Cost attribution convention (adopt it or the report is useless):** put the
note id somewhere in the argv of the runner call that spends
(`--cost-usd` on a per-note invocation), or every dollar lands in
`totals.unattributed_actual_usd`. `batch_generate.py <job_dir> <notes.json>`
names no note; split the batch per note when per-note actuals matter more than
saturating `max_concurrent`, and say which you chose in the step summary.

**Traps worth knowing:**
- `check_budget` must be called **before** submission. It never escalates on
  its own — escalating is the operator's call (`state.escalate`).
- `diff_gate` region specs accept only `bbox` and `fullframe`; a note whose
  `region.type` is `mask` must be converted to the mask's bounding box.
  An unknown type is a hard tool error (exit 2), not a silent pass.
- Tools open files without an explicit encoding (house idiom), so a hand-authored
  `notes.json` saved as UTF-8 with curly quotes will be misread on this
  cp1252 host. Tool-written JSON is `ensure_ascii` and safe. Worth a
  repo-wide encoding pass if client prose ever arrives non-ASCII.
- `state.py show` is the first thing to run when picking a round back up; it
  re-syncs the ceiling from job.yaml and prints position, spend, batches, open
  escalations and the ledger tail.
- A round that ends parked still produces a valid `round_report.json`
  (`terminal_state: "escalated"`). Run `build_round_report.py` at close either
  way — `--check` is the round-close self-test.
