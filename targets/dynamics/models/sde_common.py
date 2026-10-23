"""Shared resolution and judging for registered SDE models with grid events.

An adapter builds its ``HybridSystem`` from ``model_params`` and initial inputs
and calls :func:`resolve_sde_model`, which reads the common inputs:

- ``solver_kind`` (``"sde"``), ``sde_dt_sec``, ``horizon_sec``
- ``sde_seed`` and optional ``sde_mixture_seed``
- ``event`` (see :mod:`targets.dynamics.models.events`)
- ``noise_path_p`` (P) and ``noise_q`` / ``noise_path_q`` (Q)
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np

from ..profile import noise_path_definition, parse_noise_path_p
from ..runners.ode import ODESolverSettings
from ..runners.sde import SDESolverSettings
from .events import evaluate_event, parse_event
from .registry import ResolvedModelRun, resolve_noise_proposal


def resolve_sde_model(
    *,
    model_id: str,
    system,
    inputs: Mapping[str, object],
    channels: frozenset[str],
    model_meta: Mapping[str, object],
) -> ResolvedModelRun:
    state_names = tuple(system.state_names)
    if str(inputs.get("solver_kind", "sde")) != "sde":
        raise ValueError(f"{model_id} supports solver_kind 'sde' only")
    dt_sec = positive(inputs.get("sde_dt_sec"), "sde_dt_sec")
    horizon_sec = positive(inputs.get("horizon_sec"), "horizon_sec")
    if dt_sec > horizon_sec:
        raise ValueError("sde_dt_sec must not exceed horizon_sec")
    event = parse_event(inputs.get("event"), state_names=state_names)
    seed = integer(inputs.get("sde_seed"), "sde_seed")
    mixture_seed = inputs.get("sde_mixture_seed")
    mixture_seed = None if mixture_seed is None else integer(mixture_seed, "sde_mixture_seed")
    noise_target = parse_noise_path_p(inputs.get("noise_path_p"), channels=channels)
    noise_proposal, proposal_definition = resolve_noise_proposal(
        inputs, channels=channels, state_names=state_names
    )
    if noise_proposal is not None and not system.has_noise:
        raise ValueError("an SDE noise proposal requires non-zero diffusion")
    ode_settings = ODESolverSettings(horizon_sec=horizon_sec, max_step_sec=dt_sec)
    initial_inputs = {name: inputs[name] for name in model_meta.get("initial_input_names", ()) if name in inputs}
    return ResolvedModelRun(
        system=system,
        solver_kind="sde",
        ode_settings=ode_settings,
        sde_settings=SDESolverSettings(
            horizon_sec=horizon_sec,
            dt_sec=dt_sec,
            seed=seed,
            ode_fallback=ode_settings,
            noise_target=noise_target,
            noise_proposal=noise_proposal,
            mixture_seed=mixture_seed,
            initial_inputs=initial_inputs,
        ),
        execution_meta={
            "model_id": model_id,
            **{key: value for key, value in model_meta.items() if key != "initial_input_names"},
            "solver_kind": "sde",
            "sde_dt_sec": dt_sec,
            "horizon_sec": horizon_sec,
            "event": event,
            "sde_seed": seed,
            "sde_mixture_seed": mixture_seed,
            "noise_path_p": noise_path_definition(noise_target),
            "noise_q": proposal_definition,
        },
    )


def interpret_sde_model(
    payload: Mapping[str, object],
    trace: Mapping[str, np.ndarray],
    *,
    state_names: tuple[str, ...],
) -> dict[str, object]:
    """``c_event``, ``event_value``, and the terminal and max |value| of each state."""
    execution = payload["execution"]
    event = parse_event(execution.get("event"), state_names=state_names)
    outputs = evaluate_event(event, trace)
    for name in state_names:
        values = np.asarray(trace[name], dtype=float)
        outputs[f"{name}_terminal"] = float(values[-1])
        outputs[f"max_abs_{name}"] = float(np.max(np.abs(values)))
    outputs["step_count"] = int(len(trace["time_sec"]) - 1)
    return outputs


def mapping(value: object, name: str) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def positive(value: object, name: str) -> float:
    number = finite(value, name)
    if number <= 0.0:
        raise ValueError(f"{name} must be positive")
    return number


def integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


__all__ = ["interpret_sde_model", "resolve_sde_model"]
