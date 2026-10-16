from pathlib import Path

from runtime.repository.statistical_history import StatisticalHistoryRepository


def test_statistical_history_repository_uses_legacy_default_paths(tmp_path: Path) -> None:
    repository = StatisticalHistoryRepository(
        scenario_name="uturn",
        traces_dir=tmp_path,
    )

    assert repository.dkw_history_path == tmp_path / "uturn_dkw_history.csv"
    assert repository.binomial_ci_history_path == tmp_path / "uturn_binomial_ci_history.csv"


def test_statistical_history_repository_appends_dkw_records_in_order(tmp_path: Path) -> None:
    repository = StatisticalHistoryRepository(
        scenario_name="uturn",
        traces_dir=tmp_path,
    )

    repository.append_dkw_records(
        [
            {
                "stage": 1,
                "task_count": 10,
                "metric": "min_ttc",
                "ess": 40,
                "estimate": 1.2,
                "lower_bound": 1.0,
                "upper_bound": 1.4,
                "interval_width": 0.4,
                "target_epsilon": 0.15,
            },
            {
                "stage": 2,
                "task_count": 20,
                "metric": "min_distance",
                "ess": 80,
                "estimate": 2.5,
                "lower_bound": 2.0,
                "upper_bound": 3.0,
                "interval_width": 1.0,
                "target_epsilon": 0.15,
                "extra_note": "stage-2",
            },
        ]
    )

    rows = repository.read_dkw_rows()

    assert len(rows) == 2
    assert rows[0]["stage"] == "1"
    assert rows[0]["metric"] == "min_ttc"
    assert rows[1]["stage"] == "2"
    assert rows[1]["extra_note"] == "stage-2"


def test_statistical_history_repository_appends_binomial_ci_records(tmp_path: Path) -> None:
    repository = StatisticalHistoryRepository(
        scenario_name="uturn",
        traces_dir=tmp_path,
    )

    repository.append_binomial_ci_record(
        {
            "task_count": 30,
            "metric": "c_collision",
            "method": "wilson",
            "confidence_level": 0.95,
            "sample_size": 120,
            "success_count": 6,
            "estimate": 0.05,
            "lower_bound": 0.02,
            "upper_bound": 0.10,
            "interval_width": 0.08,
            "target_width": 0.02,
        }
    )

    rows = repository.read_binomial_ci_rows()

    assert len(rows) == 1
    assert rows[0]["metric"] == "c_collision"
    assert rows[0]["sample_size"] == "120"
    assert rows[0]["target_width"] == "0.02"
