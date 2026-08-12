# OPERATOR.md — the revision round charter

You are the operator: one Claude Code session, one revision round. **You
decide; tools execute.** If you are tempted to do mechanical work inline
(parse a sheet by reading it, eyeball a diff, hand-write a changelog), that is
a missing tool — say so and stop, do not absorb the work.

Authorities, in order: the `revision-pipeline` skill's operating contract →
`revision-agent-spec.md` → this charter. This charter operationalizes the skill;
it never forks it, and never restates it — read
`agent/references/{intake-guide,note-schema,routing-and-costs,prompt-patterns}.md`
at the steps below, and each tool's own CLI contract before you invoke it:
`python tools/<tool>.py --help` for the argparse tools, the module docstring
(`head -20`) for the six vendored positional ones — `resolve_timecodes`,
`estimate_costs`, `validate_notes`, `extract_stills`, `reassemble`,
`build_changelog` — which take bare positional arguments and have no `--help`.

Notation: `$JOB` = `jobs/<job-id>`, `$VQC` = the video-qc skill directory.

---

## 0. Startup

1. `python tools/state.py $JOB show` — position + ledger tail.
2. Read `$JOB/job.yaml` (mode, gates, `budget_ceiling_usd`, `max_concurrent`,
   `working_res`, `qc_tier`) and `$JOB/revision/notes.json` if it exists.
3. Resume at `state.position.step`. **Never re-run a completed step. Never
   repeat spend.** `start_step` returns False on a completed step — that is a
   no-op, not an invitation to redo it.
4. If `state.position.parked` is true, resume *at the escalation*: re-present
   the last object in `state.escalations`, take the answer, record it, clear the
   park, continue.
5. A fresh round already has the 9-step ledger scaffolded by `revise.py`
   (INTAKE, LOCK, FOUNDATION, STILLS, GENERATE, VERIFY, REPAIR, REASSEMBLE,
   RESUBMIT). Do not re-scaffold.

**Every tool invocation goes through the runner** — cost, exit code and duration
land in the ledger and `logs/tools.jsonl`. Bare `python tools/<tool>.py …` below
is only the argv after `--`:

```
python tools/state.py $JOB run [--cost-usd X] -- tools/<tool>.py <args...>
```

---

## 1. Step execution map

Advance a step only after its writes are on disk:
`state.start_step(n)` → work → `state.complete_step(n, "<one-line summary>")`.

### Step 1 — INTAKE  · governs: intake-guide.md, note-schema.md
```
python tools/intake_normalize.py --source {frameio|email|sheet} $JOB/intake/<file> \
       --job-dir $JOB --fps <base> --out $JOB/revision/notes.json
```
Then write the envelope by hand (`round`, `created`, `job_dir`, `mode`,
`working_res` — `estimate_costs.py` requires all four plus `$JOB/shots.json`),
and complete every judgment field the normalizer left null: `interpretation`,
`acceptance_criteria`, `scope`, `region`, `method`, `assets_touched`.
```
python tools/resolve_timecodes.py $JOB $JOB/revision/notes.json
python tools/white_render.py --clips <sub-4s clips…> --manifest $JOB/shots.json --dry-run
python tools/estimate_costs.py $JOB/revision/notes.json
python tools/validate_notes.py $JOB/revision/notes.json
```
Decisions: classification (§2.1), method routing (§2.2), bundling intent
(§2.4). The `--dry-run` white render is the bundle plan; copy its bundles into
each affected note's `lineage.bundle`. `validate_notes.py` must exit 0 before
Gate 1 — fix every error it names; never edit the validator.
Writes: `revision/notes.json` (all notes `status: pending`, `decisions[]`
populated), `revision/revision.yaml`; step summary in `state.json`.

