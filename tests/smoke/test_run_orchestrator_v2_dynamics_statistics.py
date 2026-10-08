import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_dynamics_binomial_sampling_persists_distribution_seed_and_stop_reason(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "records.jsonl"
    dataset_path = tmp_path / "dataset.csv"
    completed = subprocess.run(
        [
            sys.executable, str(ROOT / "run_orchestrator_v2.py"),
            "--target", "dynamics", "--case-kind", "uturn", "--mode", "binomial_ci",
            "--max-samples", "3", "--binomial-min-samples", "3",
            "--binomial-target-width", "1.0", "--seed", "7",
            "--output", str(output_path), "--dataset-csv", str(dataset_path),
            "--dynamics-output-root", str(tmp_path / "artifacts"),
        ],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout.splitlines()[-1])
    report = summary["statistical_report"]
    assert report["metric"] == "c_collision"
    assert report["sample_count"] == 3
    assert report["seed"] == 7
    assert report["input_distribution"]["dx0"]["min"] == 10.0
    assert "max_samples=3" in summary["stop_reason"]
    artifact = Path(summary["statistical_artifact"])
    saved_summary = json.loads(artifact.read_text(encoding="utf-8"))
    assert saved_summary["statistical_report"]["seed"] == 7

    with dataset_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 3
    assert {row["sampling_seed"] for row in rows} == {"7"}
    assert all(row["sampled_parameters"] for row in rows)


def test_dynamics_screening_mode_uses_candidate_metric(tmp_path: Path) -> None:
    output_path = tmp_path / "records.jsonl"
    completed = subprocess.run(
        [
            sys.executable, str(ROOT / "run_orchestrator_v2.py"),
            "--target", "dynamics", "--case-kind", "uturn", "--mode", "binomial_ci",
            "--max-samples", "3", "--binomial-min-samples", "3",
            "--binomial-target-width", "1.0", "--seed", "7",
            "--dynamics-decision-mode", "screening",
            "--output", str(output_path), "--dataset-csv", str(tmp_path / "dataset.csv"),
            "--dynamics-output-root", str(tmp_path / "artifacts"),
        ],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout.splitlines()[-1])
    assert summary["statistical_report"]["metric"] == "c_screening_candidate"
    saved = json.loads(Path(summary["statistical_artifact"]).read_text(encoding="utf-8"))
    assert saved["dynamics_decision_mode"] == "screening"
