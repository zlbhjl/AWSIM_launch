from pathlib import Path

from runtime.repository.boundary_gap_progress import BoundaryGapProgressRepository


def test_boundary_gap_progress_repository_uses_legacy_default_path(tmp_path: Path) -> None:
    repository = BoundaryGapProgressRepository(
        scenario_name="uturn",
        traces_dir=tmp_path,
    )

    assert repository.progress_path == tmp_path / "uturn_boundary_gap_progress.csv"


def test_boundary_gap_progress_repository_appends_snapshot_rows(tmp_path: Path) -> None:
    repository = BoundaryGapProgressRepository(
        scenario_name="uturn",
        traces_dir=tmp_path,
    )

    repository.append_snapshot(
        cycle=2,
        snapshot_kind="cycle_complete",
        summary={
            "total_cells": 10,
            "candidate_cells": 3,
            "needs_more_data_cells": 2,
            "densified_cells": 1,
            "clarified_cells": 4,
            "target_cells": [
                {
                    "dx0": 12.5,
                    "ego_speed": 35.0,
                    "status": "needs_more_data",
                    "sample_count": 4,
                    "collision_ratio": 0.25,
                    "near_ratio": 0.50,
                    "is_candidate": True,
                },
                {
                    "dx0": 15.0,
                    "ego_speed": 36.0,
                    "status": "clarified",
                    "sample_count": 8,
                    "collision_ratio": 0.0,
                    "near_ratio": 0.25,
                    "is_candidate": False,
                },
            ],
        },
    )

    rows = repository.read_rows()

    assert len(rows) == 2
    assert rows[0]["cycle"] == "2"
    assert rows[0]["snapshot_kind"] == "cycle_complete"
    assert rows[0]["point_index"] == "1"
    assert rows[0]["point_count"] == "2"
    assert rows[0]["candidate_cells"] == "3"
    assert rows[0]["dx0"] == "12.5"
    assert rows[1]["status"] == "clarified"


def test_boundary_gap_progress_repository_writes_summary_only_row_when_targets_missing(
    tmp_path: Path,
) -> None:
    repository = BoundaryGapProgressRepository(
        scenario_name="uturn",
        traces_dir=tmp_path,
    )

    repository.append_snapshot(
        cycle=3,
        snapshot_kind="final",
        summary={
            "total_cells": 20,
            "candidate_cells": 0,
            "needs_more_data_cells": 0,
            "densified_cells": 5,
            "clarified_cells": 15,
            "target_cells": [],
        },
    )

    rows = repository.read_rows()

    assert len(rows) == 1
    assert rows[0]["cycle"] == "3"
    assert rows[0]["snapshot_kind"] == "final"
    assert rows[0]["candidate_cells"] == "0"
