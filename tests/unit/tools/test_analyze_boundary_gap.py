import types

import pandas as pd

from tools.analysis import analyze_boundary_gap


def test_resolve_dataset_path_prefers_fixed_csv(tmp_path):
    dataset_dir = tmp_path / "datasets"
    dataset_dir.mkdir()
    normal = dataset_dir / "uturn_dataset.csv"
    fixed = dataset_dir / "uturn_dataset_fixed.csv"
    normal.write_text("a\n1\n", encoding="utf-8")
    fixed.write_text("a\n2\n", encoding="utf-8")

    resolved = analyze_boundary_gap.resolve_dataset_path(str(dataset_dir), "uturn")

    assert resolved == str(fixed)


def test_run_analysis_writes_boundary_gap_cells_csv(monkeypatch, tmp_path):
    dataset_dir = tmp_path / "datasets"
    dataset_dir.mkdir()
    dataset_file = dataset_dir / "uturn_dataset.csv"
    pd.DataFrame([{"dx0": 1.0, "ego_speed": 2.0, "npc_speed": 3.0}]).to_csv(
        dataset_file, index=False
    )

    cfg = types.SimpleNamespace(
        PARAM_RANGES={"dx0": [1.0], "ego_speed": [2.0], "npc_speed": [3.0]}
    )
    cell_df = pd.DataFrame(
        [
            {
                "dx0": 1.0,
                "ego_speed": 2.0,
                "npc_speed": 3.0,
                "sample_count": 1,
                "collision_ratio": 0.0,
                "near_ratio": 0.0,
                "status": "candidate",
                "is_candidate": True,
            }
        ]
    )

    monkeypatch.setattr(
        analyze_boundary_gap.importlib,
        "import_module",
        lambda name: cfg,
    )
    monkeypatch.setattr(
        analyze_boundary_gap.point_extractors,
        "summarize_boundary_gap_progress",
        lambda df, param_names, cfg_obj: {
            "cell_df": cell_df,
            "total_cells": 1,
            "candidate_cells": 1,
            "densified_cells": 0,
            "clarified_cells": 0,
        },
    )

    result = analyze_boundary_gap.run_analysis(str(dataset_dir), "uturn")

    assert result["output_path"] == str(dataset_dir / "uturn_boundary_gap_cells.csv")
    written = pd.read_csv(result["output_path"])
    assert len(written) == 1
    assert written.loc[0, "status"] == "candidate"


def test_run_analysis_writes_empty_boundary_gap_cells_csv(monkeypatch, tmp_path):
    dataset_dir = tmp_path / "datasets"
    dataset_dir.mkdir()
    dataset_file = dataset_dir / "uturn_dataset.csv"
    pd.DataFrame([{"dx0": 1.0, "ego_speed": 2.0, "npc_speed": 3.0}]).to_csv(
        dataset_file, index=False
    )

    cfg = types.SimpleNamespace(
        PARAM_RANGES={"dx0": [1.0], "ego_speed": [2.0], "npc_speed": [3.0]}
    )
    cell_df = pd.DataFrame(
        columns=[
            "dx0",
            "ego_speed",
            "npc_speed",
            "sample_count",
            "collision_ratio",
            "near_ratio",
            "status",
            "is_candidate",
        ]
    )

    monkeypatch.setattr(
        analyze_boundary_gap.importlib,
        "import_module",
        lambda name: cfg,
    )
    monkeypatch.setattr(
        analyze_boundary_gap.point_extractors,
        "summarize_boundary_gap_progress",
        lambda df, param_names, cfg_obj: {
            "cell_df": cell_df,
            "total_cells": 0,
            "candidate_cells": 0,
            "densified_cells": 0,
            "clarified_cells": 0,
        },
    )

    result = analyze_boundary_gap.run_analysis(str(dataset_dir), "uturn")

    assert result["output_path"] == str(dataset_dir / "uturn_boundary_gap_cells.csv")
    written = pd.read_csv(result["output_path"])
    assert written.empty
    assert list(written.columns) == list(cell_df.columns)
