# Routing and costs

## Contents
- The four methods
- Selection: route by expected total cost
- Rate math (how estimate_costs.py computes)
- Tiebreak heuristics
- Foundational fan-out
- Still editors

## The four methods

1. **White-3D + stills (motion-preserving).** Convert the shot(s) to a white
   textureless render — Seedance Mini 480p, V2V ("the same video as a plain
   white untextured 3D render, geometry and motion identical") — extract key
   stills from the original (first, last; more if the noted element moves),
   fix the stills, then re-run referencing the white pass for motion + the
   approved stills for appearance + the original-or-minimally-edited prompt.
   Use when motion/blocking is approved and appearance is wrong.
2. **Stills-only.** Extract + fix key stills; re-run referencing stills +
   original audio/prompt. Use when motion is not critical.
3. **Direct video edit.** Seedance strict-edit on the original clip. Use for
   small localized changes where motion and most pixels must survive.
4. **Full re-do.** Fresh generation per occ keyframe rules. Use when the note
   rejects the shot's concept; story-level foundationals route here after
   the storyboard edit.

Sub-4s shots: Seedance's 4s floor means short shots generate bundled with
their storyboard neighbors per occ's ≤15s packing rules, trimmed back to the
EDL afterward. Never pad a short shot with invented content.

## Selection: route by expected total cost

Per-attempt costs at working res are close; **yield differences dominate.**

`expected_usd = per_attempt(method, res, duration) × expected_attempts(method)`

Seed expected_attempts (update from the video-qc ledger as real ratios
accumulate — the ledger is the authority once it has ≥3 jobs of data):

| Method | Seed attempts | Why |
|---|---|---|
| 1 | 2–3 | Highest control: locked motion + approved stills |
| 2 | 2–4 | Motion is re-rolled each attempt |
| 3 | 3–6 | Least controllable; the edit model decides what "unchanged" means |
| 4 | 3–5 | Fresh-generation lottery, mitigated by approved keyframes |

## Rate math

`scripts/estimate_costs.py` implements the CreationCanvas formulas — run it,
do not hand-compute:

```
tokens/sec   = (width × height × fps) / 1024
$/gen-sec    = (tokens/sec / 1,000,000) × rate_per_M
V2V          : ADDITIVE — output seconds bill at the regular (no-video)
               rate, AND input video seconds (~1:1 with output) bill at the
               with-video rate on top. Video reference always costs extra.
```

Rates (verify against creationcanvas.com/calc when variants or prices
change; put a fresh number in every Gate 1 table):

| Variant | Regular $/M | Video-input $/M (additive) |
|---|---|---|
| Pro 4K | 4.0 | 2.4 |
| Pro 1080p | 7.7 | 4.7 |
| Pro 720/480p | 7.0 | 4.3 |
| Fast | 5.6 | 3.3 |
| Mini | 3.5 | 2.1 |

Reference points, 4s shot: Method 2 ≈ $1.50 @1080p / $0.60 @720p; Methods
1 & 3 ≈ $2.41 / $0.98 (+ ~$0.22 Method-1 white pass at Mini 480p). Stills
edits cost cents — iterate at Gate 3, not in generation.

Concurrency: 3 tasks / 180 RPM at ≤1080p. Revision rounds never use 4K
(Pro-only, 1 task / 15 RPM — the delivery skill's problem).

## Tiebreak heuristics

- Note names a specific object/region, everything else must survive →
  **3** first; fall back to **1** after 2 failed attempts.
- Character identity or wardrobe → **1** or **2** (stills give identity
  control that video edits lack).
- Lighting, atmosphere, grade — anything the white pass would erase →
  **2** or **4**, never 1.
- Prompt itself implicated → any method, with the minimal prompt edit that
  addresses the note (prompt-patterns.md).

## Foundational fan-out

After Gate 2 approves the mutated canonical asset:

- Enumerate affected shots — occ-native: match the asset's name in the
  storyboard Cast/Location columns; standalone: shots.json `characters[]` /
  `location`. Append to the note's `resolved_shots`.
- Method per shot: **1** where motion is approved, **2** elsewhere. Every
  fan-out generation references the ONE approved asset — never a per-shot
  local edit of it.
- Story-level changes (a beat rewritten): storyboard row edit → `occ check`
  → Method 4.

## Inference endpoints (ModelArk)

| Model | Endpoint ID |
|---|---|
| Seedance 2.0 (Pro) | ep-20260508193017-pjb8t |
| Seedance Fast | ep-20260626055930-mnztv |
| Seedance Mini | ep-20260626055125-lst2w |
| Seedream 5 Pro | ep-20260708211633-gjng5 |
| Seedream 5 Lite | ep-20260720040538-hchm2 |

Overridable via the skill .env (ARK_EP_* variables) if endpoints rotate.

## Still editors

Default **Seedream 5 Pro** (single-image edit; output caps at 2K — fine for
working res). Fallbacks when it refuses the edit or identity drifts:
**Nano Banana Pro** (strongest for text-in-image), **GPT image-2**. Record
which editor produced each approved still in `lineage.approved_stills`.
