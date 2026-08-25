from pathlib import Path

from apps.cli.consistency_summary_main import (
    render_consistency_summary,
    run_consistency_summary,
)
from contracts.statistics import StatisticalReport
from runtime.repository.consistency_dkw_summary import (
    ConsistencyDkwSummaryRepository,
)


def test_render_consistency_summary_formats_latest_rows() -> None:
    rendered = render_consistency_summary(
        "uturn",
        {
            "consistent": {
                "status": "success",
                "metric": "min_ttc",
                "sample_count": "12",
                "confidence_level": "0.95",
                "estimate": "0.8",
                "lower_bound": "0.7",
                "upper_bound": "0.9",
                "interval_width": "0.2",
                "target_epsilon": "0.15",
                "next_action": "stop",
            },
            "stochastic": {
                "status": "error",
                "metric": "min_ttc",
                "sample_count": "0",
                "confidence_level": "0.95",
                "estimate": "",
                "lower_bound": "",
                "upper_bound": "",
                "interval_width": "",
                "target_epsilon": "0.15",
                "next_action": "error",
                "message": "empty subset",
            },
        },
    )

    assert "AWSIM_launch verify_consistency DKW summary" in rendered
    assert "--- Consistent Risk ---" in rendered
    assert "width        : 0.2 (target <= 0.15)" in rendered
    assert "--- Stochastic Risk ---" in rendered
    assert "message      : empty subset" in rendered


def test_run_consistency_summary_prints_latest_rows(tmp_path: Path, capsys) -> None:
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

    exit_code = run_consistency_summary(
        [
            "--scenario-name",
            "uturn",
            "--traces-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "scenario     : uturn" in stdout
    assert "--- Consistent Risk ---" in stdout
    assert "--- Stochastic Risk ---" in stdout


def test_run_consistency_summary_returns_one_when_summary_missing(
    tmp_path: Path,
    capsys,
) -> None:
    exit_code = run_consistency_summary(
        [
            "--scenario-name",
            "uturn",
            "--traces-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 1
    stdout = capsys.readouterr().out
    assert "status       : no summary rows found" in stdout
