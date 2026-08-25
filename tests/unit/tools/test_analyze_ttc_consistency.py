import types

import pandas as pd

from tools.analysis import analyze_ttc_consistency


def test_run_analysis_saves_consistency_csvs(monkeypatch, tmp_path):
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    dataset_file = traces_dir / "uturn_dataset.csv"
    pd.DataFrame(
        [
            {"dx0": 1.0, "ego_speed": 2.0, "npc_speed": 3.0, "min_ttc": 1.2},
            {"dx0": 1.0, "ego_speed": 2.0, "npc_speed": 3.0, "min_ttc": 1.1},
        ]
    ).to_csv(dataset_file, index=False)

    cfg = types.SimpleNamespace(
        PARAM_RANGES={"dx0": [], "ego_speed": [], "npc_speed": []},
        DKW_TARGET_METRIC="min_ttc",
    )
    consistent_df = pd.DataFrame([{"dx0": 1.0, "min_ttc": 1.2}])
    stochastic_df = pd.DataFrame([{"dx0": 1.0, "min_ttc": 1.1}])
    saved = {}

    monkeypatch.setattr(
        analyze_ttc_consistency.importlib,
        "import_module",
        lambda name: cfg,
    )
    monkeypatch.setattr(
        analyze_ttc_consistency.point_extractors,
        "filter_by_region_and_bounds",
        lambda df, region: df.copy(),
    )
    monkeypatch.setattr(
        analyze_ttc_consistency.point_extractors,
        "classify_consistency",
        lambda df, param_names, target_metric, threshold, min_repeats: (
            consistent_df,
            stochastic_df,
        ),
    )

    def fake_save_dataframe_to_csv(df, path, message):
        saved[path] = df.copy()
        df.to_csv(path, index=False)

    monkeypatch.setattr(
        analyze_ttc_consistency.point_extractors,
        "save_dataframe_to_csv",
        fake_save_dataframe_to_csv,
    )

    result = analyze_ttc_consistency.run_analysis(
        scenario_type="uturn",
        traces_dir=str(traces_dir),
        region="custom",
        consistency_threshold=0.2,
        min_repeats=3,
    )

    assert result["target_metric"] == "min_ttc"
    assert result["out_path_consistent"] in saved
    assert result["out_path_stochastic"] in saved
    assert pd.read_csv(result["out_path_consistent"]).to_dict("records") == (
        consistent_df.to_dict("records")
    )
    assert pd.read_csv(result["out_path_stochastic"]).to_dict("records") == (
        stochastic_df.to_dict("records")
    )
