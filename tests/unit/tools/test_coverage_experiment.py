import math
from dataclasses import replace

import pytest

from tools.validation.coverage_experiment import (
    SCENARIOS,
    Scenario,
    _run_task,
    clopper_pearson,
    judge,
    run_dkw_runner_once,
    run_iid_once,
    run_importance_fixed_once,
    summarize,
)

BY_NAME = {scenario.name: scenario for scenario in SCENARIOS}


def test_scenario_names_are_unique_and_engines_known() -> None:
    assert len(BY_NAME) == len(SCENARIOS)
    assert {scenario.engine for scenario in SCENARIOS} == {"iid", "dkw_runner", "importance_fixed"}


def test_iid_engine_stops_at_the_fixed_sample_size(tmp_path) -> None:
    scenario = replace(
        BY_NAME["binomial_cp_fixed_n100_p0.05"],
        config={"binomial_method": "clopper-pearson", "binomial_target_width": 1.0,
                "binomial_min_samples": 20, "max_samples": 20},
    )

    result = run_iid_once(scenario, seed=1, workdir=tmp_path)

    assert result["sample_count"] == 20
    assert result["interval"][0] <= result["estimate"] <= result["interval"][1]


def test_iid_engine_is_reproducible_for_a_seed(tmp_path) -> None:
    scenario = BY_NAME["sprt_true_p1"]

    first = run_iid_once(scenario, seed=5, workdir=tmp_path)
    second = run_iid_once(scenario, seed=5, workdir=tmp_path)

    assert first == second
    assert first["verdict"] in {"h0", "h1", "continue"}


def test_dkw_runner_fixed_evaluates_once_after_all_samples(tmp_path) -> None:
    scenario = replace(
        BY_NAME["dkw_runner_fixed_n500"],
        config={"total_delta": 0.05, "target_epsilon": 10.0, "base_samples": 10, "max_samples": 60},
    )

    result = run_dkw_runner_once(scenario, seed=2, workdir=tmp_path)

    assert result["sample_count"] == 60
    assert result["interval"] is not None
    assert result["stop_reason"].startswith("Fixed Sampling + DKW Complete")


def test_judge_checks_the_interval_against_the_exact_truth() -> None:
    binomial = BY_NAME["binomial_cp_fixed_n100_p0.05"]
    dkw = BY_NAME["dkw_runner_fixed_n500"]

    assert judge(binomial, {"interval": [0.01, 0.06]})["correct"]
    assert not judge(binomial, {"interval": [0.06, 0.09]})["correct"]
    assert judge(dkw, {"interval": [0.0, 0.1]})["truth"] == pytest.approx(-math.log(0.95))
    assert not judge(dkw, {"interval": None})["correct"]


def test_judge_counts_only_the_wrong_sprt_decision_as_an_error() -> None:
    true_p0 = BY_NAME["sprt_true_p0"]

    assert judge(true_p0, {"verdict": "h0"})["correct"]
    assert judge(true_p0, {"verdict": "continue"})["correct"]
    assert not judge(true_p0, {"verdict": "h1"})["correct"]


def test_judge_uses_the_relative_error_for_ebstop() -> None:
    scenario = BY_NAME["ebstop_beta25_eps0.1"]
    mean = 2.0 / 7.0

    assert judge(scenario, {"estimate": mean * 1.09})["correct"]
    assert not judge(scenario, {"estimate": mean * 1.11})["correct"]


def test_clopper_pearson_bounds() -> None:
    lower, upper = clopper_pearson(95, 100)

    assert lower == pytest.approx(0.887, abs=1e-3)
    assert upper == pytest.approx(0.984, abs=1e-3)
    assert clopper_pearson(0, 10)[0] == 0.0
    assert clopper_pearson(10, 10)[1] == 1.0


def test_run_task_and_summary_report_the_correct_rate() -> None:
    scenario = Scenario(
        "tiny", "iid", "binomial_ci", "tiny", {"p_failure": 0.2},
        {"binomial_method": "clopper-pearson", "binomial_target_width": 1.0,
         "binomial_min_samples": 10, "max_samples": 10},
    )

    runs = [_run_task((scenario, seed)) for seed in range(3)]
    summary = summarize([scenario], runs)

    assert summary[0]["reps"] == 3
    assert 0.0 <= summary[0]["correct_rate"] <= 1.0
    assert summary[0]["truth"] == 0.2


def test_exploration_rows_are_prefilled_and_not_counted_as_new_samples(tmp_path) -> None:
    scenario = replace(
        BY_NAME["dkw_runner_fixed_mixed_explore300"],
        config={"total_delta": 0.05, "target_epsilon": 10.0, "base_samples": 10, "max_samples": 30,
                "pure_smc": False, "exploration_rows": 20},
    )

    result = run_dkw_runner_once(scenario, seed=4, workdir=tmp_path)

    assert result["sample_count"] == 30
    assert result["interval"] is not None


def test_importance_engine_reports_every_interval_and_judges_them(tmp_path) -> None:
    scenario = replace(
        BY_NAME["is_ou_a_3sd_defensive_u3_b0.2_n1000"],
        config={**BY_NAME["is_ou_a_3sd_defensive_u3_b0.2_n1000"].config, "max_samples": 30},
    )

    result = run_importance_fixed_once(scenario, seed=5, workdir=tmp_path)
    verdict = judge(scenario, result)

    assert result["stop_reason"] == "complete"
    assert result["interval_is_hoeffding"] is not None and result["interval_is_bernstein"] is not None
    assert verdict["truth"] == pytest.approx(1.3498980316e-3, rel=1e-8)
    assert verdict["correct_is_hoeffding"] is True
    assert verdict["correct"] == verdict["correct_is_normal"]


def test_importance_engine_is_reproducible_and_needs_no_bound_for_direct_q(tmp_path) -> None:
    scenario = replace(
        BY_NAME["is_ou_a_3sd_gaussian_u3_n1000"],
        config={**BY_NAME["is_ou_a_3sd_gaussian_u3_n1000"].config, "max_samples": 20},
    )

    first = run_importance_fixed_once(scenario, seed=9, workdir=tmp_path)
    second = run_importance_fixed_once(scenario, seed=9, workdir=tmp_path)

    assert first["estimate_is"] == second["estimate_is"]
    assert first["interval_is_hoeffding"] is None
    assert judge(scenario, first)["correct_is_hoeffding"] is None


def test_importance_summary_reports_coverage_per_interval(tmp_path) -> None:
    scenario = replace(
        BY_NAME["is_ou_a_3sd_q_equals_p_n1000"],
        config={**BY_NAME["is_ou_a_3sd_q_equals_p_n1000"].config, "max_samples": 20},
    )
    runs = [_run_task((scenario, seed)) for seed in (1, 2)]

    entry = summarize([scenario], runs)[0]

    assert set(entry["intervals"]) == {"mc_wilson", "is_normal", "is_hoeffding", "is_bernstein"}
    assert entry["estimators"]["is"]["no_event_rate"] >= 0.0
    assert entry["intervals"]["is_hoeffding"]["missing_rate"] == 0.0
