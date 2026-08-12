# Note schema

## Contents
- revision/ directory layout
- The Note object
- notes.json envelope
- revision.yaml
- Status lifecycle and lineage

## revision/ directory layout

```
<job_dir>/revision/
├── revision.yaml        # mode, round number, working res, tier, master ref
├── notes.json           # the plan — validated by scripts/validate_notes.py
├── stills/<note_id>/    # extracted + edited stills per note
├── white/<shot_id>.mp4  # Method-1 white-pass motion references
└── changelog_round<N>.md
```

## The Note object

Every field required unless marked optional.

```json
{
  "note_id": "N-014",
  "source": {"type": "frameio|email|spreadsheet", "ref": "row/comment/message id", "author": "who gave the note"},
  "raw_text": "verbatim original — never paraphrased away",
  "timecode": {"in": "00:01:02:12", "out": "00:01:08:00", "fps_base": 24},
  "resolved_shots": ["SQ010-SC020-SH030"],
  "scope": "foundational|local",
  "interpretation": "one-sentence plain statement of the change",
  "acceptance_criteria": [
    {"id": "N-014-c1", "question": "Is the jacket red in every frame of the shot?", "expected": "yes"}
  ],
  "region": {"type": "bbox|mask|fullframe", "data": "x,y,w,h normalized 0-1 | mask path | null"},
  "method": 1,
  "assets_touched": ["refs/mara.png"],
  "est_cost": {"per_attempt_usd": 1.83, "expected_attempts": 3, "expected_usd": 5.49},
  "status": "pending",
  "lineage": {"parent_clip": "clips/SQ010-SC020-SH030_v1.mp4", "revision_cycle": 1,
              "approved_stills": [], "white_pass": null, "candidates": []}
}
```

Field notes:
- `timecode` is null for asset/global foundational notes with no anchor.
- `region` is optional; when present it drives the video-qc N2 pixel-lane
  mask. Omit rather than invent — a wrong mask hides collateral damage.
- `method` values: 1 white-3D+stills · 2 stills-only · 3 direct video edit ·
  4 full re-do (see routing-and-costs.md).
- `assets_touched` — foundational notes only; paths to canonical refs/plates
  mutated at Gate 2.
- `est_cost` is written by `scripts/estimate_costs.py`; do not hand-compute.

## notes.json envelope

```json
{
  "round": 2,
  "created": "2026-07-19",
  "job_dir": "projects/blast-off",
  "mode": "occ-native",
  "working_res": "720p",
  "notes": [ /* Note objects */ ],
  "totals": {"expected_usd": 41.20, "notes": 9, "shots_touched": 14}
}
```

## revision.yaml

```yaml
mode: occ-native            # or standalone
round: 2
working_res: 720p           # never above the lineup's current res
qc_tier: broadcast          # passed through to video-qc --tier
master: audio/master.wav    # runtime + drift verification target
prior_cut: outputs/cut_v1.mp4
```

## Status lifecycle and lineage

`pending → locked → stills_approved → generated → verified | failed → done | escalated`

- Only Gate transitions advance status; scripts and generation steps update
  it in place. notes.json is the single source of truth — the changelog and
  resubmission package are rendered from it, never hand-written.
- `lineage.candidates` accumulates every generated attempt
  (`{path, ts, verdict}`); nothing is deleted until the round is approved.
- `revision_cycle` increments per repair loop and is copied into the
  video-qc shots.json (`repair_cycle`) so the 2-cycle cap and
  net-improvement rules bind across both systems.