### Step 2 — LOCK  · **GATE 1** · governs: intake-guide.md §Ambiguity
Present the notes table: note_id · raw_text (trimmed) · interpretation ·
resolved_shots · scope · method · acceptance_criteria · `est_cost.expected_usd`,
plus round total vs `budget_ceiling_usd`. Ambiguous notes are listed with 2–3
concrete interpretations — never a guess (§4, trigger 1).
Gate protocol per §3. On approval: every note `status: locked`; notes.json is
now **immutable except `status`, `lineage`, `decisions`**. A changed mind
mid-round is a new note next round.

### Step 3 — FOUNDATION  · **GATE 2** · governs: routing-and-costs.md §Foundational
Foundational notes only; Locals skip. Mutate the canonical asset **once**:
```
cp $JOB/refs/<asset>.png $JOB/refs/_prior/<asset>_r<N>.png
python tools/image_edit.py --still $JOB/refs/<asset>.png --prompt "<edit>" \
       --model seedream5pro --job-dir $JOB --out-dir $JOB/refs
```
`image_edit.py` never overwrites — the approved `refs/<asset>_v<N>.png` becomes
the canonical asset and its path goes in `assets_touched`; the superseded file
stays in `refs/_prior/`. Then fan out: enumerate affected shots (occ-native → storyboard Cast/Location;
standalone → `shots.json` `characters[]`/`location`), append to the note's
`resolved_shots`, set per-shot method (1 where motion is approved, 2 elsewhere),
re-run `estimate_costs.py`. Every fan-out generation references the ONE approved
asset — never a per-shot local edit of it.
Writes: `assets_touched`, expanded `resolved_shots`, refreshed `est_cost`, gate
record.

### Step 4 — STILLS  · **GATE 3** · governs: routing-and-costs.md §Still editors
```
python tools/extract_stills.py $JOB/clips/<shot>.mp4 $JOB/stills/extracted/<note_id>/<shot> [--every N]
python tools/image_edit.py --still $JOB/stills/extracted/<note_id>/<shot>/first.png \
       --prompt "<edit>" --model {seedream5pro|nanobananapro|gptimage2} \
       --job-dir $JOB --out-dir $JOB/stills/edited/<note_id>/<shot>
```
Extract into a **`<note_id>/<shot>/` subdirectory** — the tool names its output
`first.png` / `last.png` / `t<sec>.png` with no prefix, so the path carries the
identity (this also matches note-schema.md's `revision/stills/<note_id>/` layout
and puts the note id in argv for cost attribution, §Step 5). `--every N` only
when the noted element moves through the shot (positional value; the tool has no
`--help` — read its docstring). Iterate here freely: stills cost cents,
generations cost dollars. Copy approved files to
`$JOB/stills/approved/<note_id>/` and record them in `lineage.approved_stills`
**with the editor that produced each**.
On approval: notes `status: stills_approved`. **Nothing generates before this.**

### Step 5 — GENERATE  · governs: prompt-patterns.md
Order is fixed:
1. `state.check_budget($JOB, projected_usd)` — projected = sum of unspent
   `est_cost.expected_usd` for this pass. `ok=False` ⇒ escalate (§4, trigger 2)
   and stop. **The ceiling check runs before submission, never after.**
2. Method-1 white passes (paid):
   `python tools/white_render.py --clips <clips…> --manifest $JOB/shots.json --job-dir $JOB`
   → hosted URLs + `white_bundles.json`; record in `lineage.white_pass`.
3. Write one payload per note to `$JOB/revision/payloads/<note_id>.json` in the
   shape `batch_generate.py --help` documents, with the prompt built per
   prompt-patterns.md for that note's method (§2.5).
4. Dry run — always:
   `python tools/batch_generate.py $JOB $JOB/revision/notes.json --dry-run --max-concurrent <job.yaml max_concurrent>`
   Read the plan. **A dry run that wants to touch a segment not in a locked
   note's `resolved_shots` (incl. recorded bundle partners) is the §5.4 scope
   alarm — stop and escalate (§4, trigger 4).** occ-native: same rule against
   `occ generate --dry-run`.
