from __future__ import annotations

import pytest

from orchestration.sampling_plan import (
    SamplingPlanValidationError,
    load_sampling_plan,
    sampling_plan_from_mapping,
)


def _plan() -> dict[str, object]:
    return {
        "schema_version": 1,
        "experiment_id": "awsim_uturn_is_v1",
        "target": "awsim",
        "target_measure": {
            "id": "normal_input_v1",
            "definition": {
                "dx0": {"distribution": "uniform", "lower": 5.0, "upper": 20.0}
            },
        },
        "proposal": {
            "id": "near_collision_v1",
            "mode": "manual_proposal",
            "definition": {
                "dx0": {
                    "distribution": "truncated_normal",
                    "mean": 7.0,
                    "stddev": 1.5,
                }
            },
        },
        "adaptive_update": {"enabled": False},
    }


def test_load_sampling_plan_validates_common_fields_and_preserves_definitions(tmp_path):
    plan_path = tmp_path / "sampling_plan.yaml"
    plan_path.write_text(
        """\
schema_version: 1
experiment_id: prism_collision_v1
target: prism
target_measure:
  id: original_model_v1
  definition:
    model: models/collision.pm
    property: 'P=? [ F collision ]'
proposal:
  id: boundary_focused_v1
  mode: manual_proposal
  definition:
    type: transition_bias
    transitions:
      unsafe_branch: 0.7
""",
        encoding="utf-8",
    )

    plan = load_sampling_plan(plan_path)

    assert plan.experiment_id == "prism_collision_v1"
    assert plan.target == "prism"
    assert plan.target_measure_definition == {
        "model": "models/collision.pm",
        "property": "P=? [ F collision ]",
    }
    assert plan.proposal_definition == {
        "type": "transition_bias",
        "transitions": {"unsafe_branch": 0.7},
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda plan: plan.pop("experiment_id"), "sampling plan.experiment_id is required"),
        (lambda plan: plan.__setitem__("schema_version", 99), "Unsupported sampling-plan"),
        (lambda plan: plan["proposal"].__setitem__("mode", "unknown"), "proposal.mode must be one of"),
        (lambda plan: plan["target_measure"].pop("definition"), "target_measure.definition is required"),
    ],
)
def test_sampling_plan_rejects_invalid_common_envelope(mutate, message):
    raw = _plan()
    mutate(raw)

    with pytest.raises(SamplingPlanValidationError, match=message):
        sampling_plan_from_mapping(raw)


def test_sampling_plan_does_not_validate_target_specific_definition_content():
    raw = _plan()
    raw["target_measure"]["definition"] = ["target-specific", {"shape": "opaque"}]
    raw["proposal"]["definition"] = "target-specific syntax"

    plan = sampling_plan_from_mapping(raw)

    assert plan.target_measure_definition == ["target-specific", {"shape": "opaque"}]
    assert plan.proposal_definition == "target-specific syntax"


def test_sampling_plan_preserves_proposal_search_without_interpreting_it():
    raw = _plan()
    raw["proposal_search"] = {
        "enabled": True,
        "adjustable_transitions": [{"arbitrary": ["target-specific", "content"]}],
    }
    raw["proposal_update"] = {"approval_required": True}

    plan = sampling_plan_from_mapping(raw)

    assert plan.proposal_search == raw["proposal_search"]
    assert plan.proposal_update == raw["proposal_update"]
