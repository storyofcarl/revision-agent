# Intake guide

## Contents
- Sources and how to parse each
- Timecode → shot resolution
- Scope classification (Foundational vs Local)
- Compiling acceptance criteria
- Ambiguity handling

## Sources and how to parse each

All notes normalize into `revision/notes.json` before anything else happens.
One actionable change = one Note object; a single email paragraph often
contains several.

- **frame.io** (manual CSV export or pasted comments — the API adapter is a
  future stub; do not build it). Comments carry native timecodes: preserve
  them exactly, including in/out ranges. Reply threads on a comment usually
  refine the same note — merge them into one Note, keeping the thread in
  `raw_text`.
- **Email**: pasted or forwarded text. Split prose into discrete notes.
  Preserve the author's wording verbatim in `raw_text`; your reading goes in
  `interpretation`, never in `raw_text`.
- **Spreadsheet**: read with the xlsx toolchain. Map columns to the schema;
  if the mapping is ambiguous, ask the user once — do not guess. Common
  layouts: (timecode | note | priority) or (shot_id | note | requested_by).

## Timecode → shot resolution

Run `scripts/resolve_timecodes.py` — this is a lookup, never inference.

- **occ-native**: the storyboard is the EDL. The script builds a cumulative
  duration map from storyboard rows and maps any timecode deterministically.
- **standalone**: requires shots.json with per-shot `duration_s` in lineup
  order (or an explicit EDL). Same cumulative map.
- A range spanning multiple shots resolves to all of them. The note then
  either splits (different fixes per shot) or keeps multiple
  `resolved_shots` (same fix everywhere).
- Frame-rate mismatches between the note's timecode base and the project are
  flagged by the script, not silently converted — confirm the base with the
  user.

## Scope classification

- **Foundational**: assets, story, style — anything that ripples. Signals: a
  character/prop/location named without a timecode; "everywhere",
  "throughout", "whenever X appears"; story or dialogue changes; style/grade
  direction. Foundational notes get `assets_touched` filled and route
  through Gate 2 (asset mutation + approval) before any shot work.
- **Local**: one scene or shot. A timecode plus a change that doesn't name a
  reusable asset.
- When unsure, classify Foundational and let Gate 1 demote it — the failure
  mode of a misfiled Foundational (N inconsistent local fixes) is far more
  expensive than one extra approval.

## Compiling acceptance criteria

Write criteria while reading the note — this is how misreads surface at
Gate 1 instead of after generation. Criteria are narrow forced-choice
questions with observable answers; they become video-qc N1 checks verbatim.

- Good: "Is the jacket red in every frame of the shot?" / "Does the sign
  read 'OPEN' with no other text?" / "Is the third background character
  removed?"
- Not a criterion: "make it feel more cinematic", "punch it up". Vague notes
  are Gate 1 discussion items — propose 2–3 concrete interpretations
  ("wider lens + slower push-in" / "moodier grade" / "re-time the cut") for
  the user to pick from, then write criteria against the pick.
- If the note names a region ("the poster on the left wall"), record it in
  `region` — it becomes the N2 pixel-lane mask.

## Ambiguity handling

Escalate, never guess. A note with no timecode and no unique referent ("fix
the lighting in the bar scene" with two bar scenes) goes to Gate 1 with your
best candidates listed. Wrong-shot and wrong-interpretation errors are the
most expensive failures in this pipeline; a clarifying question at Gate 1
costs nothing.
