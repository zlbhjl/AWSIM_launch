import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_run_orchestrator_v2_dynamics_param_smoke_forwards_artifact_root(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "orchestrator_dynamics_records.jsonl"
    dataset_path = tmp_path / "orchestrator_dynamics_dataset.csv"
    artifacts_root = tmp_path / "dynamics_artifacts"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_orchestrator_v2.py"),
            "--target",
            "dynamics",
            "--case-kind",
            "uturn",
            "--param",
            "dx0=15",
            "--param",
            "ego_speed=36",
            "--param",
            "npc_speed=18",
            "--output",
            str(output_path),
            "--dataset-csv",
            str(dataset_path),
            "--dynamics-output-root",
            str(artifacts_root),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    output_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert json.loads(output_lines[-1])["worker_exit_code"] == 0

    record = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert record["target"] == "dynamics"
    assert record["case_kind"] == "uturn"
    assert Path(record["evidence"]["trace_csv"]).is_relative_to(artifacts_root)

    with dataset_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert rows[0]["target"] == "dynamics"