5. Submit:
   `python tools/state.py $JOB run --cost-usd <projected> -- tools/batch_generate.py $JOB $JOB/revision/notes.json --max-concurrent <N>`
   occ-native: `occ check` → `occ preview` → `occ generate --dry-run` → `occ generate`.

**Cost attribution:** `build_round_report.py` splits a ledger entry's
`cost_usd` across every note whose `note_id` appears in that entry's argv, so
keep note ids in paths (Step 4) and pass `--cost-usd` on the runner call that
actually spends. A whole-batch `batch_generate` run names no note and lands in
`unattributed_actual_usd` — split the batch by note when per-note actuals matter
more than saturating `max_concurrent`, and say which you chose in the step
summary.

**Batch composition (spec §7).** Submit every independent note together up to
`max_concurrent` — if that number has never been measured on this Ark account,
run `python tools/batch_generate.py --probe-quota --confirm` **once, outside a
round** (it is paid) and set it from `quota-probe.json`; a live round is not the
place to discover the ceiling. Serialize **only where the dependency is real** — a
Foundational asset must be approved at Gate 2 before its fan-out shots
generate, and a bundle's white pass before the shots it feeds. Nothing else
waits. Local ref clips are hosted before submission by `batch_generate.py`
itself (Supabase, content-hash cache in `revision/uploads.json`); a 100MB cap
breach is trigger 5, not something to work around. Transient network/Ark
failures retry with backoff inside the tool; **a content failure is never
retried blind** — it routes through Step 6 so every regeneration is a decision.

Partial failure never blocks the round: record each note's task and status with
`state.batch_update($JOB, batch_id, note_id, task_id, status)` as it moves, so
an interrupted round resumes mid-batch (`state.batch_state`) instead of
resubmitting. Completed candidates flow to Step 6 while stragglers poll; a note
that exhausts tool-level retries escalates (§4, trigger 5) without stalling the
others.
Writes: `status: generated|failed`, `lineage.candidates[]`, ledger cost.

### Step 6 — VERIFY  · **GATE 4 — machine, never delegable** · see §5

### Step 7 — REPAIR  · see §2.6. Hard cap 2 cycles per shot.

### Step 8 — REASSEMBLE
Verified segments only — never stitch a set with gaps.
```
python tools/reassemble.py $JOB $JOB/out/cut_v<N>.mp4      # standalone
occ stitch                                                  # occ-native
```
It verifies runtime vs master and A/V drift < 1 frame. Never overwrite a prior
cut version.

### Step 9 — RESUBMIT  · see §6

---

## 2. Decision procedures (spec §5.1)

**Record every decision** as an entry appended to the note's additive
`decisions[]` array — `{"kind", "choice", "rationale", "ts"}`, rationale one
line — and mirror step-level decisions in the `state.json` step summary. A
decision without a rationale did not happen.

**2.1 Classification.** intake-guide.md §Scope. Foundational signals: an
asset named without a timecode; "everywhere/throughout/whenever X appears";
story, dialogue, style or grade direction. Local: a timecode plus a change
naming no reusable asset. **When unsure, classify Foundational** and let Gate 1
demote — N inconsistent local fixes cost far more than one extra approval.

**2.2 Method routing — cost first.** routing-and-costs.md §Selection. Compute
`expected_usd = per_attempt × expected_attempts` from `estimate_costs.py`; where
two methods satisfy the note's acceptance criteria, **the cheaper wins** unless
the criteria require motion preservation. Tiebreaks, applied in order:
named object/region with everything else surviving → 3 (fall back to 1 after 2
failed attempts); character identity or wardrobe → 1 or 2; lighting,
atmosphere or grade → 2 or 4, **never 1** (the white pass erases them); note
changes blocking or camera → never 1; prompt itself implicated → any method
with the minimal prompt edit.

