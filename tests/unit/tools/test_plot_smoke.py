from importlib import import_module

import numpy as np
import pandas as pd

from tools.plot import (
    visualize_collision_regions,
    visualize_traces,
    visualize_version_comparison,
)


PLOT_MODULES = [
    "tools.plot.visualize_collision_regions",
    "tools.plot.visualize_collision_surfaces",
    "tools.plot.visualize_jama_zones",
    "tools.plot.visualize_min_ttc",
    "tools.plot.visualize_min_ttc_3d",
    "tools.plot.visualize_risk_matrix",
    "tools.plot.visualize_traces",
    "tools.plot.visualize_traces_split",
    "tools.plot.visualize_version_comparison",
    "tools.plot.visualize_worker_failure_clusters",
    "tools.plot.visualize_worker_stats",
]


def test_plot_modules_expose_parser_and_main():
    for module_name in PLOT_MODULES:
        module = import_module(module_name)
        assert callable(module.build_parser)
        assert callable(module.main)
        parser = module.build_parser()
        assert parser.prog is not None


def test_visualize_traces_run_plot_resolves_output_path(tmp_path, monkeypatch):
    dataset_dir = tmp_path / "datasets"
    dataset_dir.mkdir()
    pd.DataFrame(
        [
            {
                "dx0": 10.0,
                "npc_speed": 20.0,
                "ego_speed": 30.0,
                "c_collision": 0,
                "c_ttc_0.3": 0,
                "c_ttc_0.5": 0,
                "c_ttc_0.9": 0,
                "c_ttc_1.1": 1,
            }
        ]
    ).to_csv(dataset_dir / "uturn_dataset.csv", index=False)

    captured = {}

    def fake_savefig(path, *args, **kwargs):
        captured["path"] = path

    monkeypatch.setattr(visualize_traces.plt, "savefig", fake_savefig)
    monkeypatch.setattr(visualize_traces, "TheoreticalSafetyCalculator", None)

    result = visualize_traces.run_plot(
        str(dataset_dir),
        "uturn",
        "custom_trace_plot.png",
    )

    assert result["output_image"] == str(dataset_dir / "custom_trace_plot.png")
    assert captured["path"] == str(dataset_dir / "custom_trace_plot.png")


def test_visualize_traces_run_plot_applies_slice_filters(tmp_path, monkeypatch):
    dataset_dir = tmp_path / "datasets"
    dataset_dir.mkdir()
    pd.DataFrame(
        [
            {
                "dx0": 10.0,
                "npc_speed": 20.0,
                "ego_speed": 30.0,
                "cutin_vy": 1.2,
                "c_collision": 0,
                "c_ttc_0.3": 0,
                "c_ttc_0.5": 0,
                "c_ttc_0.9": 0,
                "c_ttc_1.1": 1,
            },
            {
                "dx0": 11.0,
                "npc_speed": 21.0,
                "ego_speed": 31.0,
                "cutin_vy": 1.4,
                "c_collision": 0,
                "c_ttc_0.3": 0,
                "c_ttc_0.5": 0,
                "c_ttc_0.9": 0,
                "c_ttc_1.1": 1,
            },
        ]
    ).to_csv(dataset_dir / "cutin_dataset.csv", index=False)

    monkeypatch.setattr(visualize_traces.plt, "savefig", lambda *args, **kwargs: None)
    monkeypatch.setattr(visualize_traces, "TheoreticalSafetyCalculator", None)

    result = visualize_traces.run_plot(
        str(dataset_dir),
        "cutin",
        "cutin_trace_plot.png",
        slice_filters={"cutin_vy": 1.4},
    )

    assert result["valid_rows"] == 1
    assert result["applied_slices"] == {"cutin_vy": 1.4}


