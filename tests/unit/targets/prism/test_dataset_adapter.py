import pandas as pd

from targets.prism.dataset_adapter import PrismDatasetAdapter


def test_dataset_adapter_keeps_exact_and_sample_rows_separate() -> None:
    adapter = PrismDatasetAdapter()
    dataframe = pd.DataFrame(
        [
            {"record_kind": "exact_model_check", "c_failure": None},
            {"record_kind": "sample", "c_failure": 1},
            {"record_kind": "sample", "c_failure": 0},
        ]
    )

    assert len(adapter.exact_model_check_rows(dataframe)) == 1
    assert len(adapter.sample_rows(dataframe)) == 2


def test_dataset_adapter_treats_legacy_rows_as_samples() -> None:
    rows = PrismDatasetAdapter().sample_rows(pd.DataFrame([{"c_failure": 1}]))

    assert rows.iloc[0]["record_kind"] == "sample"
