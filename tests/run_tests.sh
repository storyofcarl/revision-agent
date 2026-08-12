#!/usr/bin/env bash
# Default tier: no network, no spend. --live adds one minimal real call per
# paid API (clearly labeled; requires a populated .env).
set -u
cd "$(dirname "$0")"

command -v ffmpeg >/dev/null || { echo "ffmpeg not on PATH"; exit 1; }
command -v ffprobe >/dev/null || { echo "ffprobe not on PATH"; exit 1; }

PY="${PYTHON:-python}"
$PY -c "import _util; _util.ensure_fixtures()" || exit 1

MODULES=(
  test_intake_normalize test_resolve_timecodes test_estimate_costs
  test_validate_notes test_extract_stills test_image_edit
  test_white_render test_supabase_upload test_diff_gate
  test_batch_generate test_reassemble test_build_changelog
  test_frameio_adapter test_revise test_build_round_report
  test_state test_plan_round
)

if [[ "${1:-}" == "--live" ]]; then
  echo "=== LIVE TIER: real API calls, real spend ==="
  export REVISION_AGENT_LIVE=1
  MODULES+=(test_live)
fi

fail=0
for m in "${MODULES[@]}"; do
  echo "--- $m"
  $PY -m unittest "$m" -v 2>&1 | tail -3
  [[ ${PIPESTATUS[0]} -ne 0 ]] && fail=1
done

if [[ $fail -eq 0 ]]; then
  echo "ALL TESTS PASSED"
else
  echo "FAILURES — see above"
fi
exit $fail
