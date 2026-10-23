"""Regressions for the importance-sampling issues in docs/sde_importance_sampling_plan.md §2.

Each test reproduces one issue found before the SDE extension (E07-E12, plus
a crash in the comparison's target-width search) and states the fixed
behaviour.
"""

from __future__ import annotations

from math import log
from pathlib import Path

import pytest

from contracts.evaluation import EvaluationRecord
from contracts.execution import RawRunResult, RunStatus

def _importance(
    case_id: str,
    value: int,
    log_weight: float,
    *,
    status: RunStatus = RunStatus.SUCCESS,
    bound: float | None = None,
    extra: dict | None = None,
) -> EvaluationRecord:
    sampling = {
        "plan_id": "plan",
        "target_measure_id": "p",
        "sampling_mode": "manual_proposal",
        "proposal_id": "q",
        "importance_weight_status": "available",
        "log_p": log_weight,
        "log_q": 0.0,
        "log_importance_weight": log_weight,
    }
    if bound is not None:
        sampling["importance_weight_upper_bound"] = bound
    sampling.update(extra or {})
    return EvaluationRecord(
        case_id=case_id,
        target="prism",
        case_kind="simple_reliability_dtmc",
        status=status,
        output={} if status is not RunStatus.SUCCESS else {"c_failure": value},
        meta={"sampling": sampling},
    )


def _ordinary(case_id: str, value: int, *, extra: dict | None = None) -> EvaluationRecord:
    sampling = {
        "plan_id": "plan",
        "target_measure_id": "p",
        "sampling_mode": "uniform",
        "importance_weight_status": "not_applicable",
    }
    sampling.update(extra or {})
    return EvaluationRecord(
        case_id=case_id,
        target="prism",
        case_kind="simple_reliability_dtmc",
        status=RunStatus.SUCCESS,
        output={"c_failure": value},
        meta={"sampling": sampling},
    )


def test_e08_failed_record_makes_the_final_evaluation_incomplete() -> None:
    from evaluation.importance_sampling_audit import audit_fixed_sample_run

    records = [
        _importance("importance-000001", 1, 0.0, extra={"sample_id": "importance-000001"}),
        _importance("importance-000002", 0, 0.0, extra={"sample_id": "importance-000002"}),
        _importance(
            "importance-000003", 0, 0.0,
            status=RunStatus.EXECUTION_ERROR, extra={"sample_id": "importance-000003"},
        ),
    ]
    audit = audit_fixed_sample_run(
        records,
        planned_sample_ids=["importance-000001", "importance-000002", "importance-000003"],
        metric="c_failure",
    )

    assert audit.complete is False
    assert audit.counts["unresolved"] == 1


def test_e09_no_observed_event_is_not_reported_as_a_zero_width_interval() -> None:
    from evaluation.importance_sampling import calculate_standard_importance_sampling_interval

    records = [_importance(f"q{i}", 0, 0.0) for i in range(5)]
    result = calculate_standard_importance_sampling_interval(records, metric="c_failure")

    assert result is not None
    assert result["estimate"] == 0.0
    assert result["lower_bound"] is None and result["upper_bound"] is None
    assert result["information"] == "insufficient"


def test_e07_exact_zero_weight_is_accepted_by_the_contract() -> None:
    from contracts.sampling import normalize_sampling_metadata

    normalized = normalize_sampling_metadata(
        {
            "plan_id": "plan",
            "target_measure_id": "p",
            "sampling_mode": "manual_proposal",
            "proposal_id": "q",
            "importance_weight_status": "available",
            "weight_kind": "zero",
            "log_p": None,
            "log_q": -1.5,
            "log_importance_weight": None,
        }
    )

    assert normalized["weight_kind"] == "zero"
    assert normalized["log_importance_weight"] is None


def test_e08_large_log_weight_does_not_raise_overflow() -> None:
    from evaluation.importance_sampling import calculate_standard_importance_sampling_interval

    records = [_importance("q1", 1, 1000.0), _importance("q2", 0, 0.0)]
    result = calculate_standard_importance_sampling_interval(records, metric="c_failure")

    assert result is not None
    assert result["numeric_status"] == "overflow"
    assert result["estimate"] is None
    assert result["sample_size"] == 2


def test_e10_bounded_interval_keeps_the_raw_point_estimate() -> None:
    from evaluation.weighted_sequential import calculate_bounded_hoeffding_interval

    result = calculate_bounded_hoeffding_interval([2.0, 2.0], confidence=0.95, upper_bound=2.0)

    assert result["estimate"] == pytest.approx(2.0)
    assert result["upper_bound"] <= 1.0


def test_e11_comparison_rejects_records_with_different_target_fingerprints() -> None:
    from evaluation.importance_sampling_comparison import compare_sampling_methods

    ordinary = [_ordinary("o1", 0, extra={"target_fingerprint": "p-h0.01"})]
    importance = [_importance("q1", 0, 0.0, extra={"target_fingerprint": "p-h0.02",
                                                     "proposal_fingerprint": "q1"})]

    with pytest.raises(ValueError, match="target"):
        compare_sampling_methods(ordinary, importance, metric="c_failure")


def test_e12_missing_outer_seed_does_not_reuse_one_sde_seed(tmp_path: Path) -> None:
    from orchestration.dynamics_importance_sampling import (
        DynamicsImportanceSamplingRunConfig,
        DynamicsImportanceSamplingRunner,
    )

    seen: list[object] = []

    class Backend:
        def run(self, case):
            seen.append(case.input.get("sde_seed"))
            return RawRunResult(case.case_id, case.target, case.case_kind, RunStatus.SUCCESS)

    class Interpreter:
        def interpret_raw_run_result(self, raw):
            return EvaluationRecord(
                raw.case_id, raw.target, raw.case_kind, raw.status,
                output={"c_collision": 0}, meta=dict(raw.meta),
            )

    DynamicsImportanceSamplingRunner(backend=Backend(), result_interpreter=Interpreter()).run_fixed_samples(
        DynamicsImportanceSamplingRunConfig(
            sampling_plan_path=Path("configs/dynamics_importance_sde_noise_paths_example.yaml"),
            output_root=tmp_path / "run",
            max_samples=2,
            seed=None,
        )
    )

    importance_seeds = seen[2:]
    assert len(set(importance_seeds)) == 2
    assert 17 not in importance_seeds


def test_width_search_skips_records_without_an_available_weight() -> None:
    from evaluation.importance_sampling_comparison import compare_sampling_methods

    ordinary = [_ordinary(f"o{i}", i % 2) for i in range(4)]
    unavailable = EvaluationRecord(
        case_id="q0",
        target="prism",
        case_kind="simple_reliability_dtmc",
        status=RunStatus.EXECUTION_ERROR,
        output={},
        meta={"sampling": {"importance_weight_status": "unavailable"}},
    )
    importance = [unavailable] + [_importance(f"q{i}", i % 2, 0.0, bound=1.0) for i in range(1, 5)]

    result = compare_sampling_methods(ordinary, importance, metric="c_failure", target_width=2.0)

    assert result["importance_samples_to_comparable_target_width"] == 1
