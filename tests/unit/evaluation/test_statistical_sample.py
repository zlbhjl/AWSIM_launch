"""Rows are put in sample order and cut at the first unresolved sample (plan §3.1)."""

from __future__ import annotations

import pandas as pd

from contracts.statistics import StatisticalReport
from evaluation.statistical_sample import prepare_statistical_sample
from orchestration.fixed_parameter_sampling import FixedParameterSamplingStrategy, FixedParameterSamplingStrategyConfig


def _rows(*items):
    return pd.DataFrame([{"sample_index": index, "status": status, "c_event": value} for index, status, value in items])


def test_rows_in_arrival_order_are_sorted_and_cut_at_the_first_missing_sample() -> None:
    # Samples 3 and 5 finished early (events); sample 2 is still running.
    data = _rows((3, "success", 1), (1, "success", 0), (5, "success", 1), (4, "success", 0))
    prepared = prepare_statistical_sample(data, issued={1, 2, 3, 4, 5})
    assert prepared.frame["sample_index"].tolist() == [1]
    assert prepared.summary["cut_at_sample_index"] == 2 and prepared.summary["waiting_samples"] == 3


def test_a_sample_waiting_for_a_retry_cuts_the_prefix_until_it_resolves() -> None:
    data = _rows((1, "success", 0), (2, "timeout", 0), (3, "success", 1))
    assert prepare_statistical_sample(data, max_attempts=3).frame["sample_index"].tolist() == [1]
    retried = pd.concat([data, _rows((2, "success", 1))], ignore_index=True)
    prepared = prepare_statistical_sample(retried, max_attempts=3)
    assert prepared.frame["sample_index"].tolist() == [1, 2, 3]
    assert prepared.frame["c_event"].tolist() == [0, 1, 1]
    assert prepared.summary["retried_samples"] == 1 and prepared.summary["attempts_to_success"] == {"2": 1}
    assert not prepared.degraded


def test_exhausted_failures_and_lost_samples_are_resolved_but_degrade_the_result() -> None:
    data = _rows((1, "success", 0), (2, "execution_error", 0), (2, "execution_error", 0), (2, "execution_error", 0),
                 (4, "success", 1))
    prepared = prepare_statistical_sample(data, max_attempts=3, abandoned={3})
    assert prepared.frame["sample_index"].tolist() == [1, 2, 4]  # the failed row of 2 is kept; rules drop it
    assert prepared.summary["unresolved_samples"] == 1 and prepared.summary["lost_samples"] == 1
    assert prepared.summary["unresolved_sample_indexes"] == [2, 3]
    assert prepared.degraded


def test_a_second_success_of_the_same_sample_is_a_duplicate() -> None:
    prepared = prepare_statistical_sample(_rows((1, "success", 1), (1, "success", 0), (2, "success", 0)))
    assert prepared.frame["c_event"].tolist() == [1, 0]
    assert prepared.summary["duplicate_rows"] == 1


def test_rows_already_in_order_are_returned_unchanged() -> None:
    data = _rows((1, "success", 0), (2, "success", 1))
    assert prepare_statistical_sample(data).frame is data
    assert prepare_statistical_sample(data.drop(columns="sample_index")).frame is not None


class _Repository:
    def __init__(self, frame):
        self.frame = frame

    def load_dataset(self):
        return self.frame


class _RecordingService:
    def __init__(self):
        self.seen = []

    def evaluate_request(self, data, request):
        self.seen.append(None if data is None else data["sample_index"].tolist())
        return StatisticalReport(method="binomial_ci", metric="c_event", sample_count=0, estimate=None,
                                 interval=None, sufficient=False, next_action="collect_more_samples", diagnostics={})


def test_the_strategy_evaluates_only_the_ordered_prefix() -> None:
    from contracts.statistics import StatisticalRequest

    service = _RecordingService()
    strategy = FixedParameterSamplingStrategy(
        FixedParameterSamplingStrategyConfig(target="bernoulli", case_kind="bernoulli", params={}, max_samples=10),
        dataset_repository=_Repository(None), statistical_service=service,
        statistical_request=StatisticalRequest(method="binomial_ci", metric="c_event"),
    )
    for _ in range(4):
        strategy.next_test_case()  # issues samples 1..4
    rows = _rows((3, "success", 1), (1, "success", 0), (4, "success", 1))
    rows["experiment_id"], rows["sampling_signature"] = strategy.experiment_id, strategy.sampling_signature
    strategy.dataset_repository.frame = rows

    strategy.next_test_case()

    assert service.seen[-1] == [1]  # samples 3 and 4 wait for sample 2
    assert strategy.latest_sample_summary["cut_at_sample_index"] == 2


def test_unresolved_samples_lower_the_guarantee_and_report_both_ends() -> None:
    from contracts.statistics import StatisticalRequest

    strategy = FixedParameterSamplingStrategy(
        FixedParameterSamplingStrategyConfig(target="bernoulli", case_kind="bernoulli", params={}, max_samples=10),
        dataset_repository=_Repository(None), statistical_service=_RecordingService(),
        statistical_request=StatisticalRequest(method="binomial_ci", metric="c_event", confidence=0.95),
    )
    strategy.max_attempts = 3
    for _ in range(3):
        strategy.next_test_case()
    rows = _rows((1, "success", 1), (2, "timeout", 0), (2, "timeout", 0), (2, "timeout", 0), (3, "success", 0))
    rows["experiment_id"], rows["sampling_signature"] = strategy.experiment_id, strategy.sampling_signature
    strategy.dataset_repository.frame = rows
    strategy.next_test_case()

    payload = strategy.statistical_report_payload()

    assert payload["guarantee"] == "approximate"
    bounds = payload["unresolved_bounds"]
    assert bounds["estimate_if_all_0"] == 1 / 3 and bounds["estimate_if_all_1"] == 2 / 3
    assert bounds["interval"][0] < 1 / 3 < 2 / 3 < bounds["interval"][1]
