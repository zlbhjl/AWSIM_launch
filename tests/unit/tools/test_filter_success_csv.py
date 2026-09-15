import csv
from pathlib import Path

import pytest

from tools.maintenance.filter_success_csv import filter_success_csv, main


def _write_dataset(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["loop_num", "status", "note"])
        writer.writerow([1, "success", "kept"])
        writer.writerow([2, "timeout", "excluded"])
        writer.writerow([3, "execution_error", "excluded"])
        writer.writerow([4, " SUCCESS ", "kept with embedded\nnewline"])


def test_filter_success_csv_keeps_only_success_rows(tmp_path: Path) -> None:
    source = tmp_path / "dataset.csv"
    output = tmp_path / "dataset_success.csv"
    _write_dataset(source)

    summary = filter_success_csv(source, output)

    with output.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["loop_num"] for row in rows] == ["1", "4"]
    assert rows[1]["note"] == "kept with embedded\nnewline"
    assert summary.total_rows == 4
    assert summary.success_rows == 2
    assert summary.excluded_rows == 2


def test_filter_success_csv_refuses_to_overwrite_output(tmp_path: Path) -> None:
    source = tmp_path / "dataset.csv"
    output = tmp_path / "dataset_success.csv"
    _write_dataset(source)
    output.write_text("existing\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="--overwrite"):
        filter_success_csv(source, output)

    assert output.read_text(encoding="utf-8") == "existing\n"


def test_filter_success_csv_refuses_input_as_output(tmp_path: Path) -> None:
    source = tmp_path / "dataset.csv"
    _write_dataset(source)

    with pytest.raises(ValueError, match="different"):
        filter_success_csv(source, source, overwrite=True)


def test_main_uses_default_success_filename(tmp_path: Path) -> None:
    source = tmp_path / "dataset.csv"
    _write_dataset(source)

    assert main(["--dataset-csv", str(source)]) == 0
    assert (tmp_path / "dataset_success.csv").is_file()
