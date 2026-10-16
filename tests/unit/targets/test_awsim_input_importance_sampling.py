from __future__ import annotations

import math

import pytest

from contracts.sampling import normalize_sampling_metadata
from orchestration.sampling_plan import sampling_plan_from_mapping
from targets.awsim.sampling import AWSIMInputSampler


def _plan(*, proposal: object | None = None):
    return sampling_plan_from_mapping(
        {
            "schema_version": 1,
            "experiment_id": "awsim_histogram_p_v1",
            "target": "awsim",
            "target_measure": {
                "id": "synthetic_speed_p_v1",
                "definition": {
                    "case_kind": "uturn",
                    "fixed_inputs": {"dx0": 15.0},
                    "parameters": {
                        "ego_speed": {
                            "distribution": "histogram",
                            "bins": [
                                {"min": 30.0, "max": 35.0, "weight": 0.5},
                                {"min": 35.0, "max": 40.0, "weight": 0.5},
                            ],
                        },
                        "npc_speed": {
                            "distribution": "histogram",
                            "bins": [
                                {"min": 10.0, "max": 15.0, "weight": 0.6},
                                {"min": 15.0, "max": 20.0, "weight": 0.4},
                            ],
                        },
                    },
                },
            },
            "proposal": {
                "id": "q1",
                "mode": "manual_proposal",
                "definition": proposal
                or {
                    "parameters": {
                        "ego_speed": {"distribution": "target"},
                        "npc_speed": {"distribution": "target"},
                    }
                },
            },
        }
    )


def test_awsim_q_equals_p_is_reproducible_and_has_unit_weight() -> None:
    sampler = AWSIMInputSampler(_plan())
    ordinary = sampler.sample_target(seed=17)
    importance = sampler.sample_proposal(seed=17)

    assert ordinary.values == importance.values
    assert importance.sampling_metadata["log_importance_weight"] == 0.0
    assert importance.sampling_metadata["importance_weight_upper_bound"] == 1.0
    assert normalize_sampling_metadata(importance.sampling_metadata)["proposal_id"] == "q1"


def test_awsim_focused_q_records_exact_density_ratio_and_stays_in_support() -> None:
    sampler = AWSIMInputSampler(
        _plan(
            proposal={
                "parameters": {
                    "ego_speed": {
                        "distribution": "mixture_target_focus",
                        "base_weight": 0.8,
                        "focus": {"distribution": "histogram", "bins": [{"min": 35, "max": 40, "weight": 1.0}]},
                    },
                    "npc_speed": {"distribution": "target"},
                }
            }
        )
    )
    first = sampler.sample_proposal(seed=21)
    second = sampler.sample_proposal(seed=21)

    assert first.values == second.values
    assert 30.0 <= first.values["ego_speed"] <= 40.0
    assert 10.0 <= first.values["npc_speed"] <= 20.0
    assert math.isclose(
        float(first.sampling_metadata["log_importance_weight"]),
        float(first.sampling_metadata["log_p"]) - float(first.sampling_metadata["log_q"]),
    )


def test_awsim_q_rejects_focus_outside_p_support() -> None:
    with pytest.raises(ValueError, match="inside P support"):
        AWSIMInputSampler(
            _plan(
                proposal={
                    "parameters": {
                        "ego_speed": {
                            "distribution": "mixture_target_focus",
                            "base_weight": 0.8,
                            "focus": {"distribution": "histogram", "bins": [{"min": 29, "max": 35, "weight": 1.0}]},
                        },
                        "npc_speed": {"distribution": "target"},
                    }
                }
            )
        )
