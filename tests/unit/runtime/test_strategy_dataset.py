from pathlib import Path

from runtime.repository.strategy_dataset import StrategyDatasetRepository


def test_strategy_dataset_repository_loads_current_dataset_only(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    (traces_dir / "uturn_dataset.csv").write_text(
        "loop_num,status\n2,success\n",
        encoding="utf-8",
    )

    repository = StrategyDatasetRepository("uturn", traces_dir=traces_dir)
    loaded = repository.load_dataset()

    assert loaded is not None
    assert loaded.to_dict(orient="records") == [{"loop_num": 2, "status": "success"}]


def test_strategy_dataset_repository_merges_base_and_current_dataset(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    (traces_dir / "uturn_dataset_base.csv").write_text(
        "loop_num,status\n1,success\n",
        encoding="utf-8",
    )
    (traces_dir / "uturn_dataset.csv").write_text(
        "loop_num,status\n2,timeout\n",
        encoding="utf-8",
    )

    repository = StrategyDatasetRepository("uturn", traces_dir=traces_dir)
    loaded = repository.load_dataset()

    assert loaded is not None
    assert loaded["loop_num"].tolist() == [1, 2]
    assert loaded["status"].tolist() == ["success", "timeout"]


def test_strategy_dataset_repository_returns_none_when_no_dataset_exists(tmp_path: Path) -> None:
    repository = StrategyDatasetRepository("uturn", traces_dir=tmp_path / "traces")

    assert repository.load_dataset() is None


def test_strategy_dataset_repository_uses_explicit_csv_paths(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    current_csv = tmp_path / "custom_current.csv"
    base_csv = tmp_path / "custom_base.csv"
    base_csv.write_text(
        "loop_num,status\n3,success\n",
        encoding="utf-8",
    )
    current_csv.write_text(
        "loop_num,status\n4,timeout\n",
        encoding="utf-8",
    )

    repository = StrategyDatasetRepository(
        "uturn",
        traces_dir=traces_dir,
        dataset_csv=current_csv,
        base_csv=base_csv,
    )
    loaded = repository.load_dataset()

    assert loaded is not None
    assert loaded["loop_num"].tolist() == [3, 4]