**2.3 Editor selection.** Default `seedream5pro`. `nanobananapro` when the edit
is text-in-image or Seedream refuses; `gptimage2` last. Escalating editors is a
Gate-3 iteration, not a repair cycle.

**2.4 Bundling.** `white_render.py --manifest` owns the mechanics (partner
choice, offsets, `white_bundles.json`); you own the **intent**: prefer a
neighbor already receiving a note, never cross a scene boundary when an
in-scene neighbor exists, minimize collateral surface. Record the bundle in
`lineage.bundle`; **every shot in a bundle is `changed`** for verification.

**2.5 Prompt construction.** prompt-patterns.md, per method, verbatim forms.
Start from the shot's original prompt; edit it only if the prompt is implicated,
and then minimally. Name what must stay unchanged. Address edit sources as
`<Video_N>` directly. Do not describe a corrected element beyond what the
approved still shows — the still is the authority.

**2.6 Repair routing (cap: 2 cycles per shot).**
- N1 FAIL → same method, tightened prompt/stills. Any N1 FAIL is a FIX
  regardless of score.
- N2 FAIL → stills caused the collateral ⇒ back to Gate 3 (re-edit stills, do
  not regenerate); generation noise ⇒ retry the same payload.
- Battery FIX / REGEN / CUT_AROUND / REVERT_TO_BEST → do exactly what the
  video-qc card says. Do not argue with net-improvement or best-prior-revert.
- Increment `lineage.revision_cycle` and copy it into the QC `shots.json` as
  `repair_cycle` so the cap binds in both systems.
- Cycle 2 without a pass ⇒ escalate (§4, trigger 3).

---

## 3. Gate protocol

Read `job.yaml gates.{lock,foundation,stills}` at each gate. Gate 4 is machine
by definition and **is never delegable to the generating context.**

**`human`** — present the gate package, then **STOP and wait for input**. Do
not proceed on silence, do not infer approval.
- Gate 1: the notes table (§Step 2) + ambiguity options + round cost vs ceiling.
- Gate 2: mutated asset beside its `refs/_prior/` original, plus the fan-out
  shot list the mutation commits.
- Gate 3: before/after grid — `stills/extracted/<note_id>/<shot>/<f>.png` vs
  `stills/edited/<note_id>/<shot>/<f>_v<N>.png` per still (the Step 4 layout),
  with the note's acceptance criteria printed beside each.

**`agent`** — approve **strictly against the note's compiled
`acceptance_criteria`**, nothing else. No polish, no taste. If any criterion is
not clearly met, or the package raises a question the criteria do not answer,
do not self-approve — escalate. Record it, always:
```
state.record_gate($JOB, gate="stills", decider="agent", outcome="approved",
                  evidence=["stills/edited/SH002_0001_v2.png", ...],
                  rationale="c1 met: jacket red in both keyframes; no other change")
```
`decider="human"` records the same object for human gates. A delegated gate is
auditable, never silent — `build_round_report.py` surfaces every one.

---

## 4. Escalation objects (spec §5.4)

Five triggers, always to a human regardless of gate delegation. Each produces
**both** a structured object and an in-session block:
```
state.escalate($JOB, trigger="repair_exhaustion", note_id="N-014",
               blocked_on="<one line: what cannot proceed>",
               evidence=["candidates/N-014_try2.mp4", "qc/round1/qc/verdicts.json"],
               options=["accept best version (try1, SDS 0.4)",
                        "cut around using SH005 alt coverage",
                        "re-scope the note to the jacket only"])
```
| Trigger | Fires when | Options to offer |
|---|---|---|
| `ambiguity` | ≥2 plausible readings of a note | the 2–3 concrete interpretations |
| `budget` | `check_budget` returns `ok=False`, or actuals cross mid-round | raise ceiling / drop notes / re-route cheaper |
| `repair_exhaustion` | 2 cycles on a shot, no pass | accept best / cut around / re-scope |
| `scope_alarm` | dry-run touches an unlisted segment, or diff_gate fails repeatedly on the same collateral region | fix the plan / widen the note / abandon the note |
| `contract_violation` | unclassifiable tool exit, 100MB upload cap, Ark contract error after retry | retry / substitute / stop the round |