def test_visualize_collision_regions_main_resolves_output_prefix(tmp_path, monkeypatch):
    dataset_dir = tmp_path / "datasets"
    dataset_dir.mkdir()
    pd.DataFrame([{"dummy": 1}]).to_csv(dataset_dir / "uturn_dataset.csv", index=False)

    monkeypatch.setattr(
        visualize_collision_regions.importlib,
        "import_module",
        lambda name: type(
            "Cfg",
            (),
            {
                "PARAM_RANGES": {
                    "dx0": [0.0, 10.0],
                    "npc_speed": [0.0, 10.0],
                    "ego_speed": [0.0, 10.0],
                }
            },
        )(),
    )
    monkeypatch.setattr(
        visualize_collision_regions,
        "load_clean_dataset",
        lambda csv_file: pd.DataFrame(
            [{"dx0": 1.0, "npc_speed": 2.0, "ego_speed": 3.0, "c_collision": 0}]
        ),
    )
    monkeypatch.setattr(
        visualize_collision_regions,
        "build_voxel_masks",
        lambda df, x_edges, y_edges, z_edges: (
            np.zeros((1, 1, 1), dtype=bool),
            np.zeros((1, 1, 1), dtype=bool),
        ),
    )
    monkeypatch.setattr(
        visualize_collision_regions,
        "predict_ai_grid",
        lambda scenario_type, cfg, traces_dir, x_centers, y_centers, z_centers: np.zeros(
            (len(x_centers), len(y_centers), len(z_centers))
        ),
    )

    captured = {"paths": []}

    def fake_plot_empirical(output_path, *args, **kwargs):
        captured["paths"].append(output_path)

    def fake_plot_ai(output_path, *args, **kwargs):
        captured["paths"].append(output_path)

    monkeypatch.setattr(
        visualize_collision_regions,
        "plot_empirical_regions",
        fake_plot_empirical,
    )
    monkeypatch.setattr(
        visualize_collision_regions,
        "plot_ai_regions",
        fake_plot_ai,
    )

    exit_code = visualize_collision_regions.main(
        [str(dataset_dir), "--type", "uturn", "--output-prefix", "cells"]
    )

    assert exit_code == 0
    assert captured["paths"] == [
        str(dataset_dir / "cells_empirical.png"),
        str(dataset_dir / "cells_ai.png"),
    ]


def test_visualize_version_comparison_pairs_replay_source_ids(tmp_path):
    source_csv = tmp_path / "source.csv"
    replay_csv = tmp_path / "replay.csv"
    output = tmp_path / "comparison.png"
    source_rows = []
    replay_rows = []
    transitions = [(0, 0), (0, 1), (1, 0), (1, 1)]
    for loop_num, (source_collision, replay_collision) in enumerate(transitions, start=1):
        common = {
            "dx0": 10.0 + loop_num,
            "npc_speed": 20.0,
            "ego_speed": 30.0,
            "c_ttc_0.5": source_collision,
        }
        source_rows.append(
            {
                "loop_num": loop_num,
                **common,
                "c_collision": source_collision,
                "status": "success",
            }
        )
        replay_rows.append(
            {
                "meta_replay_source_loop_num": loop_num,
                **common,
                "c_collision": replay_collision,
                "c_ttc_0.5": replay_collision,
                "status": "success",
            }
        )
    replay_rows.append(
        {
            "meta_replay_source_loop_num": 5,
            "dx0": 15.0,
            "npc_speed": 20.0,
            "ego_speed": 30.0,
            "c_collision": np.nan,
            "c_ttc_0.5": np.nan,
            "status": "timeout",
        }
    )
    source_rows.append(
        {
            "loop_num": 5,
            "dx0": 15.0,
            "npc_speed": 20.0,
            "ego_speed": 30.0,
            "c_collision": 0,
            "c_ttc_0.5": 0,
            "status": "success",
        }
    )
    pd.DataFrame(source_rows).to_csv(source_csv, index=False)
    pd.DataFrame(replay_rows).to_csv(replay_csv, index=False)

    summary = visualize_version_comparison.run_plot(
        str(source_csv),
        str(replay_csv),
        str(output),
    )

    assert output.exists()
    assert summary["matched_rows"] == 5
    assert summary["complete_rows"] == 4
    assert summary["changed_rows"] == 2
    assert summary["unresolved_rows"] == 1
    assert summary["transition_counts"] == {
        "0_to_0": 1,
        "0_to_1": 1,
        "1_to_0": 1,
        "1_to_1": 1,
        "unresolved": 1,
    }
