# notes-brief-01 — toolchain build (2026-08-12)

Brief 01 complete. `bash tests/run_tests.sh` passes (14 modules, 49 tests,
no-spend), and the `--live` tier passed against real APIs: Supabase upload
round-trip, Seedream 5 Pro edit (Ark), Nano Banana Pro edit (WaveSpeed).

## Deviations from the brief/spec (all forced, all recorded)

1. **`wavespeed_upload.py` is `supabase_upload.py`** — owner correction (see
   notes-setup.md): hosting is Supabase storage; WaveSpeed is a model API
   only. Contract preserved (100MB cap, cache, 7-day retention, `--force`).
   `ark_client.upload_wavespeed()` became `upload_hosted()`, delegating to
   supabase_upload so there is one hosting implementation.
2. **Upload cache keys by sha256 content hash** (brief §4.4 contract), not
   the skill's `path::mtime` — renames/copies now hit the cache. Retention
   cleanup deletes expired bucket objects on every run (public URLs never
   expire on their own).
3. **`batch_generate.py` output dir** is `candidates/` per spec §3 (skill
   wrote `revision/gen/`). Permitted layout adaptation.
4. **`batch_generate.py` gained `--max-concurrent`** (default 3) so job.yaml
   can drive it, plus the brief-mandated `--probe-quota --confirm`.
5. **Windows fixes to vendored scripts:** SIGPIPE guard in
   build_changelog.py (no SIGPIPE on win32); forward slashes in
   reassemble.py's concat list (ffmpeg concat demuxer chokes on backslashes).
6. **PyYAML avoided:** job.yaml is written from a template string and only
   read by the operator (Brief 02 may parse it — add pyyaml then if needed).
   Deps stay: requests, numpy (diff_gate); openpyxl optional (.xlsx intake).

## Verified against live services (2026-08-12)

- WaveSpeed model IDs confirmed by no-spend 400-probe AND live edits:
  `google/nano-banana-pro/edit`, `openai/gpt-image-2/edit` (both take
  `prompt` + `images[]`; overridable via WS_MODEL_NANOBANANA/WS_MODEL_GPTIMAGE).
- Supabase bucket `revision-agent` on creation-canvas project: anon-key
  upload → public fetch → delete all work (policies via migration
  `revision_agent_bucket_policies`).

## Untested paths (register for Brief 03)

- **Seedance video generation** (submit/poll/download) — dry-run tested
  only; endpoints (`ep-…` IDs) vendored from the skill and assumed current.
  First live use is the pilot round (or the quota probe).
- **gptimage2 live edit** — endpoint existence confirmed by probe; no live
  edit run (nano banana already proves the WaveSpeed contract).
- **Quota probe live** — mock-tested; run once before the pilot:
  `python tools/batch_generate.py --probe-quota --confirm`.
- **occ-native mode against a real occ install** — dummy-fixture tested only.

## For Brief 02

- `revise.py` already scaffolds state.json with the 9-step ledger,
  `position`, `invocations[]`, `ledger[]`, `escalations[]`, and a `budget`
  block; `--plan-only` is recorded per invocation. Brief 02 builds the
  runner/idempotency/budget machinery on top — do not re-scaffold.
- `_common.py` provides `fail()` (JSON error + exit 1) and
  `log_invocation()` (jobs/<id>/logs/tools.jsonl) — reuse for new tools.
- Fixture lineup (tests/fixtures/generated/, built by tests/_util.py):
  SH001 5s / SH002 2.5s (sub-4s) / SH003 4s / SC002-SH004 4s at 24fps,
  plus authorized/unauthorized diff-gate candidates and notes fixtures.
- estimate_costs.py requires `doc["job_dir"]`/shots.json and
  resolved_shots before it runs — sequence intake tools accordingly.
