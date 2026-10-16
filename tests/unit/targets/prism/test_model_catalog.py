import pytest

from targets.prism.model_catalog import SIMPLE_RELIABILITY_DTMC, normalize_constants, resolve_horizon


def test_normalize_constants_accepts_lowercase_cli_names():
    constants = normalize_constants(
        SIMPLE_RELIABILITY_DTMC,
        {"p_normal_degrade": 0.2, "p_fail": 0.05, "p_degraded_normal": 0.3, "p_degraded_failure": 0.1},
    )

    assert constants["P_NORMAL_DEGRADE"] == 0.2
    assert constants["P_NORMAL_FAILURE"] == 0.05


def test_normalize_constants_rejects_invalid_probability_group():
    with pytest.raises(ValueError, match="sum"):
        normalize_constants(SIMPLE_RELIABILITY_DTMC, {"p_normal_degrade": 0.8, "p_normal_failure": 0.3})


def test_resolve_horizon_uses_steps_and_rejects_zero():
    assert resolve_horizon({"steps": 7}) == 7
    with pytest.raises(ValueError):
        resolve_horizon({"horizon": 0})
