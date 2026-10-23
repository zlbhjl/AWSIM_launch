from __future__ import annotations

import pandas as pd

from contracts.statistics import StatisticalReport
from orchestration.dkw_mode import DKWModeRunner
from orchestration.fixed_parameter_sampling import FixedParameterSamplingStrategyConfig
from orchestration.fixed_parameter_dkw import (
    PrismDkwSamplingStrategy,
    _RecordingDKWService,
)


class FrameRepository:
    def __init__(self, frame: pd.DataFrame | None = None):
        self.frame = frame

    def load_dataset(self) -> pd.DataFrame | None:
        return self.frame


def _sample_rows(strategy, tasks, value=5.0) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "experiment_id": strategy.experiment_id,
                "sampling_signature": strategy.sampling_signature,
                "record_kind": "sample",
                "status": "success",
                "reason": task.reason,
                "loop_num": index,
                "steps_to_failure_capped": value,
            }
            for index, task in enumerate(tasks, start=1)
        ]
    )


class FakeDKWService:
    """Reports 'collect_more_samples' until told to stop."""

    def __init__(self, *, stop_after_calls: int | None = None) -> None:
        self.calls = 0
        self.stop_after_calls = stop_after_calls

    def evaluate_request(self, _data, request):
        self.calls += 1
        should_stop = (
            self.stop_after_calls is not None and self.calls >= self.stop_after_calls
        )
        return StatisticalReport(
            method="dkw",
            metric=request.metric,
            sample_count=self.calls * 5,
            estimate=1.0,
            interval=(0.9, 1.1) if should_stop else (0.0, 5.0),
            sufficient=should_stop,
            next_action="stop" if should_stop else "collect_more_samples",
            diagnostics={"status": "success", "filtered_df": pd.DataFrame()},
        )


def _prism_exact_settings() -> dict[str, object]:
    from targets.prism.plugin import PLUGIN

    return {
        "exact_probability_columns": PLUGIN.exact_probability_columns,
        "exact_probability_metric": PLUGIN.exact_probability_metric,
        "exact_model_check_failure_reason": PLUGIN.exact_model_check_failure_reason,
    }


def _config(**overrides: object) -> FixedParameterSamplingStrategyConfig:
    values: dict[str, object] = {
        "target": "prism",
        "case_kind": "simple_reliability_dtmc",
        "params": {"model": "simple_reliability_dtmc", "steps": 20},
        "max_samples": 100,
        "experiment_id": "dkw-batch",
        "run_model_check_once": False,
        # The exact model check of PRISM, as the orchestrator passes it from the plugin.
        **_prism_exact_settings(),
    }
    values.update(overrides)
    return FixedParameterSamplingStrategyConfig(**values)


def _runner(dkw_service, *, max_samples: int = 100) -> DKWModeRunner:
    return DKWModeRunner.from_runtime_config(
        param_names=[],
        target_metric="steps_to_failure_capped",
        target_metrics=["steps_to_failure_capped"],
        total_delta=0.05,
        target_epsilon=0.15,
        base_samples=2,
        region="custom",
        pure_smc=True,
        simultaneous=False,
        max_samples=max_samples,
        dkw_service=_RecordingDKWService(dkw_service),
        case_kind="simple_reliability_dtmc",
    )


def test_sequential_mode_issues_fixed_point_sample_tasks() -> None:
    repository = FrameRepository(pd.DataFrame())
    strategy = PrismDkwSamplingStrategy(
        _config(),
        dataset_repository=repository,
        dkw_mode_runner=_runner(FakeDKWService()),
        sequential=True,
    )

    first = strategy.next_test_case()
    second = strategy.next_test_case()

    assert first is not None and second is not None
    assert first.input["model"] == "simple_reliability_dtmc"
    assert first.input["execution_kind"] == "sample_path"
    assert "SMC" in first.reason
    assert first.case_id != second.case_id
    # Stage 1 needs base_samples=2 results before its single evaluation.
    assert strategy.latest_statistical_report is None


