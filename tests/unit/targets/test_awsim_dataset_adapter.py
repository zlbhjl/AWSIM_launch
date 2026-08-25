from pathlib import Path

import pandas as pd

from targets.awsim.dataset_adapter import AWSIMDatasetAdapter


def test_load_dataframe_builds_sample_id_from_loop_num(tmp_path: Path) -> None:
    csv_path = tmp_path / "dataset.csv"
    csv_path.write_text("loop_num,status\n1,success\n2,timeout\n", encoding="utf-8")

    dataframe = AWSIMDatasetAdapter().load_dataframe(csv_path)

    assert list(dataframe["sample_id"]) == ["loop_1", "loop_2"]
    assert list(dataframe["status"]) == ["success", "timeout"]


def test_normalize_dataframe_keeps_existing_sample_id() -> None:
    dataframe = pd.DataFrame(
        [
            {"sample_id": "case_a", "loop_num": 3},
            {"sample_id": "case_b", "loop_num": 4},
        ]
    )

    normalized = AWSIMDatasetAdapter().normalize_dataframe(dataframe)

    assert list(normalized["sample_id"]) == ["case_a", "case_b"]


def test_universal_dataset_accepts_dataframe_and_csv(tmp_path: Path) -> None:
    adapter = AWSIMDatasetAdapter()
    dataframe = pd.DataFrame([{"loop_num": 7}, {"loop_num": 8}])
    csv_path = tmp_path / "dataset.csv"
    csv_path.write_text("sample_id\nalpha\nbeta\n", encoding="utf-8")

    assert adapter.universal_dataset(dataframe) == {"loop_7", "loop_8"}
    assert adapter.universal_dataset(csv_path) == {"alpha", "beta"}
