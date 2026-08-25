from pathlib import Path

from adapters.bbsl.event_builder import BBSLEventSetBuilder as LegacyBBSLEventSetBuilder
from targets.bbsl.dataset_adapter import BBSLExperimentAdapter
from targets.bbsl.event_builder import BBSLEventSetBuilder


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "bbsl"


def test_event_builder_matches_legacy_output_for_batch_fixture() -> None:
    adapter = BBSLExperimentAdapter()
    clean_output = adapter.load_output(FIXTURES / "clean_baseline.json")
    batch_output = adapter.load_output(FIXTURES / "noisy_batch_0001.json")
    batch_output["source_output_json"] = str(FIXTURES / "noisy_batch_0001.json")

    current = BBSLEventSetBuilder(adapter)
    legacy = LegacyBBSLEventSetBuilder()

    current_result = current.build_event_inputs_from_batch_outputs(
        clean_output,
        [batch_output],
        sigma_pf_source="dataset",
        sigma_pb_mode="raw",
        and_rule="min",
    )
    legacy_result = legacy.build_event_inputs_from_batch_outputs(
        clean_output,
        [batch_output],
        sigma_pf_source="dataset",
        sigma_pb_mode="raw",
        and_rule="min",
    )

    assert current_result["active_conditions"] == legacy_result["active_conditions"]
    assert current_result["batch_count"] == legacy_result["batch_count"]
    assert current_result["batch_output_paths"] == legacy_result["batch_output_paths"]
    assert current_result["events"] == legacy_result["events"]
