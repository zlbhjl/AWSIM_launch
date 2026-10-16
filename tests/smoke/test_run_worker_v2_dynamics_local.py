import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_run_worker_v2_dynamics_param_smoke_writes_jsonl_dataset_and_trace(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "worker_dynamics_records.jsonl"
    dataset_path = tmp_path / "worker_dynamics_dataset.csv"
    artifacts_root = tmp_path / "dynamics_artifacts"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_worker_v2.py"),
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
    summary = json.loads(completed.stdout.strip())
    assert summary["target"] == "dynamics"
    assert summary["case_kind"] == "uturn"
    assert summary["status"] == "success"

    record = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert record["target"] == "dynamics"
    assert record["output"]["c_collision"] in (0, 1)
    assert Path(record["evidence"]["raw_result_json"]).is_file()
    assert Path(record["evidence"]["trace_csv"]).is_file()

    with dataset_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert rows[0]["target"] == "dynamics"
    assert rows[0]["case_kind"] == "uturn"
