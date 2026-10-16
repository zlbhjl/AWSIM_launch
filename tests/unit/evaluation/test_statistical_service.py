import pandas as pd

from evaluation.statistical_service import StatisticalEvaluationService


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "loop_num": [1, 2, 3, 4, 5],
            "dx0": [10.0, 10.5, 11.0, 11.5, 12.0],
            "ego_speed": [30.0, 30.5, 31.0, 31.5, 32.0],
            "c_collision": [0, 1, 0, 1, 1],
            "min_ttc": [1.0, 1.5, 2.0, 0.8, 0.9],
            "min_distance": [3.0, 2.5, 4.0, 1.8, 1.5],
            "reason": [
                "BINOMIAL_CI: 1",
                "BINOMIAL_CI: 2",
                "other",
                "BINOMIAL_CI: 3",
                "other",
            ],
        }
    )


def test_statistical_service_summarizes_dkw_multiple() -> None:
    service = StatisticalEvaluationService(feature_names=["dx0", "ego_speed"])

    summary = service.summarize_dkw_multiple(
        _sample_df(),
        target_columns=["min_ttc", "min_distance"],
        q=0.5,
        delta_total=0.1,
    )

    assert summary["status"] == "success"
    assert set(summary["metrics"]) == {"min_ttc", "min_distance"}


def test_statistical_service_summarizes_binomial_ci() -> None:
    service = StatisticalEvaluationService(feature_names=["dx0", "ego_speed"])

    summary = service.summarize_binomial_ci(
        _sample_df(),
        target_column="c_collision",
        reason_pattern="BINOMIAL_CI:",
    )

    assert summary["status"] == "success"
    assert summary["sample_size"] == 3


def test_statistical_service_calculates_dkw_quantile() -> None:
    service = StatisticalEvaluationService(feature_names=["dx0", "ego_speed"])

    result = service.calculate_dkw_quantile(
        _sample_df(),
        target_column="min_ttc",
        q=0.5,
        delta=0.1,
    )

    assert result is not None
    assert result["lower_bound"] <= result["upper_bound"]
