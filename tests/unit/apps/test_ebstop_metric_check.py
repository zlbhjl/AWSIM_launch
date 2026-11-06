"""EBStop is refused on metrics without a fixed range (docs/statistical_method_selection.md, EBStop)."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.cli.orchestrator_main import run_orchestrator


@pytest.mark.parametrize(("target", "extra"), [
    ("awsim", ["--param", "dx0=20"]),
    ("awsim", ["--param", "dx0=20", "--ebstop-target", "min_distance"]),
    ("dynamics", []),
])
def test_ebstop_on_an_unbounded_metric_fails_before_running(tmp_path: Path, target: str, extra: list[str]) -> None:
    with pytest.raises(ValueError, match="has no fixed range"):
        run_orchestrator(["--target", target, "--mode", "ebstop", "--max-samples", "5",
                          "--output", str(tmp_path / "records.jsonl"), "--dataset-csv", str(tmp_path / "d.csv"), *extra])
    assert not (tmp_path / "records.jsonl").exists()


def test_ebstop_on_a_bounded_metric_still_runs(tmp_path: Path) -> None:
    exit_code = run_orchestrator([
        "--target", "bernoulli", "--mode", "ebstop", "--seed", "7", "--max-samples", "40",
        "--ebstop-value-range-min", "0", "--ebstop-value-range-max", "1",
        "--output", str(tmp_path / "records.jsonl"), "--dataset-csv", str(tmp_path / "d.csv"),
    ])
    assert exit_code == 0