def test_sequential_mode_bootstraps_first_sample_without_evaluating_on_cold_start() -> None:
    # DKWModeRunner.handle_sequential evaluates before dispatching a new task.
    # With zero prior samples that evaluation fails outright (nothing to
    # compute a bound from) and would be treated as a terminal stop, so a
    # fresh PRISM run must skip straight to issuing the first sample instead
    # of calling handle_sequential/evaluate_request on an empty dataset.
    repository = FrameRepository(pd.DataFrame())
    dkw_service = FakeDKWService()
    strategy = PrismDkwSamplingStrategy(
        _config(),
        dataset_repository=repository,
        dkw_mode_runner=_runner(dkw_service),
        sequential=True,
    )

    first = strategy.next_test_case()

    assert first is not None
    assert dkw_service.calls == 0
    assert strategy.dkw_state.dispatched_task_count == 1


def test_sequential_mode_stops_when_dkw_converges() -> None:
    repository = FrameRepository(pd.DataFrame())
    strategy = PrismDkwSamplingStrategy(
        _config(),
        dataset_repository=repository,
        dkw_mode_runner=_runner(FakeDKWService(stop_after_calls=1)),
        sequential=True,
    )

    bootstrap = strategy.next_test_case()
    assert bootstrap is not None  # cold start: first sample is issued unevaluated
    second = strategy.next_test_case()
    assert second is not None  # stage 1 (n=2) is not complete yet

    repository.frame = _sample_rows(strategy, [bootstrap, second])
    result = strategy.next_test_case()

    assert result is None
    assert "SMC Verification Complete" in strategy.stop_reason
    assert strategy.latest_statistical_report is not None
    assert strategy.latest_statistical_report.next_action == "stop"


def test_fixed_mode_dispatches_all_samples_before_evaluating() -> None:
    repository = FrameRepository(pd.DataFrame())
    dkw_service = FakeDKWService(stop_after_calls=1)
    strategy = PrismDkwSamplingStrategy(
        _config(),
        dataset_repository=repository,
        dkw_mode_runner=_runner(dkw_service, max_samples=3),
        sequential=False,
    )

    cases = [strategy.next_test_case() for _ in range(3)]
    assert all(case is not None for case in cases)
    assert dkw_service.calls == 0

    result = strategy.next_test_case()
    assert result is None
    assert dkw_service.calls == 1
    assert "Fixed Sampling + DKW Complete" in strategy.stop_reason


def test_model_check_is_issued_once_before_dkw_sampling_begins() -> None:
    repository = FrameRepository()
    strategy = PrismDkwSamplingStrategy(
        _config(run_model_check_once=True),
        dataset_repository=repository,
        dkw_mode_runner=_runner(FakeDKWService()),
        sequential=True,
    )

    model_check = strategy.next_test_case()
    assert model_check is not None
    assert model_check.input["record_kind"] == "exact_model_check"
    assert strategy.next_test_case() is None
    assert strategy.waiting_for_result is True

    repository.frame = pd.DataFrame(
        [
            {
                "experiment_id": strategy.experiment_id,
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
    assert sample.input["record_kind"] == "sample"


def test_statistical_report_payload_skips_probability_comparison_for_step_count_metric() -> None:
    repository = FrameRepository()
    strategy = PrismDkwSamplingStrategy(
        _config(run_model_check_once=True),
        dataset_repository=repository,
        dkw_mode_runner=_runner(FakeDKWService()),
        sequential=True,
    )
    strategy.next_test_case()  # issues the model-check TestCase
    repository.frame = pd.DataFrame(
        [
            {
                "experiment_id": strategy.experiment_id,
                "sampling_signature": strategy.sampling_signature,
                "record_kind": "exact_model_check",
                "status": "success",
                "prism_eventual_failure_probability": 1.0,
                "prism_bounded_failure_probability": 0.25,
            }
        ]
    )

    sample_task = strategy.next_test_case()  # captures exact_model_check, bootstraps 1st sample
    assert sample_task is not None
    second_task = strategy.next_test_case()
    assert second_task is not None

    repository.frame = pd.concat(
        [repository.frame, _sample_rows(strategy, [sample_task, second_task])], ignore_index=True
    )
    strategy.next_test_case()  # stage 1 (n=2) is complete and evaluated

    payload = strategy.statistical_report_payload()
    assert payload is not None
    assert payload["metric"] == "steps_to_failure_capped"
    assert payload["exact_model_check"]["ci_contains_bounded_probability"] is None
    assert "comparison_skipped_reason" in payload["exact_model_check"]
