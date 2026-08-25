from pathlib import Path

import pytest

from runtime.repository.dataset_restore import DatasetPaths, restore_base_dataset


def test_restore_base_dataset_copies_dataset_to_base_csv(tmp_path: Path) -> None:
    resume_from = tmp_path / "resume"
    traces_dir = tmp_path / "traces"
    resume_from.mkdir()
    (resume_from / "uturn_dataset.csv").write_text("loop_num,status\n1,success\n", encoding="utf-8")

    restored_path = restore_base_dataset(resume_from, "uturn", traces_dir)

    assert restored_path == traces_dir / "uturn_dataset_base.csv"
    assert restored_path.read_text(encoding="utf-8") == "loop_num,status\n1,success\n"


def test_restore_base_dataset_raises_when_source_is_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        restore_base_dataset(tmp_path / "missing", "uturn", tmp_path / "traces")


def test_dataset_paths_build_expected_names(tmp_path: Path) -> None:
    paths = DatasetPaths(traces_dir=tmp_path, scenario_name="uturn")
    assert paths.base_csv == tmp_path / "uturn_dataset_base.csv"
    assert paths.dataset_csv == tmp_path / "uturn_dataset.csv"
