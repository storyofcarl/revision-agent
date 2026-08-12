# revision-agent

Standing operator for the notes-driven revision loop on AI-generated video.
Receives stakeholder notes against a generated cut, classifies and routes
them, drives still edits and regeneration through the revision-pipeline
toolchain, verifies every candidate (diff-gate pre-check + video-qc
note-conformance gates), reassembles the cut, and packages the resubmission
with a per-note disposition report.

Spec: `../brief/revision-agent-spec.md` (build package). The
revision-pipeline skill's operating contract governs above everything here.

## Setup

1. `cp .env.example .env` and fill the keys (Ark, WaveSpeed, Supabase).
2. Runtime: Python 3.10+, ffmpeg/ffprobe on PATH.
3. `pip install requests numpy` (`openpyxl` optional, for .xlsx intake).

## Starting a round

```
python tools/revise.py <job-id> --notes <path> [--occ-project <path>] \
       [--config budget_ceiling_usd=25 gates.stills=agent] [--plan-only]
```

Scaffolds (or resumes) `jobs/<job-id>/` and prints the operator preamble.
A Claude Code session started in this repo then executes the round per
`agent/OPERATOR.md`, with human input at the gates `job.yaml` assigns to
humans. Modes: **standalone** (default) or **occ-native** when
`--occ-project` points at a directory with `project.yaml` + `storyboard.md`.

## Tools

Every mechanical operation is an independently runnable CLI — `--help` on
each fully documents its contract. Exit 0 on success; non-zero with a JSON
error object on stderr on failure. Runs inside a job log to
`jobs/<id>/logs/tools.jsonl`.

| Tool | What it does |
|---|---|
| `intake_normalize.py` | frame.io export / email / spreadsheet → candidate Note objects |
| `resolve_timecodes.py` | notes.json + shot manifest → shot IDs per note |
| `estimate_costs.py` | notes.json → per-note and round cost estimate |
| `validate_notes.py` | notes.json → pass/fail with itemized errors |
| `extract_stills.py` | shot clip → key stills |
| `image_edit.py` | still + prompt → edited still (Seedream 5 Pro default; Nano Banana Pro / GPT-image via WaveSpeed) |
| `white_render.py` | clip(s) → white motion-reference render via Seedance Mini; sub-4s bundling |
| `supabase_upload.py` | local file → hosted public URL (100MB cap, content-hash cache, 7-day retention) |
| `batch_generate.py` | submit/poll Seedance tasks; `--dry-run` plan check; `--probe-quota` (paid, `--confirm`) |
| `diff_gate.py` | original vs candidate → PASS/FAIL on pixel shift outside authorized regions |
| `reassemble.py` | verified clips → versioned cut; runtime/drift verified |
| `build_changelog.py` | notes.json → per-note status changelog |
| `frameio_adapter.py` | documented stub — fill in `fetch_comments()` later |
| `revise.py` | launcher: scaffold/resume job dir, record invocation, print preamble |

### Quota probe (recommended once per account)

```
python tools/batch_generate.py --probe-quota --confirm [--max-tasks N]
```

Paid, deliberate: ramps cheap Seedance Mini tasks until the API refuses,
reports the measured ceiling to `quota-probe.json`. Set `max_concurrent` in
`job.yaml` to the measured ceiling **minus headroom** for other projects
sharing the same Ark account (occ generation competes on the same quota).

## Tests

```
bash tests/run_tests.sh          # default tier: no network, no spend
bash tests/run_tests.sh --live   # + one minimal real call per paid API
```

Fixtures are tiny synthetic clips generated with ffmpeg on first run.
Individual modules: `python tests/test_<tool>.py`.

## Job directory

One directory per revision round under `jobs/<job-id>/` — self-contained,
versioned, never overwritten. Layout per spec §3; `state.json` records every
step transition, gate outcome, and tool invocation so an interrupted round
resumes without repeating spend.
