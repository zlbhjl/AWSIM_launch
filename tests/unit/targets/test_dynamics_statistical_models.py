"""Statistical modes of registered dynamics models: model choice, defaults and checks."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from targets.dynamics.models.registry import resolve_dynamics_model, statistical_defaults, get_model_adapter
from targets.dynamics.plugin import PLUGIN

OU_PARAMS = {"sde_dt_sec": 0.01, "horizon_sec": 1.0, "event": {"kind": "terminal_exceedance", "threshold": 1.0}}


def _prepare(**changes):
    values = dict(mode="binomial_ci", case_kind="ou_scalar", case_kind_explicit=True, params=OU_PARAMS,
                  explicit_options=frozenset())
    values.update(changes)
    return PLUGIN.prepare_statistical_run(**values)


def test_model_comes_from_model_id_or_the_case_kind() -> None:
    assert resolve_dynamics_model({"model_id": "ou_scalar"}, "uturn", case_kind_explicit=False)[0] == "ou_scalar"
    assert resolve_dynamics_model({}, "ou_augmented")[0] == "ou_augmented"
    with pytest.raises(ValueError, match="does not match"):
        resolve_dynamics_model({"model_id": "ou_scalar"}, "nonlinear2d")
    with pytest.raises(ValueError, match="unsupported dynamics model_id"):
        resolve_dynamics_model({}, "lane_change")


def test_uturn_keeps_its_previous_defaults() -> None:
    prepared = _prepare(case_kind="uturn", params={})

    assert prepared["params"] == {}
    assert sorted(prepared["default_input_distribution"]) == ["dx0", "ego_speed", "npc_speed"]
    metrics = PLUGIN.iid_statistical_metrics(SimpleNamespace(params={}, case_kind="uturn", dynamics_decision_mode="screening"))
    assert metrics["binary"] == "c_screening_candidate" and metrics["dkw"] == "min_ttc"
    assert PLUGIN.default_config_module("uturn") == "scenario_specs.uturn"


def test_registered_model_adds_its_id_and_the_sde_solver() -> None:
    prepared = _prepare()

    assert prepared == {"case_kind": "ou_scalar", "params": {"model_id": "ou_scalar", "solver_kind": "sde"},
                        "default_input_distribution": {}}
    assert _prepare(case_kind_explicit=False, case_kind="uturn", params={**OU_PARAMS, "model_id": "ou_scalar"})["case_kind"] == "ou_scalar"


def test_registered_model_uses_its_own_metrics() -> None:
    metrics = PLUGIN.iid_statistical_metrics(
        SimpleNamespace(params={"model_id": "ou_augmented"}, case_kind="ou_augmented", dynamics_decision_mode="judgment"))

    assert metrics == {"binary": "c_event", "sprt": "c_event", "dkw": "x_terminal", "ebstop": "x_terminal",
                       "minimum_dkw_value": None}
    assert statistical_defaults(get_model_adapter("nonlinear2d"))["continuous_metric"] == "event_value"
    with pytest.raises(ValueError, match="only for the U-turn"):
        PLUGIN.iid_statistical_metrics(
            SimpleNamespace(params={"model_id": "ou_scalar"}, case_kind="ou_scalar", dynamics_decision_mode="screening"))


def test_missing_fixed_inputs_are_reported_before_running() -> None:
    with pytest.raises(ValueError, match="dynamics model ou_scalar: event must be a mapping"):
        _prepare(params={"sde_dt_sec": 0.01, "horizon_sec": 1.0})
    with pytest.raises(ValueError, match="only for the U-turn"):
        _prepare(decision_mode="screening")


def test_ebstop_on_an_unbounded_metric_needs_an_explicit_range() -> None:
    with pytest.raises(ValueError, match="--ebstop-value-range-min"):
        _prepare(mode="ebstop")
    assert _prepare(mode="ebstop", explicit_options=frozenset({"ebstop_value_range_min", "ebstop_value_range_max"}))


def test_noise_only_distribution_needs_an_sde_seed_source() -> None:
    from orchestration.random_parameter_sampling import normalize_input_distribution

    assert normalize_input_distribution({}, allow_empty=True) == {}
    with pytest.raises(ValueError, match="must not be empty"):
        normalize_input_distribution({})


def test_worker_validation_uses_the_model_registry() -> None:
    PLUGIN.validate_worker_args(case_kind="ou_scalar", params={"model_id": "ou_scalar"})
    with pytest.raises(ValueError, match="unsupported dynamics model_id"):
        PLUGIN.validate_worker_args(case_kind="lane_change", params={})
