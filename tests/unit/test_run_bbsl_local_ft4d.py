from types import SimpleNamespace

from pathlib import Path

from run_bbsl_local_ft4d import (
    _build_execution_profile_from_args,
    _evaluate_bbsl_batch_outputs_via_new_pipeline,
    _evaluate_bbsl_output_via_new_pipeline,
    _rebuild_batch_loop_result_via_new_pipeline,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "bbsl"


def test_build_execution_profile_from_args_normalizes_cli_values(tmp_path: Path) -> None:
    profile = _build_execution_profile_from_args(
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


def test_evaluate_bbsl_output_via_new_pipeline_legacy_fixture() -> None:
    result = _evaluate_bbsl_output_via_new_pipeline(
        str(FIXTURES / "experiment_all_raw_result_mini.json"),
        tree_mode="basic",
        sigma_pf_source="dataset",
        and_rule="min",
    )

    assert result["source_output_json"].endswith("experiment_all_raw_result_mini.json")
    assert result["tree_mode"] == "basic"
    assert result["active_conditions"] == ["clean", "salt_pepper", "occlusion", "blur"]
    assert result["sigma_pb_mode"] == "delta-clean"
    assert result["top_sigma_pe"] is not None
    assert "SALT_PEPPER" in result["event_inputs"]
    assert "rendered_tree" in result


def test_evaluate_bbsl_batch_outputs_via_new_pipeline_batch_fixture() -> None:
    result = _evaluate_bbsl_batch_outputs_via_new_pipeline(
        str(FIXTURES / "clean_baseline.json"),
        [str(FIXTURES / "noisy_batch_0001.json")],
        tree_mode="basic",
        sigma_pf_source="dataset",
        sigma_pb_mode="raw",
        and_rule="min",
    )

    assert result["clean_baseline_json"].endswith("clean_baseline.json")
    assert result["source_batch_jsons"][0].endswith("noisy_batch_0001.json")
    assert result["tree_mode"] == "basic"
    assert result["batch_count"] == 1
    assert result["top_sigma_pe"] is not None
    assert result["event_inputs"]["SALT_PEPPER"]["total_count"] > 0


def test_rebuild_batch_loop_result_via_new_pipeline_reuses_batch_metadata() -> None:
    rebuilt = _rebuild_batch_loop_result_via_new_pipeline(
        {
            "batch_loop": {
                "clean_baseline_json": str(FIXTURES / "clean_baseline.json"),
                "source_batch_jsons": [str(FIXTURES / "noisy_batch_0001.json")],
                "condition_policy": "underconfident",
            }
        },
        tree_mode="basic",
        sigma_pf_source="dataset",
        sigma_pb_mode="raw",
        and_rule="min",
    )

    assert rebuilt["batch_loop"]["condition_policy"] == "underconfident"
    assert rebuilt["source_batch_jsons"][0].endswith("noisy_batch_0001.json")
    assert rebuilt["top_sigma_pe"] is not None
