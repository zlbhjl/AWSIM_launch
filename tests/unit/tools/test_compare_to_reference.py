from __future__ import annotations

import pytest

from tools.validation.compare_to_reference import run_once, summarise

REFERENCE = {"estimate": 1e-3, "ci95_clopper_pearson": [0.9e-3, 1.1e-3]}


def _run(plan, estimate_is, interval_is, estimate_mc=0.0, interval_mc=(0.0, 0.004)):
    return {"plan": plan, "final_status": "complete", "estimate_mc": estimate_mc, "interval_mc": list(interval_mc),
            "estimate_is": estimate_is, "interval_is": interval_is, "seconds_per_path": 0.001}


def test_summary_counts_intervals_that_overlap_the_reference() -> None:
    runs = [_run("q", 1.0e-3, [0.8e-3, 1.2e-3]), _run("q", 2.0e-3, [1.5e-3, 2.5e-3]), _run("q", 0.0, None)]

    entry = summarise(runs, REFERENCE)["q"]

    assert entry["is"]["intervals_overlapping_reference"] == 1
    assert entry["is"]["intervals_missing"] == 1
    assert entry["is"]["mean"] == pytest.approx(1.0e-3)
    assert entry["mc"]["intervals_overlapping_reference"] == 3


def test_run_once_returns_estimates_and_cost() -> None:
    result = run_once(("configs/is_final_reference/nonlinear2d_max_1p5_q1d.yaml", 20, 3))

    assert result["final_status"] == "complete"
    assert result["interval_is_bernstein"] is not None
    assert result["seconds_per_path"] > 0
