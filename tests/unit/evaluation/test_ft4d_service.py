import json
from pathlib import Path

import pytest

from contracts.verification import VerificationInput
from evaluation.ft4d_service import FT4DService, FT4DServiceConfig, run_ft4d


def _write_tree_config(path: Path) -> Path:
    payload = {
        "tree": {
            "top_event": {
                "id": "TOP",
                "gate": "OR",
                "use_dataset_aggregation": True,
                "children": ["EVENT_A", "EVENT_B"],
            }
        },
        "params": {
            "EVENT_A": {"sigma_pf": 0.1, "sigma_pb": 0.0},
            "EVENT_B": {"sigma_pf": 0.2, "sigma_pb": 0.0},
        },
        "labels": {
            "TOP": "Top event",
            "EVENT_A": "Event A",
            "EVENT_B": "Event B",
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_ft4d_service_runs_with_dataset_rates(tmp_path: Path) -> None:
    tree_path = _write_tree_config(tmp_path / "tree.json")
    verification_input = VerificationInput(
        tree_mode="basic",
        universal_dataset={"s1", "s2", "s3", "s4"},
        events={
            "EVENT_A": {
                "dataset_d": {"s1", "s2"},
                "dataset_e": {"s1"},
                "total_count": 2,
                "error_count": 1,
            },
            "EVENT_B": {
                "dataset_d": {"s3"},
                "dataset_e": {"s3"},
                "total_count": 1,
                "error_count": 1,
            },
        },
        meta={"tree_path": str(tree_path), "source_module": "targets.awsim.verification_input"},
    )

    result = run_ft4d(verification_input)

    assert result.tree_mode == "basic"
    assert result.top_sigma_pe == pytest.approx(0.5)
    assert result.confidence == pytest.approx(1.0)
    assert result.raw_result["tree_path"] == str(tree_path.resolve())
    assert result.raw_result["tree_report"]["tree"]["sigma_pe"] == pytest.approx(0.5)
    assert {node["node_id"] for node in result.node_summaries} == {"TOP", "EVENT_A", "EVENT_B"}


def test_ft4d_service_prefers_assumption_overrides_and_recognition_tests(
    tmp_path: Path,
) -> None:
    tree_path = _write_tree_config(tmp_path / "tree.json")
    service = FT4DService(
        FT4DServiceConfig(
            tree_path=tree_path,
            sigma_pf_source="dataset",
            and_rule="product",
        )
    )
    verification_input = VerificationInput(
        tree_mode="combined",
        universal_dataset={"s1", "s2", "s3", "s4"},
        events={
            "EVENT_A": {
                "dataset_d": {"s1", "s2"},
                "dataset_e": {"s1"},
                "total_count": 2,
                "error_count": 1,
                "recognition_test": {
                    "n": 10,
                    "required_sample_size": 8,
                    "has_required_sample_size": True,
                    "correct_count": 9,
                    "required_correct_count": 8,
                    "meets_correct_count": True,
                    "sample_recognition_rate": 0.9,
                    "required_sample_rate": 0.8,
                    "expected_recognition_rate": 0.7,
                    "epsilon": 0.1,
                    "delta": 0.05,
                    "confidence": 0.95,
                    "passed": True,
                },
            },
            "EVENT_B": {
                "dataset_d": {"s3"},
                "dataset_e": {"s3"},
                "total_count": 1,
                "error_count": 1,
            },
        },
        assumptions={
            "sigma_pf_source": "assumption",
            "sigma_pf_assumptions": {"EVENT_A": 0.4, "EVENT_B": 0.2},
            "and_rule": "min",
        },
        meta={"source_module": "targets.bbsl.verification_input"},
    )

    result = service.run(verification_input)

    assert result.tree_mode == "combined"
    assert result.top_sigma_pe == pytest.approx(0.4)
    assert result.confidence == pytest.approx(0.95)
    assert result.raw_result["sigma_pf_source"] == "assumption"
    assert result.raw_result["and_rule"] == "min"
    event_a = next(node for node in result.node_summaries if node["node_id"] == "EVENT_A")
    assert event_a["confidence"] == pytest.approx(0.95)


def test_ft4d_service_requires_tree_path() -> None:
    service = FT4DService()
    verification_input = VerificationInput(
        tree_mode="basic",
        universal_dataset={"sample_1"},
        events={"EVENT_A": {"dataset_d": {"sample_1"}, "dataset_e": set()}},
    )

    with pytest.raises(ValueError, match="tree_path"):
        service.run(verification_input)
