# Setup notes — pre-Brief-01

Recorded 2026-08-12 at repo creation, before Brief 01 runs. Reconcile into the build.

## .env is populated (gitignored, never commit)

Keys present and live. Deviations from spec §4.3's key list:

- **No Google or OpenAI keys.** All three `image_edit.py` editors route through
  existing providers:
  - `seedream5pro` → **Ark** (`ARK_API_KEY` + `ARK_BASE_URL`)
  - `nanobananapro` → **WaveSpeed API** (`WAVESPEED_API_KEY`)
  - `gptimage2` → **WaveSpeed API** (`WAVESPEED_API_KEY`)
- **Hosting is Supabase storage, not WaveSpeed** (owner correction, 2026-08-12,
  supersedes spec §4.2/§7 where they name WaveSpeed as the upload host).
  WaveSpeed is a model API only. The upload tool the spec calls
  `wavespeed_upload.py` should be built as **`supabase_upload.py`**: local file
  → public URL in `SUPABASE_STORAGE_BUCKET` via `SUPABASE_URL` +
  `SUPABASE_KEY`. Contract otherwise unchanged: 100MB cap pre-flight,
  `revision/uploads.json` cache keyed by content hash, 7-day retention,
  `--force` bypass. Note: spec §1's dependency policy says "zero dependency on
  Supabase" — that means the agency system's database/services, not
  storage-as-hosting; this repo uses Supabase storage strictly as a dumb file
  host behind one tool, so swapping hosts later is a one-file change.

  **Hosting is provisioned and verified (2026-08-12):** project
  `creation-canvas` (`msdcpcjozrmdlfyjhtey`), public bucket `revision-agent`
  created, with storage.objects RLS policies granting the **anon** role
  insert/select/update/delete scoped to that bucket only (migration
  `revision_agent_bucket_policies`). `SUPABASE_KEY` in `.env` is the anon key —
  sufficient for uploads under these policies; no service-role key in this
  repo. Upload → public-URL round trip tested live and passing:
  `POST /storage/v1/object/revision-agent/<path>` then
  `GET /storage/v1/object/public/revision-agent/<path>`.
  Public URLs are permanent while the object exists — the 7-day "retention" in
  the cache contract governs cache reuse and cleanup, not link expiry, so
  `supabase_upload.py` should delete objects older than retention when it runs.
- `ARK_BASE_URL` is provided explicitly (`https://ark.ap-southeast.bytepluses.com/api/v3`);
  tools should read it from env rather than hardcoding.
- `ARK_ACCESS_Key` / `ARK_SECRET_KEY` also present (Ark account credentials) —
  only reach for them if the Ark contract actually requires signed requests.

## Keys present but NOT for v1 — do not wire

- `MINIMAX_API_KEY`, `FLUX_API_KEY`/`FLUX_BASE_URL` — Phase 3 material, out of scope.
- `ANTHROPIC_API_KEY` — available if video-qc adjudication needs direct API calls,
  but the operator itself runs as a Claude Code session (spec §9).
- `FRAMEIO_TOKEN` — blank by design; adapter is a stub in v1.

## .env.example

Brief 01's `.env.example` should mirror the actual key names above (names only,
no values) — not the spec's original ARK/WAVESPEED/GEMINI/OPENAI list.
