"""Targets declare their metrics; methods that do not fit are refused (plan §3.2)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from contracts.metric_spec import binary, bounded, check_method_fits, real
from contracts.statistics import StatisticalRequest
from orchestration.fixed_parameter_sampling import FixedParameterSamplingStrategy, FixedParameterSamplingStrategyConfig
from orchestration.orchestrator import OrchestratorConfig, _apply_metric_specs


def test_specs_count_values_outside_the_declaration() -> None:
    assert binary().violations([0, 1, 0.5]) == 1
    assert bounded(0, 1).violations([0, 1, 1.5, float("inf")]) == 2
    assert real().violations([float("inf"), -3.0]) == 0
    with pytest.raises(ValueError, match="lower < upper"):
        bounded(1, 1)


@pytest.mark.parametrize(("method", "spec"), [("ebstop", real()), ("binomial_ci", real()), ("sprt", bounded(0, 2))])
def test_methods_that_do_not_fit_are_refused(method, spec) -> None:
    with pytest.raises(ValueError, match=f"{method} cannot be used"):
        check_method_fits("t", "m", method, spec)
    check_method_fits("t", "m", "dkw", spec)


def test_ebstop_takes_the_declared_range_and_may_only_narrow_it() -> None:
    config = OrchestratorConfig(output="o.jsonl", target="bernoulli", run_mode="ebstop")
    assert _apply_metric_specs(config).ebstop_value_range == (0.0, 1.0)  # score is declared in [0, 1]
    narrowed = OrchestratorConfig(output="o.jsonl", target="bernoulli", run_mode="ebstop", ebstop_value_range=(0.0, 0.5))
    assert _apply_metric_specs(narrowed).ebstop_value_range == (0.0, 0.5)
    widened = OrchestratorConfig(output="o.jsonl", target="bernoulli", run_mode="ebstop", ebstop_value_range=(0.0, 2.0))
    with pytest.raises(ValueError, match="may only narrow"):
        _apply_metric_specs(widened)


def test_prism_declares_its_step_metric_from_the_horizon() -> None:
    config = OrchestratorConfig(output="o.jsonl", target="prism", run_mode="ebstop", params={"steps": 20},
                                ebstop_target="steps_to_failure_capped")
    assert _apply_metric_specs(config).ebstop_value_range == (0.0, 21.0)


def test_a_value_outside_the_declaration_stops_with_an_error(tmp_path: Path) -> None:
    class Repository:
        frame = None

        def load_dataset(self):
            return self.frame

    class Service:
        def evaluate_request(self, data, request):
            assert data is None, "rows that do not fit must not reach the statistics"
            from contracts.statistics import StatisticalReport

            return StatisticalReport(method="binomial_ci", metric="c_failure", sample_count=0, estimate=None,
                                     interval=None, sufficient=False, next_action="collect_more_samples", diagnostics={})

    strategy = FixedParameterSamplingStrategy(
        FixedParameterSamplingStrategyConfig(target="bernoulli", case_kind="bernoulli", params={}, max_samples=5),
        dataset_repository=Repository(), statistical_service=Service(),
        statistical_request=StatisticalRequest(method="binomial_ci", metric="c_failure"),
    )
    strategy.next_test_case()
    strategy.dataset_repository.frame = pd.DataFrame([{
        "sample_index": 1, "status": "success", "c_failure": 0.5,
        "experiment_id": strategy.experiment_id, "sampling_signature": strategy.sampling_signature,
    }])

    assert strategy.next_test_case() is None
    assert "do not fit its declaration (0/1)" in strategy.stop_reason
