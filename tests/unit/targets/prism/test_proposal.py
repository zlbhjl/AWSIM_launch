from __future__ import annotations

import pytest

from orchestration.sampling_plan import sampling_plan_from_mapping
from targets.prism.model_catalog import get_model_definition
from targets.prism.proposal import PrismProposalSearchError, build_proposal_candidates


_CONSTANTS = {
    "P_NORMAL_DEGRADE": 0.02,
    "P_NORMAL_FAILURE": 0.001,
    "P_DEGRADED_NORMAL": 0.30,
    "P_DEGRADED_FAILURE": 0.02,
}


def _plan() -> object:
    return sampling_plan_from_mapping(
        {
            "schema_version": 1,
            "experiment_id": "prism_search_v1",
            "target": "prism",
            "target_measure": {
                "id": "original_model_v1",
                "definition": {"model": "simple_reliability_dtmc"},
            },
            "proposal": {
                "id": "initial_q",
                "mode": "manual_proposal",
                "definition": {
                    "transition_probabilities": {
                        "normal": {"normal": 0.96, "degraded": 0.03, "failure": 0.01},
                        "degraded": {"normal": 0.28, "degraded": 0.66, "failure": 0.06},
                        "failure": {"failure": 1.0},
                    }
                },
            },
            "proposal_search": {
                "enabled": True,
                "policy": "suggest",
                "adjustable_transitions": [
                    {
                        "from": "normal",
                        "to": "failure",
                        "compensate_to": "normal",
                        "values": [0.005, 0.02],
                    },
                    {
                        "from": "degraded",
                        "to": "failure",
                        "compensate_to": "degraded",
                        "values": [0.04, 0.08],
                    },
                ],
                "constraints": {"preserve_row_sum": True, "preserve_support": True},
            },
        }
    )


def test_build_proposal_candidates_changes_only_configured_transitions():
    candidates = build_proposal_candidates(
        _plan(),
        get_model_definition("simple_reliability_dtmc"),
        target_constants=_CONSTANTS,
    )

    assert len(candidates) == 4
    first = candidates[0].definition["transition_probabilities"]
    assert first["normal"] == {"normal": 0.965, "degraded": 0.03, "failure": 0.005}
    assert first["degraded"] == {"normal": 0.28, "degraded": 0.68, "failure": 0.04}
    assert first["failure"] == {"failure": 1.0}
    assert candidates[0].proposal_id == "initial_q__candidate_001"
    assert candidates[0].diagnostics["search_policy"] == "suggest"


def test_build_proposal_candidates_rejects_unsupported_transition_outside_p_support():
    plan = _plan()
    plan.proposal_search["adjustable_transitions"] = [
        {
            "from": "failure",
            "to": "normal",
            "compensate_to": "failure",
            "values": [0.01],
        }
    ]

    with pytest.raises(PrismProposalSearchError, match="outside P support"):
        build_proposal_candidates(
            plan,
            get_model_definition("simple_reliability_dtmc"),
            target_constants=_CONSTANTS,
        )
