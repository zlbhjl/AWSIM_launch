import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "awsim" / "normal_trace_maude.json"


def test_run_orchestrator_v2_local_smoke(tmp_path: Path) -> None:
    output_path = tmp_path / "orchestrator_records.jsonl"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_orchestrator_v2.py"),
            "--fixture",
            str(FIXTURE),
            "--output",
            str(output_path),
            "--case-id",
            "orchestrator_smoke_case",
            "--tag",
            "smoke",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert output_path.exists()

    stdout_lines = [line for line in completed.stdout.strip().splitlines() if line.strip()]
    assert len(stdout_lines) == 2

    worker_payload = json.loads(stdout_lines[0])
    orchestrator_payload = json.loads(stdout_lines[1])
    assert worker_payload["case_id"] == "orchestrator_smoke_case"
    assert worker_payload["status"] == "success"
    assert worker_payload["mode"] == "queue"
    assert orchestrator_payload["mode"] == "local_queue"
    assert orchestrator_payload["worker_exit_code"] == 0

    lines = output_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["case_id"] == "orchestrator_smoke_case"
    assert record["status"] == "success"
    assert record["meta"]["verifier_name"] == "maude"
