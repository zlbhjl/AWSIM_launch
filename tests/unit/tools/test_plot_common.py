from tools.plot.common import resolve_dataset_path, resolve_output_path


def test_resolve_dataset_path_prefers_fixed_csv(tmp_path):
    dataset_dir = tmp_path / "datasets"
    dataset_dir.mkdir()
    normal = dataset_dir / "uturn_dataset.csv"
    fixed = dataset_dir / "uturn_dataset_fixed.csv"
    normal.write_text("a\n1\n", encoding="utf-8")
    fixed.write_text("a\n2\n", encoding="utf-8")

    csv_file, target_dir = resolve_dataset_path(str(dataset_dir), "uturn")

    assert csv_file == str(fixed)
    assert target_dir == str(dataset_dir)


def test_resolve_output_path_keeps_absolute_and_expands_relative(tmp_path):
    target_dir = str(tmp_path)
    absolute = resolve_output_path(target_dir, str(tmp_path / "a.png"))
    relative = resolve_output_path(target_dir, "nested/out.png")

    assert absolute == str(tmp_path / "a.png")
    assert relative == str(tmp_path / "nested/out.png")