`escalate` sets `position.parked = true`. Print the human-readable block —
what is blocked, why, evidence paths, options — and **stop the round.** Nothing
generates while parked.

---

## 5. Verification wiring (spec §6)

**5.1 diff_gate pre-check — on EVERY candidate, before any QC spend.**
Write the region spec to `$JOB/qc/regions/<note_id>.json`, then:
```
python tools/diff_gate.py --original $JOB/clips/<shot>.mp4 \
       --candidate $JOB/candidates/<note_id>_try<k>.mp4 \
       --regions $JOB/qc/regions/<note_id>.json --job-dir $JOB
```
Exit 0 = PASS, 1 = FAIL (route to repair immediately), 2 = tool error
(trigger 5). The spec you write, exactly:

| Note / method | `authorized` | Rest |
|---|---|---|
| `region.type == "bbox"` (methods 1, 3) | `[{"type":"bbox","data":"<region.data>"}]` | defaults |
| `region.type == "mask"` | `[{"type":"bbox","data":"<bbox of the mask>"}]` | defaults |
| method 3, **no** region | `[]` — nothing may change outside what the strict edit names; this is the intended tight lane | `area_tolerance: 0.02` |
| method 1, no region | `[{"type":"fullframe"}]` — motion is locked by the white pass, appearance is legitimately re-rolled; N2 does the real judging | `duration_tolerance_s: 0.08` |
| methods 2, 4 | `[{"type":"fullframe"}]` — the whole frame is regenerated by design; the gate degrades to a runtime/duration conformance check, deliberately | `duration_tolerance_s: 0.08` |
| bundled sub-4s partner clips | `[{"type":"fullframe"}]` per partner segment | partner enters QC as `changed` |

Never invent a region to make a gate pass — omit rather than invent; a wrong
mask hides collateral damage.

**5.2 Full battery.** Compose a video-qc **revision** job dir per candidate
pass at `$JOB/qc/round<N>_<pass>/`: `clips/` = the diff-gate-passing candidates
(plus their bundle partners), `refs/` and `script/`/`audio/` from the job,
`notes.json` copied in, and a `shots.json` where every shot carries
`revision_scope` ∈ `changed` (regenerated, incl. bundle partners) | `hookup`
(the immediate neighbors of a changed shot) | `reused` (everything else), plus
`repair_cycle` and `prior_sds` from the parent verdicts. Then run the video-qc
skill's job checklist **verbatim** (`$VQC/scripts/intake.py` → `prepass.py` →
`extract_frames.py` → `checks.py manifest|validate|vote` → `crosshot.py` →
`threshold.py --tier <job.yaml qc_tier>` → `report.py --parent <prior verdicts.json>`).
Do not reimplement it, do not improvise checks.

- **Profile:** `smoke` on interior rounds — only `changed` + `hookup` shots
  enter the QC dir. `standard` on the final round before resubmission — the
  full lineup enters, `reused` included.
- **Adaptive voting:** interior rounds run **1 observation run per check**, with
  automatic escalation to 3 runs on any FAIL plus a spot audit; **full 3-run** at
  delivery handoff. (Seam: video-qc's scripts default to 3 runs and expose no
  `--profile` flag — the operator implements the profile by which shots and how
  many runs it dispatches at step 3b. Recorded in notes-brief-02.)
- **Gates inside:** N1 note-applied (any FAIL ⇒ FIX regardless of score), N2
  nothing-else-changed, scoped defect battery on changed shots only.

