from pathlib import Path

from runtime.repository.dataset_csv import DatasetCsvRepository
from runtime.repository.shared_store import SharedStore


def test_shared_store_merges_buffered_input_with_result(tmp_path: Path) -> None:
    repository = DatasetCsvRepository(tmp_path / "uturn_dataset.csv")
    store = SharedStore(repository)

    store.buffer_parameters(5, {"dx0": 15.0, "worker_id": "21"}, reason="boundary_explore")
    merged = store.merge_result({"loop_num": 5, "status": "success", "min_ttc": 1.23})

    assert merged is not None
    assert merged["dx0"] == 15.0
    assert merged["status"] == "success"
    assert merged["reason"] == "boundary_explore"
    assert repository.read_rows()[0]["dx0"] == "15.0000"
    assert repository.read_rows()[0]["min_ttc"] == "1.2300"


def test_shared_store_flush_timeout_uses_explicit_input_when_not_buffered(tmp_path: Path) -> None:
    repository = DatasetCsvRepository(tmp_path / "uturn_dataset.csv")
    store = SharedStore(repository)

    merged = store.flush_timeout(
        loop_num=8,
        timeout_row={"status": "timeout", "min_ttc": -1},
        input_row={"dx0": 25.0},
        reason="timeout_case",
    )

    assert merged["loop_num"] == 8
    assert merged["status"] == "timeout"
    assert merged["dx0"] == 25.0
    assert merged["reason"] == "timeout_case"
    assert repository.read_rows()[0]["status"] == "timeout"
    assert repository.read_rows()[0]["dx0"] == "25.0000"
