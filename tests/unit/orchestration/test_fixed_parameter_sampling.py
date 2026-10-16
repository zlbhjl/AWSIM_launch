from __future__ import annotations

import pandas as pd

from contracts.statistics import StatisticalReport, StatisticalRequest
from evaluation.binomial_ci import BinomialCIService
from orchestration.fixed_parameter_sampling import (
    FixedParameterSamplingStrategy,
    FixedParameterSamplingStrategyConfig,
    build_sampling_signature,
)


class FrameRepository:
    def __init__(self, frame: pd.DataFrame | None = None):
        self.frame = frame

    def load_dataset(self) -> pd.DataFrame | None:
        return self.frame


def _config(**overrides: object) -> FixedParameterSamplingStrategyConfig:
    values: dict[str, object] = {
        "target": "prism",
        "case_kind": "simple_reliability_dtmc",
        "params": {"model": "simple_reliability_dtmc", "steps": 20},
        "max_samples": 3,
        "experiment_id": "batch-a",
    }
    values.update(overrides)
    return FixedParameterSamplingStrategyConfig(**values)


def test_fixed_parameter_sampling_emits_one_test_case_per_path() -> None:
    strategy = FixedParameterSamplingStrategy(_config())

    cases = [strategy.next_test_case() for _ in range(4)]

    assert [case.case_id for case in cases[:3]] == [
        "simple_reliability_dtmc-batch-a-0001",
        "simple_reliability_dtmc-batch-a-0002",
        "simple_reliability_dtmc-batch-a-0003",
    ]
    assert cases[3] is None
    assert all(case is not None and case.target == "prism" for case in cases[:3])
    assert [case.input["sample_index"] for case in cases[:3] if case] == [1, 2, 3]
    assert len({case.input["sampling_signature"] for case in cases[:3] if case}) == 1


def test_sampling_signature_is_independent_of_parameter_order() -> None:
    first = build_sampling_signature(
        target="prism",
        case_kind="dtmc",
        params={"steps": 20, "p_fail": 0.1},
    )
    second = build_sampling_signature(
        target="prism",
        case_kind="dtmc",
        params={"p_fail": 0.1, "steps": 20},
    )

    assert first == second


def test_prism_model_check_is_emitted_once_before_sample_paths() -> None:
    repository = FrameRepository()
    strategy = FixedParameterSamplingStrategy(
        _config(run_model_check_once=True),
        dataset_repository=repository,
    )

    model_check = strategy.next_test_case()

    assert model_check is not None
    assert model_check.input["execution_kind"] == "model_check"
    assert model_check.input["record_kind"] == "exact_model_check"
    assert strategy.next_test_case() is None
    assert strategy.waiting_for_result is True

    repository.frame = pd.DataFrame(
        [
            {
                "experiment_id": "batch-a",
                "sampling_signature": strategy.sampling_signature,
                "record_kind": "exact_model_check",
                "status": "success",
                "prism_eventual_failure_probability": 1.0,
                "prism_bounded_failure_probability": 0.25,
            }
        ]
    )
    sample = strategy.next_test_case()

    assert sample is not None
    assert sample.input["execution_kind"] == "sample_path"
    assert sample.input["record_kind"] == "sample"
    assert sample.input["sample_index"] == 1


def test_fixed_sampling_stops_when_matching_binomial_interval_is_sufficient() -> None:
    repository = FrameRepository()
    strategy = FixedParameterSamplingStrategy(
        _config(max_samples=20),
        dataset_repository=repository,
        statistical_service=BinomialCIService(),
        statistical_request=StatisticalRequest(
            method="binomial_ci",
            metric="c_failure",
            confidence=0.95,
            target_width=0.3,
            options={"method": "wilson", "min_samples": 10, "max_samples": 20},
        ),
    )
    repository.frame = pd.DataFrame(
        [
            {
                "experiment_id": "batch-a",
                "sampling_signature": strategy.sampling_signature,
                "status": "success",
                "c_failure": 0,
            }
            for _ in range(10)
        ]
        + [
            {
                "experiment_id": "another-batch",
                "sampling_signature": strategy.sampling_signature,
                "status": "success",
                "c_failure": 1,
            }
            for _ in range(50)
        ]
    )

    assert strategy.next_test_case() is None
    assert strategy.latest_statistical_report is not None
    assert strategy.latest_statistical_report.sample_count == 10
    assert strategy.latest_statistical_report.sufficient is True
    assert "interval sufficient" in strategy.stop_reason


class FakeService:
    def __init__(self, report: StatisticalReport) -> None:
        self._report = report

    def evaluate_request(self, _data, _request) -> StatisticalReport:
        return self._report


def _strategy_with_exact_check(report: StatisticalReport) -> FixedParameterSamplingStrategy:
    strategy = FixedParameterSamplingStrategy(
        _config(run_model_check_once=True),
        statistical_service=FakeService(report),
        statistical_request=StatisticalRequest(method=report.method, metric=report.metric),
    )
    strategy.exact_model_check = {
        "eventual_failure_probability": 1.0,
        "bounded_failure_probability": 0.4227,
    }
    strategy.latest_statistical_report = report
    return strategy


def test_exact_comparison_skipped_for_non_probability_metric() -> None:
    # steps_to_failure_capped is a step count, not a probability: comparing it
    # against bounded_failure_probability would mix units (see EBStop verification
    # against real cluster data, which surfaced this as a nonsensical
    # absolute_estimation_error of ~16).
    report = StatisticalReport(
        method="ebstop",
        metric="steps_to_failure_capped",
        sample_count=376,
        estimate=16.74,
        interval=(12.9, 20.58),
        sufficient=False,
        next_action="stop_max_samples",
        diagnostics={},
    )
    strategy = _strategy_with_exact_check(report)

    payload = strategy.statistical_report_payload()

    exact = payload["exact_model_check"]
    assert exact["ci_contains_bounded_probability"] is None
    assert exact["absolute_estimation_error"] is None
    assert "not directly comparable" in exact["comparison_skipped_reason"]
    assert exact["bounded_failure_probability"] == 0.4227


def test_exact_comparison_computed_for_c_failure_metric() -> None:
    report = StatisticalReport(
        method="sprt",
        metric="c_failure",
        sample_count=22,
        estimate=0.227,
        interval=None,
        sufficient=True,
        next_action="stop",
        diagnostics={},
    )
    strategy = _strategy_with_exact_check(report)

    payload = strategy.statistical_report_payload()

    exact = payload["exact_model_check"]
    assert "comparison_skipped_reason" not in exact
    assert exact["ci_contains_bounded_probability"] is False
    assert exact["absolute_estimation_error"] == abs(0.227 - 0.4227)
