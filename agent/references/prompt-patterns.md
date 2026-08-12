# Prompt patterns per method

## Contents
- Global rules (all methods)
- Method 1 — white-3D + stills (combined task)
- Method 2 — stills-only
- Method 3 — direct video edit
- Method 4 — full re-do
- White-pass conversion prompt

## Global rules (all methods)

- Start from the shot's original prompt. Edit it only if the prompt itself
  is implicated in the note, and then minimally.
- occ conventions hold: referenced characters are `@ImageN Name` and nothing
  more (wardrobe/relative-size notes only); state time-of-day; keep the
  standing negatives line (`No music. No text/logos.`).
- Edit/extend tasks address the source as `<Video_N>` directly — never
  "reference `<Video_N>`", which flips the task type to reference and
  unlocks everything you meant to keep.
- Name what must remain unchanged. For edits, the model treats unmentioned
  elements as free unless you emphasize them.

## Method 1 — white-3D + stills (combined task)

Pattern (Seedance combined-task form):

```
Reference the motion and camera movement in <Video_1>, and the appearance of
<Image_1> [first frame] and <Image_2> [last frame], to generate:
[original prompt, with the note's minimal edit if implicated].
[Time of day.] No music. No text/logos.
```

`<Video_1>` = the white pass; `<Image_N>` = the approved corrected stills in
first→last order. Do not describe the corrected element beyond what the
stills show — the stills are the authority; text descriptions of them leak
and drift.

## Method 2 — stills-only

```
Reference <Image_1> [first frame] and <Image_2> [last frame] to generate:
[original prompt ± minimal edit]. [Original audio direction if the shot had
dialogue/SFX.] [Time of day.] No music. No text/logos.
```

## Method 3 — direct video edit

Strict-edit form — one change per instruction, unchanged elements named:

```
Strictly edit <Video_1>: change [original characteristic] to
[new characteristic]. Keep [subject identity / camera movement / lighting /
everything else] exactly unchanged. No music. No text/logos.
```

- Add: describe element + timing + location. Delete: name the element to
  remove AND emphasize what stays.
- Chain at most two modifications per attempt; more collapses edit fidelity.

## Method 4 — full re-do

Follow occ's normal prompt assembly for the segment (director or storyboard
mode per the row), with fresh approved keyframes as `<Image_N>` references.
Nothing revision-specific beyond the storyboard row carrying the note's
change.

## White-pass conversion prompt (Method 1 pre-step)

Seedance Mini 480p, V2V on the original clip:

```
Strictly edit <Video_1>: render the entire video as a plain white,
textureless, unlit-clay 3D version of itself. Identical geometry, identical
motion, identical camera movement, identical timing. Flat white surfaces,
neutral grey background, no textures, no color, no lighting mood. No music.
No text/logos.
```

The white pass carries motion only. If the note changes blocking or camera,
Method 1 is the wrong method — reroute (routing-and-costs.md).
