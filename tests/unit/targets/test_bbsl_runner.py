from pathlib import Path

from targets.bbsl.runner import cleanup_bbsl_batch_state, run_bbsl_experiment


def test_run_bbsl_experiment_builds_expected_command(tmp_path: Path, monkeypatch) -> None:
    target_repo = tmp_path / "bbsl_repo"
    script_path = target_repo / "examples" / "run_full_experiment_all.py"
    output_json = target_repo / "output" / "experiment_all_raw_result_mini.json"
    script_path.parent.mkdir(parents=True)
    output_json.parent.mkdir(parents=True)
    script_path.write_text("print('stub')\n", encoding="utf-8")
    output_json.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0

    def fake_run(command, cwd, check):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["check"] = check
        return Completed()

    monkeypatch.setattr("targets.bbsl.runner.subprocess.run", fake_run)

    resolved_output = run_bbsl_experiment(
        str(target_repo),
        mini=True,
        max_images=8,
        tree="basic",
        sigma_pf_source="dataset",
        sigma_pb_mode="delta-clean",
        and_rule="min",
        detect_timeout=15,
    )

    assert resolved_output == str(output_json.resolve())
    assert captured["cwd"] == str(target_repo.resolve())
    assert "--mini" in captured["command"]
    assert "--max-images" in captured["command"]
    assert "--detect-timeout" in captured["command"]


def test_cleanup_bbsl_batch_state_removes_generated_files(tmp_path: Path) -> None:
    target_repo = tmp_path / "bbsl_repo"
    batch_dir = target_repo / "output" / "batches"
    output_dir = target_repo / "output"
    generated_dir = target_repo / "data" / "kitti" / "ft4d_batches" / "batch_0001"
    batch_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True)

    batch_json = batch_dir / "noisy_batch_0001.json"
    detect_png = output_dir / "detect_0001.png"
    generated_file = generated_dir / "stub.txt"
    batch_json.write_text("{}", encoding="utf-8")
    detect_png.write_text("png", encoding="utf-8")
    generated_file.write_text("generated", encoding="utf-8")

    summary = cleanup_bbsl_batch_state(str(target_repo))

    assert str(batch_json) in summary["removed_json_files"]
    assert str(detect_png) in summary["removed_detect_pngs"]
    assert str(generated_dir) in summary["removed_generated_dirs"]
    assert not batch_json.exists()
    assert not detect_png.exists()
    assert not generated_dir.exists()
