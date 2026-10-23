"""Registered dynamics models in the statistical modes, through the CLI."""

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
OU = ["--param", "sde_dt_sec=0.01", "--param", "horizon_sec=1.0",
      "--param", 'event={"kind": "terminal_exceedance", "threshold": 1.0}']


def _run(tmp_path: Path, *arguments: str) -> tuple[dict, list[dict]]:
    output, dataset = tmp_path / "records.jsonl", tmp_path / "dataset.csv"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "run_orchestrator_v2.py"), "--target", "dynamics", *arguments,
         "--output", str(output), "--dataset-csv", str(dataset), "--dynamics-output-root", str(tmp_path / "art")],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    summary = json.loads(completed.stdout.splitlines()[-1])
    with dataset.open(encoding="utf-8", newline="") as stream:
        return summary, list(csv.DictReader(stream))


def test_ou_binomial_ci_varies_only_the_noise(tmp_path: Path) -> None:
    summary, rows = _run(tmp_path, "--case-kind", "ou_scalar", "--mode", "binomial_ci", "--max-samples", "30",
                         "--binomial-min-samples", "30", "--binomial-target-width", "1.0", *OU)

    report = summary["statistical_report"]
    assert report["metric"] == "c_event"
    assert report["input_distribution"] == {}
    assert len(rows) == 30 and len({row["sde_seed"] for row in rows}) == 30
    assert {row["model_id"] for row in rows} == {"ou_scalar"}


def test_ou_dkw_uses_the_terminal_state(tmp_path: Path) -> None:
    summary, _ = _run(tmp_path, "--case-kind", "ou_scalar", "--mode", "dkw_fixed", "--max-samples", "60", *OU)

    assert summary["statistical_report"]["metric"] == "x_terminal"


def test_ou_with_a_stopping_rule(tmp_path: Path) -> None:
    summary, rows = _run(tmp_path, "--case-kind", "ou_scalar", "--mode", "binomial_ci",
                         "--stopping-rule", "binomial_ci_fixed_strict", "--max-samples", "20", *OU)

    assert len(rows) == 20
    assert summary["statistical_report"]["metric"] == "c_event"


def test_model_b_with_a_sampled_initial_state(tmp_path: Path) -> None:
    summary, rows = _run(
        tmp_path, "--param", "model_id=ou_augmented", "--mode", "binomial_ci", "--max-samples", "10",
        "--binomial-min-samples", "10", "--binomial-target-width", "1.0", "--param", "sde_dt_sec=0.01", "--param", "horizon_sec=1.0",
        "--param", 'event={"kind": "terminal_exceedance", "threshold": 0.3, "state": "x"}',
        "--dynamics-input-distribution", '{"x0": {"min": -0.5, "max": 0.5}}',
    )

    assert {row["case_kind"] for row in rows} == {"ou_augmented"}
    assert len({row["x0"] for row in rows}) == 10
    assert summary["statistical_report"]["input_distribution"]["x0"]["max"] == 0.5


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--case-kind", "ou_scalar", "--mode", "binomial_ci", "--max-samples", "5",
          "--param", "sde_dt_sec=0.01", "--param", "horizon_sec=1.0"], "event must be a mapping"),
        (["--case-kind", "ou_scalar", "--mode", "ebstop", "--max-samples", "5", *OU], "--ebstop-value-range-min"),
        (["--case-kind", "lane_change", "--mode", "binomial_ci", "--max-samples", "5", *OU], "unsupported dynamics model_id"),
    ],
)
def test_invalid_registered_model_runs_are_rejected_before_running(tmp_path: Path, arguments, message) -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "run_orchestrator_v2.py"), "--target", "dynamics", *arguments,
         "--output", str(tmp_path / "r.jsonl"), "--dataset-csv", str(tmp_path / "d.csv")],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )

    assert completed.returncode != 0
    assert message in completed.stderr
