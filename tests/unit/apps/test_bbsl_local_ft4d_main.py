from pathlib import Path
from types import SimpleNamespace

import json

import apps.cli.bbsl_local_ft4d_main as bbsl_local_ft4d_main


def test_build_execution_profile_from_args_normalizes_cli_values(tmp_path: Path) -> None:
    profile = bbsl_local_ft4d_main._build_execution_profile_from_args(
        SimpleNamespace(
            target_repo=str(tmp_path / "bbsl_repo"),
            mini=True,
            max_images=8,
            tree="all",
            sigma_pb_mode="raw",
            and_rule="product",
            detect_timeout=15,
            reuse_existing_output=True,
        ),
        sigma_pf_source="assumption",
    )

    assert profile.target_repo == str((tmp_path / "bbsl_repo").resolve())
    assert profile.mini is True
    assert profile.max_images == 8
    assert profile.tree_mode == "all"
    assert profile.sigma_pf_source == "assumption"
    assert profile.sigma_pb_mode == "raw"
    assert profile.and_rule == "product"
    assert profile.detect_timeout == 15
    assert profile.reuse_existing_output is True


def test_main_writes_result_json_for_legacy_reuse_mode(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    fixture_path = tmp_path / "experiment_all_raw_result_mini.json"
    fixture_path.write_text("{}", encoding="utf-8")
    output_path = tmp_path / "result.json"

    class FakeAdapter:
        def default_output_path(self, target_repo, mini, prefer_raw=True):
            assert target_repo == str((tmp_path / "bbsl_repo").resolve())
            assert mini is True
            assert prefer_raw is True
            return str(fixture_path)

    monkeypatch.setattr(bbsl_local_ft4d_main, "BBSLExperimentAdapter", FakeAdapter)
    monkeypatch.setattr(
        bbsl_local_ft4d_main,
        "_evaluate_bbsl_output_via_new_pipeline",
        lambda *args, **kwargs: {
            "source_output_json": str(fixture_path),
            "tree_mode": "basic",
            "active_conditions": ["clean", "salt_pepper"],
            "sigma_pf_source": "dataset",
            "sigma_pb_mode": "delta-clean",
            "and_rule": "min",
            "top_sigma_pe": 0.0,
            "rendered_tree": "TOP",
        },
    )

    exit_code = bbsl_local_ft4d_main.main(
        [
            "--target-repo",
            str(tmp_path / "bbsl_repo"),
            "--execution-mode",
            "legacy",
            "--reuse-existing-output",
            "--mini",
            "--output-json",
            str(output_path),
        ]
    )

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["tree_mode"] == "basic"
    assert payload["source_output_json"] == str(fixture_path)

    stdout = capsys.readouterr().out
    assert "AWSIM_launch local FT4D over BBSL output" in stdout
    assert str(output_path) in stdout


def test_main_uses_targets_batch_loop_for_normal_batch_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output_path = tmp_path / "result.json"
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        bbsl_local_ft4d_main,
        "run_bbsl_until_ft4d_confident",
        lambda **kwargs: (
            captured.setdefault("kwargs", kwargs),
            {
                "batch_loop": {
                    "clean_baseline_json": "/tmp/clean.json",
                    "source_batch_jsons": ["/tmp/noisy_batch_0001.json"],
                    "completed_batches": 1,
                    "stop_reason": "confidence-satisfied",
                }
            },
        )[1],
    )
    monkeypatch.setattr(
        bbsl_local_ft4d_main,
        "_rebuild_batch_loop_result_via_new_pipeline",
        lambda legacy_result, **kwargs: {
            "clean_baseline_json": "/tmp/clean.json",
            "source_batch_jsons": ["/tmp/noisy_batch_0001.json"],
            "tree_mode": "basic",
            "active_conditions": ["salt_pepper", "occlusion", "blur"],
            "sigma_pf_source": "dataset",
            "sigma_pb_mode": "delta-clean",
            "and_rule": "min",
            "top_sigma_pe": 0.0,
            "rendered_tree": "TOP",
            "batch_loop": legacy_result["batch_loop"],
        },
    )

    exit_code = bbsl_local_ft4d_main.main(
        [
            "--target-repo",
            str(tmp_path / "bbsl_repo"),
            "--execution-mode",
            "batch-loop",
            "--run-mode",
            "resume",
            "--output-json",
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert captured["kwargs"]["target_repo"] == str((tmp_path / "bbsl_repo").resolve())
    assert captured["kwargs"]["resume_batches"] is True
    assert captured["kwargs"]["reuse_clean_baseline"] is True
