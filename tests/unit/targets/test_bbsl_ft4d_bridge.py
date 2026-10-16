from pathlib import Path

from targets.bbsl.ft4d_bridge import (
    evaluate_ft4d_from_bbsl_batch_outputs,
    evaluate_ft4d_from_bbsl_output,
)


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "bbsl"


def test_evaluate_ft4d_from_bbsl_output_runs_new_bridge() -> None:
    result = evaluate_ft4d_from_bbsl_output(
        FIXTURES / "experiment_all_raw_result_mini.json",
        tree_mode="basic",
        sigma_pf_source="dataset",
        and_rule="min",
    )

    assert result["source_output_json"].endswith("experiment_all_raw_result_mini.json")
    assert result["tree_mode"] == "basic"
    assert result["sigma_pb_mode"] == "delta-clean"
    assert result["top_sigma_pe"] is not None
    assert "SALT_PEPPER" in result["event_inputs"]
    assert "rendered_tree" in result


def test_evaluate_ft4d_from_bbsl_batch_outputs_runs_new_bridge() -> None:
    result = evaluate_ft4d_from_bbsl_batch_outputs(
        FIXTURES / "clean_baseline.json",
        [FIXTURES / "noisy_batch_0001.json"],
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
