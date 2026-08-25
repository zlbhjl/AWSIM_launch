import json
from pathlib import Path


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def load_json(*parts: str) -> dict:
    return json.loads((FIXTURES / Path(*parts)).read_text(encoding="utf-8"))


def test_awsim_maude_failure_fixture_is_missing_vehicle_sizes() -> None:
    payload = load_json("awsim", "maude_failure_missing_vehicle_sizes.json")
    assert "groundtruth_size" in payload
    assert "vehicle_sizes" not in payload["groundtruth_size"]


def test_awsim_empty_trace_fixture_keeps_vehicle_sizes_but_has_no_timesteps() -> None:
    payload = load_json("awsim", "empty_trace_with_vehicle_sizes.json")
    assert "vehicle_sizes" in payload["groundtruth_size"]
    assert payload["groundtruth_kinematic"] == []
    assert payload["perception_objects"] == []


def test_bbsl_invalid_fixture_is_missing_bbsl_results() -> None:
    payload = load_json("bbsl", "invalid_missing_bbsl_results.json")
    assert "bbsl_results" not in payload
    assert "condition_datasets" in payload


def test_bbsl_empty_fixture_has_required_keys_with_empty_payloads() -> None:
    payload = load_json("bbsl", "empty_experiment_all_raw_result.json")
    assert payload["universal_dataset_size"] == 0
    assert payload["universal_dataset"] == []
    assert set(payload["bbsl_results"].keys()) == {
        "clean",
        "salt_pepper",
        "occlusion",
        "blur",
    }
