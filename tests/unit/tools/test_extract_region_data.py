import pandas as pd

from tools.analysis import extract_region_data


def test_run_extraction_filters_and_saves(monkeypatch, tmp_path):
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    dataset_file = traces_dir / "uturn_dataset.csv"
    pd.DataFrame(
        [
            {"dx0": 1.0, "c_collision": 1, "min_ttc": 0.8},
            {"dx0": 2.0, "c_collision": 0, "min_ttc": 2.0},
        ]
    ).to_csv(dataset_file, index=False)

    filtered_df = pd.DataFrame([{"dx0": 1.0, "c_collision": 1, "min_ttc": 0.8}])
    saved = {}

    monkeypatch.setattr(
        extract_region_data.point_extractors,
        "filter_by_region_and_bounds",
        lambda df, region, bounds: filtered_df.copy(),
    )

    def fake_save_dataframe_to_csv(df, path, message):
        saved[path] = df.copy()
        df.to_csv(path, index=False)

    monkeypatch.setattr(
        extract_region_data.point_extractors,
        "save_dataframe_to_csv",
        fake_save_dataframe_to_csv,
    )

    result = extract_region_data.run_extraction(
        scenario_type="uturn",
        traces_dir=str(traces_dir),
        region="custom",
        bounds_json='{"dx0": [1.0, 2.0]}',
        output_name="filtered.csv",
    )

    assert len(result["filtered_df"]) == 1
    assert result["out_path"] in saved
    assert pd.read_csv(result["out_path"]).to_dict("records") == filtered_df.to_dict(
        "records"
    )
