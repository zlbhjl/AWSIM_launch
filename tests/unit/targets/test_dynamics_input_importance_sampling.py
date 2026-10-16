from __future__ import annotations

import math

import pytest

from orchestration.sampling_plan import sampling_plan_from_mapping
from targets.dynamics.sampling import DynamicsInputSampler


def _plan() -> object:
    return sampling_plan_from_mapping(
        {
            "schema_version": 1,
            "experiment_id": "dynamics_is_v1",
            "target": "dynamics",
            "target_measure": {
                "id": "uturn_uniform_p_v1",
                "definition": {
                    "model_id": "uturn",
                    "parameters": {
                        "dx0": {"distribution": "uniform", "min": 10.0, "max": 25.0},
                        "ego_speed": {"distribution": "uniform", "min": 30.0, "max": 40.0},
                        "npc_speed": {"distribution": "uniform", "min": 10.0, "max": 25.0},
                    },
                },
            },
            "proposal": {
                "id": "dx0_focus_q1",
                "mode": "manual_proposal",
                "definition": {
                    "parameters": {
                        "dx0": {
                            "distribution": "mixture_uniform_focus",
                            "base_weight": 0.7,
                            "focus": {"min": 10.0, "max": 14.0},
                        },
                        "ego_speed": {"distribution": "uniform", "min": 30.0, "max": 40.0},
                        "npc_speed": {"distribution": "uniform", "min": 10.0, "max": 25.0},
                    },
                },
            },
        }
    )


def test_dynamics_q_sampler_is_seeded_and_records_exact_input_density_ratio() -> None:
    sampler = DynamicsInputSampler(_plan())
    first = sampler.sample_proposal(seed=17)
    second = sampler.sample_proposal(seed=17)

    assert first.values == second.values
    assert first.sampling_metadata["importance_weight_status"] == "available"
    assert math.isclose(
        float(first.sampling_metadata["log_importance_weight"]),
        float(first.sampling_metadata["log_p"]) - float(first.sampling_metadata["log_q"]),
    )
    assert float(first.sampling_metadata["importance_weight_upper_bound"]) == 1.0 / 0.7
    assert 10.0 <= first.values["dx0"] <= 25.0


def test_dynamics_q_rejects_a_focus_outside_p_support() -> None:
    raw = _plan()
    raw.target_measure_definition["parameters"]["dx0"] = {
        "distribution": "uniform", "min": 11.0, "max": 25.0,
    }
    definition = raw.proposal_definition  # type: ignore[attr-defined]
    definition["parameters"]["dx0"]["focus"] = {"min": 10.0, "max": 14.0}
    with pytest.raises(ValueError, match="inside P support"):
        DynamicsInputSampler(raw)


def test_dynamics_q_equals_p_has_identical_inputs_and_unit_weights() -> None:
    raw = _plan()
    parameters = raw.target_measure_definition["parameters"]
    raw.proposal_definition["parameters"] = {
        name: dict(spec) for name, spec in parameters.items()
    }
    sampler = DynamicsInputSampler(raw)

    ordinary = sampler.sample_target(seed=31)
    importance = sampler.sample_proposal(seed=31)

    assert ordinary.values == importance.values
    assert float(importance.sampling_metadata["log_p"]) == float(importance.sampling_metadata["log_q"])
    assert float(importance.sampling_metadata["log_importance_weight"]) == 0.0
    assert float(importance.sampling_metadata["importance_weight_upper_bound"]) == 1.0


def test_dynamics_sde_noise_proposal_contributes_to_the_recorded_weight_bound() -> None:
    raw = _plan()
    raw.target_measure_definition["execution"] = {"solver_kind": "sde"}
    raw.proposal_definition["noise_path"] = {
        "distribution": "defensive_gaussian_drift",
        "base_weight": 0.8,
        "channel_drifts": {"npc_acceleration": -0.4},
    }

    sampler = DynamicsInputSampler(raw)

    assert sampler.sde_noise_proposal == {
        "distribution": "defensive_gaussian_drift",
        "base_weight": 0.8,
        "channel_drifts": {"npc_acceleration": -0.4},
        "drift_schedule": [],
    }
    assert sampler.importance_weight_upper_bound == pytest.approx(1.0 / (0.7 * 0.8))


def test_dynamics_joint_histogram_p_and_q_use_one_input_density() -> None:
    variables = ["dx0", "ego_speed", "npc_speed"]
    whole_domain = {
        "dx0": {"min": 10.0, "max": 25.0},
        "ego_speed": {"min": 30.0, "max": 40.0},
        "npc_speed": {"min": 10.0, "max": 25.0},
    }
    focused_domain = {
        "dx0": {"min": 10.0, "max": 14.0},
        "ego_speed": {"min": 30.0, "max": 34.0},
        "npc_speed": {"min": 10.0, "max": 14.0},
    }
    raw = _plan()
    raw.target_measure_definition.pop("parameters")
    raw.target_measure_definition["input_density"] = {
        "distribution": "joint_histogram",
        "variables": variables,
        "cells": [{"bounds": whole_domain, "weight": 1.0}],
    }
    raw.proposal_definition.pop("parameters")
    raw.proposal_definition["input_density"] = {
        "distribution": "joint_histogram",
        "variables": variables,
        "cells": [
            {"bounds": whole_domain, "weight": 0.7},
            {"bounds": focused_domain, "weight": 0.3},
        ],
    }

    sampler = DynamicsInputSampler(raw)
    sample = sampler.sample_proposal(seed=8)

    assert set(sample.values) == set(variables)
    assert float(sample.sampling_metadata["log_importance_weight"]) == pytest.approx(
        float(sample.sampling_metadata["log_p"]) - float(sample.sampling_metadata["log_q"])
    )
    assert sampler.importance_weight_upper_bound == pytest.approx(1.0 / 0.7)


def test_dynamics_uses_current_uniform_product_p_when_input_density_is_omitted() -> None:
    raw = _plan()
    raw.target_measure_definition.pop("parameters")
    raw.proposal_definition.pop("parameters")

    sampler = DynamicsInputSampler(raw)
    ordinary = sampler.sample_target(seed=12)
    importance = sampler.sample_proposal(seed=12)

    assert ordinary.values == importance.values
    assert float(importance.sampling_metadata["log_importance_weight"]) == 0.0


def test_dynamics_default_p_can_be_compared_with_a_joint_q() -> None:
    raw = _plan()
    raw.target_measure_definition.pop("parameters")
    raw.proposal_definition.pop("parameters")
    raw.proposal_definition["input_density"] = {
        "distribution": "joint_histogram",
        "variables": ["dx0", "ego_speed", "npc_speed"],
        "cells": [
            {
                "bounds": {
                    "dx0": {"min": 10.0, "max": 25.0},
                    "ego_speed": {"min": 30.0, "max": 40.0},
                    "npc_speed": {"min": 10.0, "max": 25.0},
                },
                "weight": 0.8,
            },
            {
                "bounds": {
                    "dx0": {"min": 10.0, "max": 14.0},
                    "ego_speed": {"min": 30.0, "max": 34.0},
                    "npc_speed": {"min": 10.0, "max": 14.0},
                },
                "weight": 0.2,
            },
        ],
    }

    sampler = DynamicsInputSampler(raw)

    assert sampler.importance_weight_upper_bound == pytest.approx(1.0 / 0.8)
