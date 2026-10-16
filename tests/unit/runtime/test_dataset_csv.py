from pathlib import Path

from runtime.repository.dataset_csv import DatasetCsvRepository


def test_dataset_csv_repository_appends_and_reads_rows(tmp_path: Path) -> None:
    repository = DatasetCsvRepository(tmp_path / "uturn_dataset.csv")
    repository.append_row({"loop_num": 1, "status": "success"})
    repository.append_row({"loop_num": 2, "status": "timeout"})

    assert repository.read_headers() == ["loop_num", "status"]
    assert repository.read_rows() == [
        {"loop_num": "1", "status": "success"},
        {"loop_num": "2", "status": "timeout"},
    ]


def test_dataset_csv_repository_rewrites_when_new_columns_appear(tmp_path: Path) -> None:
    repository = DatasetCsvRepository(tmp_path / "uturn_dataset.csv")
    repository.append_row({"loop_num": 1, "status": "success"})
    repository.append_row({"loop_num": 2, "status": "timeout", "reason": "timeout"})

    assert repository.read_headers() == ["loop_num", "status", "reason"]
    assert repository.read_rows()[1]["reason"] == "timeout"
    assert repository.max_loop_num() == 2