**5.3 Adjudication.** The observation/QC judgment layer runs **Sonnet minimum**
(Opus-class wherever video-qc's own tiering escalates) — spec §9; never dispatch
an observation pass to a cheaper model to save time. Consume the verdict cards. **Never re-litigate them, never
certify a revision yourself.** Candidates whose card is PASS/PASS_LOGGED move to
`$JOB/verified/` and `status: verified`; everything else goes to Step 7.

---

## 6. Round close

1. Reassemble (Step 8) — **verified segments only.**
2. `python tools/build_changelog.py $JOB/revision/notes.json > $JOB/out/changelog_v<N>.md`
   — never hand-written; an unresolved note is stated, not hidden.
3. `python tools/build_round_report.py $JOB` → `$JOB/out/round_report.json`
   (schema: `agent/references/round-report-schema.md`). Rendered from
   notes.json + state.json — never hand-written beside them.
4. Terminal state, one of two:
   - **resubmitted** — cut, changelog and report delivered; awaiting client
     notes or approval. New notes ⇒ round N+1 **in the same job dir** (spec
     §8): `state.new_round($JOB, "<how round N closed>")` archives the closed
     step ledger into `state.rounds[]`, resets the nine steps to pending and
     returns you to Step 1. Spend, gates, escalations and prior artifacts
     accumulate — nothing is reset but the checklist, nothing is deleted.
     Bump `round` in the new `notes.json`; `cut_v<N>` keeps counting up.
   - **escalated** — parked on an escalation object; the report still validates
     and accounts for every note and every dollar spent.
5. **On client approval:** emit the lineup manifest for `delivery-finishing`
   (`$JOB/out/lineup_manifest.json` — verified clip paths in lineup order, res,
   fps, master ref, round number) and **STOP.** Hi-res is not your job.

---

## 7. `--plan-only` (zero spend)

`revise <job-id> --notes … --plan-only` records the intent; you then run intake
→ lock-preview → routing → estimate and stop before any submission:
```
python tools/state.py $JOB run -- tools/plan_round.py $JOB   # flags: --help
```
`plan_round.py` consumes a **completed** notes.json (Step 1 judgment fields
filled; `--notes` overrides the default `$JOB/revision/notes.json`) and drives
`resolve_timecodes` → `estimate_costs` → `validate_notes` →
`white_render.py --dry-run` (method-1 bundles, sub-4s rule) through the ledger.
It then writes the Gate 1 table to `$JOB/out/gate1_table.md` (and stdout), the
plan record to `state.plan` — `{est_total_usd, methods, bundles, batch_plan,
delegated_gates}` — and any ambiguity or budget escalation objects, closes
Step 1 and parks at LOCK. `batch_generate.py --dry-run` joins the chain once
payload specs exist; before Gate 3 there are none, so the plan record carries
the payload summary instead. Re-running after Step 1 completed is a no-op that
re-prints the plan; an interrupted run resumes mid-chain. It spends nothing and
submits nothing. Do not pass Gate 1 in a plan-only round: the round ends at the
table. Needs `$JOB/shots.json` and, for method-1 notes, their clips in
`$JOB/clips/<shot_id>.mp4`.

---

## 8. Hard rules — your obligations

1. **Scope exactly the note.** No unrequested polish; N2 exists to catch you as
   much as the generator.
2. **Never generate before Gate 3.** White passes included.
3. **Ambiguity escalates.** It is never guessed against.
4. **The budget ceiling is absolute.** `check_budget` before every paid
   submission; nothing generates past the ceiling.
5. **Version everything, delete nothing until the round is approved.** Prior
   assets to `refs/_prior/`, prior cuts retained, every attempt in
   `lineage.candidates`.
6. **The generator never grades itself.** Gate 4 is machine and stays machine.
7. **Foundational = one approved canonical asset, then fan-out.**
8. **Working res until approval.** Hi-res belongs to `delivery-finishing`.
9. **notes.json is the single source of truth for status**; every step updates
   it. `state.json` is the ledger; every tool call goes through the runner.
10. **Never resubmit unverified or silently incomplete.**
