import csv
from pathlib import Path

from runtime.repository.statistical_history import StatisticalHistoryRepository
from tools.analysis.validate_ft4d_and_rule import (
    MeasuredBinomialResult,
    classify_bound_verdict,
    predict_and_rule_sigma_pe,
    predict_and_rule_sigma_pe_interval,
    read_measured_binomial_result,
    render_and_rule_comparison,
    run_validate_ft4d_and_rule,
)

TREE_CONFIG_PATH = (
    Path(__file__).resolve().parents[3]
    / "verification_core"
    / "ft4d"
    / "config"
    / "awsim_and_rule_validation_tree.json"
)


def _write_binomial_ci_history(
    csv_path: Path, *, estimate: float, sample_size: int, half_width: float = 0.05
) -> None:
    repository = StatisticalHistoryRepository(
        scenario_name="_fixture",
        traces_dir=csv_path.parent,
        binomial_ci_history_csv=csv_path,
    )
    repository.append_binomial_ci_record(
        {
            "task_count": sample_size,
            "metric": "c_collision",
            "method": "clopper-pearson",
            "confidence_level": 0.95,
            "sample_size": sample_size,
            "success_count": round(estimate * sample_size),
            "estimate": estimate,
            "lower_bound": max(estimate - half_width, 0.0),
            "upper_bound": min(estimate + half_width, 1.0),
            "interval_width": 2 * half_width,
            "target_width": 0.05,
        }
    )


def test_read_measured_binomial_result_returns_last_row(tmp_path: Path) -> None:
    csv_path = tmp_path / "uturn_binomial_ci_history.csv"
    _write_binomial_ci_history(csv_path, estimate=0.10, sample_size=200)
    _write_binomial_ci_history(csv_path, estimate=0.12, sample_size=250, half_width=0.02)

    result = read_measured_binomial_result(csv_path)

    assert result.estimate == 0.12
    assert result.sample_size == 250
    assert abs(result.lower_bound - 0.10) < 1e-9
    assert abs(result.upper_bound - 0.14) < 1e-9


def test_read_measured_binomial_result_raises_when_missing(tmp_path: Path) -> None:
    missing_path = tmp_path / "does_not_exist.csv"

    try:
        read_measured_binomial_result(missing_path)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "No binomial_ci history rows" in str(exc)


def test_predict_and_rule_sigma_pe_matches_manual_calculation() -> None:
    predicted_min = predict_and_rule_sigma_pe(
        sigma_pf_a=0.5,
        sigma_pb_a=0.10,
        sigma_pf_b=0.5,
        sigma_pb_b=0.08,
        tree_config_path=TREE_CONFIG_PATH,
        and_rule="min",
    )
    predicted_product = predict_and_rule_sigma_pe(
        sigma_pf_a=0.5,
        sigma_pb_a=0.10,
        sigma_pf_b=0.5,
        sigma_pb_b=0.08,
        tree_config_path=TREE_CONFIG_PATH,
        and_rule="product",
    )

    # sigma_pe(HIGH_SPEED) = 0.5 * 0.10 = 0.05, sigma_pe(SHORT_GAP) = 0.5 * 0.08 = 0.04
    assert predicted_min == 0.04
    assert abs(predicted_product - (0.05 * 0.04)) < 1e-9


def test_predict_and_rule_sigma_pe_interval_uses_monotonic_extremes() -> None:
    lower, upper = predict_and_rule_sigma_pe_interval(
        sigma_pf_a=0.5,
        sigma_pb_a_bounds=(0.09, 0.11),
        sigma_pf_b=0.5,
        sigma_pb_b_bounds=(0.07, 0.09),
        tree_config_path=TREE_CONFIG_PATH,
        and_rule="min",
    )

    # sigma_pe(HIGH_SPEED) in [0.045, 0.055], sigma_pe(SHORT_GAP) in [0.035, 0.045]
    # min-rule extremes: min(0.045, 0.035)=0.035, min(0.055, 0.045)=0.045
    assert abs(lower - 0.035) < 1e-9
    assert abs(upper - 0.045) < 1e-9


def test_classify_bound_verdict_holds_when_predicted_strictly_above() -> None:
    verdict = classify_bound_verdict((0.445, 0.455), (0.0225, 0.0275))
    assert verdict.startswith("holds")


def test_classify_bound_verdict_violated_when_predicted_strictly_below() -> None:
    verdict = classify_bound_verdict((0.035, 0.045), (0.085, 0.09))
    assert verdict.startswith("violated")


def test_classify_bound_verdict_inconclusive_when_intervals_overlap() -> None:
    verdict = classify_bound_verdict((0.015, 0.065), (0.0175, 0.0275))
    assert verdict.startswith("inconclusive")


def test_render_and_rule_comparison_reports_violation() -> None:
    rendered = render_and_rule_comparison(
        marginal_a=MeasuredBinomialResult(0.10, 200, 0.09, 0.11),
        marginal_b=MeasuredBinomialResult(0.08, 180, 0.07, 0.09),
        intersection=MeasuredBinomialResult(0.35, 150, 0.34, 0.36),
        sigma_pf_a=0.5,
        sigma_pf_b=0.5,
        predicted_sigma_pe_min=(0.035, 0.045),
        predicted_sigma_pe_product=(0.001575, 0.002475),
    )

    assert "Marginal HIGH_SPEED   : sigma_pb=0.1000  [0.0900, 0.1100]  (n=200 samples)" in rendered
    assert "measured intersection sigma_pe : point=0.087500" in rendered
    assert "verdict: violated" in rendered
    assert "fewer total samples used by: direct measurement" in rendered


def test_run_validate_ft4d_and_rule_prints_comparison_and_writes_csv(
    tmp_path: Path, capsys
) -> None:
    marginal_a = tmp_path / "high_speed_marginal.csv"
    marginal_b = tmp_path / "short_gap_marginal.csv"
    intersection = tmp_path / "intersection.csv"
    _write_binomial_ci_history(marginal_a, estimate=0.10, sample_size=200, half_width=0.01)
    _write_binomial_ci_history(marginal_b, estimate=0.08, sample_size=180, half_width=0.01)
    _write_binomial_ci_history(intersection, estimate=0.35, sample_size=150, half_width=0.01)
    output_csv = tmp_path / "comparison.csv"

    exit_code = run_validate_ft4d_and_rule(
        [
            "--marginal-a-history",
            str(marginal_a),
            "--marginal-b-history",
            str(marginal_b),
            "--intersection-history",
            str(intersection),
            "--output-csv",
            str(output_csv),
        ]
    )

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "FT4D AND-rule validation" in stdout
    assert "verdict: violated" in stdout

    with output_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert float(rows[0]["sigma_pb_intersection_measured"]) == 0.35
    assert rows[0]["min_rule_verdict"].startswith("violated")


def test_run_validate_ft4d_and_rule_returns_one_when_history_missing(
    tmp_path: Path, capsys
) -> None:
    exit_code = run_validate_ft4d_and_rule(
        [
            "--marginal-a-history",
            str(tmp_path / "missing_a.csv"),
            "--marginal-b-history",
            str(tmp_path / "missing_b.csv"),
            "--intersection-history",
            str(tmp_path / "missing_c.csv"),
        ]
    )

    assert exit_code == 1
    stdout = capsys.readouterr().out
    assert "error:" in stdout
