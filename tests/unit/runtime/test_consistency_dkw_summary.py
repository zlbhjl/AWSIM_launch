from pathlib import Path

from contracts.statistics import StatisticalReport
from runtime.repository.consistency_dkw_summary import (
    ConsistencyDkwSummaryRepository,
)


def test_consistency_dkw_summary_repository_appends_expected_row(tmp_path: Path) -> None:
    repo = ConsistencyDkwSummaryRepository(
        scenario_name="uturn",
        traces_dir=tmp_path,
    )

    repo.append_report(
        classification="consistent",
        report=StatisticalReport(
            method="dkw",
            metric="min_ttc",
            sample_count=12,
            estimate=0.8,
            interval=(0.7, 0.9),
            sufficient=True,
            next_action="stop",
            diagnostics={"status": "success"},
        ),
        confidence_level=0.95,
        target_epsilon=0.15,
    )

    assert repo.read_rows() == [
        {
            "classification": "consistent",
            "metric": "min_ttc",
            "method": "dkw",
            "sample_count": "12",
            "confidence_level": "0.95",
            "estimate": "0.8",
            "lower_bound": "0.7",
            "upper_bound": "0.9",
            "interval_width": "0.20000000000000007",
            "target_epsilon": "0.15",
            "sufficient": "True",
            "next_action": "stop",
            "status": "success",
            "message": "",
        }
    ]


def test_consistency_dkw_summary_repository_reads_latest_rows_by_classification(
    tmp_path: Path,
) -> None:
    repo = ConsistencyDkwSummaryRepository(
        scenario_name="uturn",
        traces_dir=tmp_path,
    )

    repo.append_report(
        classification="consistent",
        report=StatisticalReport(
            method="dkw",
            metric="min_ttc",
            sample_count=10,
            estimate=0.7,
            interval=(0.6, 0.8),
            sufficient=False,
            next_action="collect_more_samples",
            diagnostics={"status": "success"},
        ),
        confidence_level=0.95,
        target_epsilon=0.15,
    )
    repo.append_report(
        classification="stochastic",
        report=StatisticalReport(
            method="dkw",
            metric="min_ttc",
            sample_count=5,
            estimate=None,
            interval=None,
            sufficient=False,
            next_action="error",
            diagnostics={"status": "error", "message": "empty subset"},
        ),
        confidence_level=0.95,
        target_epsilon=0.15,
    )
    repo.append_report(
        classification="consistent",
        report=StatisticalReport(
            method="dkw",
            metric="min_ttc",
            sample_count=12,
            estimate=0.8,
            interval=(0.7, 0.9),
            sufficient=True,
            next_action="stop",
            diagnostics={"status": "success"},
        ),
        confidence_level=0.95,
        target_epsilon=0.15,
    )

    latest = repo.read_latest_rows_by_classification()

    assert latest["consistent"]["sample_count"] == "12"
    assert latest["consistent"]["next_action"] == "stop"
    assert latest["stochastic"]["status"] == "error"
    assert latest["stochastic"]["message"] == "empty subset"
