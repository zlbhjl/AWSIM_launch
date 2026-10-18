import math

import numpy as np
import pandas as pd
import pytest

from contracts.experiment import Guarantee
from contracts.statistics import StatisticalReport, StatisticalRequest
from evaluation.stopping_rules import (
    BinomialFixedStrict,
    BinomialSequentialAnytime,
    DKWFixedStrict,
    DKWSequentialAnytime,
    anytime_delta,
    clopper_pearson,
    dkw_quantile_interval,
    sprt_wald,
)


def _frame(metric: str, values, *, statuses=None) -> pd.DataFrame:
    rows = []
    for index, value in enumerate(values, start=1):
        rows.append({"sample_index": index, metric: value, "status": (statuses or {}).get(index, "success")})
    return pd.DataFrame(rows)


def test_anytime_budget_never_exceeds_delta() -> None:
    total = sum(anytime_delta(0.05, look) for look in range(1, 100_000))

    assert total < 0.05
    assert anytime_delta(0.05, 1) == pytest.approx(6 * 0.05 / math.pi**2)


def test_clopper_pearson_matches_known_values() -> None:
    lower, upper = clopper_pearson(5, 100, 0.05)

    assert lower == pytest.approx(0.01643, abs=1e-4)
    assert upper == pytest.approx(0.11284, abs=1e-4)
    assert clopper_pearson(0, 10, 0.05)[0] == 0.0
    assert clopper_pearson(10, 10, 0.05)[1] == 1.0


def test_dkw_interval_reaches_the_support_when_the_band_is_wide() -> None:
    values = np.array([0.40, 0.41, 0.42, 0.43])

    lower, upper = dkw_quantile_interval(values, 0.05, 0.05, lower_support=0.0)

    # n=4: epsilon ~0.68, so q - epsilon < 0 and the lower bound is the support;
    # q + epsilon ~0.73 gives rank ceil(4 * 0.73) = 3 for the upper bound.
    assert lower == 0.0
    assert upper == 0.42
    assert dkw_quantile_interval(values, 0.9, 0.05)[1] == math.inf


def test_dkw_interval_uses_order_statistics_for_large_n() -> None:
    values = np.linspace(0.0, 1.0, 1001)

    lower, upper = dkw_quantile_interval(values, 0.5, 0.05)
    epsilon = math.sqrt(math.log(2 / 0.05) / (2 * 1001))

    assert lower == pytest.approx(0.5 - epsilon, abs=2e-3)
    assert upper == pytest.approx(0.5 + epsilon, abs=2e-3)


def test_binomial_fixed_strict_waits_for_n_and_uses_the_first_n_samples() -> None:
    rule = BinomialFixedStrict(metric="c_failure", sample_size=4)

    assert not rule.decide(_frame("c_failure", [1, 0, 0]), issued=3).stop
    decision = rule.decide(_frame("c_failure", [1, 0, 0, 0, 1, 1]), issued=6)

    assert decision.stop
    assert decision.report.sample_count == 4
    assert decision.report.estimate == 0.25
    assert decision.report.diagnostics["guarantee"] == Guarantee.FIXED_SAMPLE.value


def test_failed_runs_are_not_counted() -> None:
    rule = BinomialFixedStrict(metric="c_failure", sample_size=3)

    decision = rule.decide(_frame("c_failure", [1, 0, 0, 1], statuses={2: "timeout"}), issued=4)

    assert decision.report.estimate == pytest.approx(2 / 3)


def test_sequential_rule_spends_budget_only_on_new_data() -> None:
    rule = BinomialSequentialAnytime(metric="c_failure", target_width=0.01, max_samples=1000)
    data = _frame("c_failure", [0] * 20)

    first = rule.decide(data, issued=20)
    again = rule.decide(data, issued=21)
    more = rule.decide(_frame("c_failure", [0] * 21), issued=21)

    assert not first.stop and not again.stop and not more.stop
    assert again.report.diagnostics["look"] == 1
    assert more.report.diagnostics["look"] == 2


def test_sequential_rule_stops_at_the_target_width_or_max_samples() -> None:
    wide = BinomialSequentialAnytime(metric="c_failure", target_width=0.9, max_samples=1000)
    capped = DKWSequentialAnytime(metric="t", target_width=1e-9, max_samples=50, lower_support=0.0)

    assert wide.decide(_frame("c_failure", [0] * 30), issued=30).report.next_action == "stop"
    capped_decision = capped.decide(_frame("t", np.linspace(0.1, 5.0, 50)), issued=50)
    assert capped_decision.stop
    assert capped_decision.report.next_action == "stop_max_samples"


def test_dkw_fixed_strict_evaluates_once_at_n() -> None:
    rule = DKWFixedStrict(metric="t", sample_size=200, lower_support=0.0)
    values = -np.log1p(-np.linspace(0.001, 0.999, 300))

    assert not rule.decide(_frame("t", values[:199]), issued=199).stop
    decision = rule.decide(_frame("t", values), issued=300)

    assert decision.stop
    assert decision.report.sample_count == 200
    assert decision.report.interval[0] <= decision.report.estimate <= decision.report.interval[1]


def test_sprt_wald_labels_the_existing_service() -> None:
    class Service:
        def evaluate_request(self, data, request):
            return StatisticalReport("sprt", "c_failure", 10, 0.1, None, True, "stop", {"verdict": "h0"})

    rule = sprt_wald(Service(), StatisticalRequest(method="sprt", metric="c_failure"))
    decision = rule.decide(None, issued=10)

    assert decision.stop
    assert decision.report.diagnostics["stopping_rule"] == "sprt_wald"
    assert decision.report.diagnostics["guarantee"] == Guarantee.ANYTIME.value
