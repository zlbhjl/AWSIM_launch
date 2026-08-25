#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="${TMPDIR:-/tmp}/awsim_v2_no_sim"
FIXTURE_PATH="$ROOT_DIR/tests/fixtures/awsim/timeout_trace.txt"

mkdir -p "$TMP_DIR"
rm -f \
  "$TMP_DIR/worker_records.jsonl" \
  "$TMP_DIR/orchestrator_records.jsonl"

echo "[1/5] v2 worker fixture smoke"
python3 "$ROOT_DIR/run_worker_v2.py" \
  --fixture "$FIXTURE_PATH" \
  --output "$TMP_DIR/worker_records.jsonl"

echo "[2/5] v2 orchestrator fixture smoke"
python3 "$ROOT_DIR/run_orchestrator_v2.py" \
  --fixture "$FIXTURE_PATH" \
  --output "$TMP_DIR/orchestrator_records.jsonl"

echo "[3/5] v2 worker param/headless smoke"
python3 -m pytest \
  "$ROOT_DIR/tests/smoke/test_run_worker_v2_local.py::test_run_worker_v2_param_headless_subprocess_smoke" \
  -q

echo "[4/5] v2 orchestrator param/headless smoke"
python3 -m pytest \
  "$ROOT_DIR/tests/smoke/test_orchestrator_param_pipeline.py::test_orchestrator_param_pipeline_smoke_runs_through_worker" \
  "$ROOT_DIR/tests/smoke/test_orchestrator_param_pipeline.py::test_run_orchestrator_v2_param_headless_smoke_forwards_to_worker_backend" \
  -q

echo "[5/5] v2 no-sim regression checks"
python3 -m pytest \
  "$ROOT_DIR/tests/unit/apps/test_worker_main.py" \
  "$ROOT_DIR/tests/unit/apps/test_orchestrator_cluster_main.py" \
  "$ROOT_DIR/tests/unit/runtime/test_result_sink.py" \
  "$ROOT_DIR/tests/unit/runtime/test_actor_runtime.py" \
  "$ROOT_DIR/tests/unit/targets/test_awsim_backend.py" \
  "$ROOT_DIR/tests/unit/targets/test_awsim_backend_infra.py" \
  -q

echo
echo "worker_records.jsonl: $TMP_DIR/worker_records.jsonl"
echo "orchestrator_records.jsonl: $TMP_DIR/orchestrator_records.jsonl"
