"""Events judged on the time grid of a registered model's trajectory.

- ``terminal_exceedance``: ``{threshold, side, state}``; the state at the
  final grid time is above the threshold (``upper``) or its absolute value is
  (``two_sided``). ``state`` defaults to the first state.
- ``max_exceedance``: ``{threshold, side, states}``; at some grid time
  (including t = 0) one of ``states`` (default: all) is above the threshold
  (``upper``) or its absolute value is (``two_sided``).

The event is judged on the grid only; crossings between grid points are not
part of the numerical model (docs/sde_importance_sampling_plan.md §3).
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np

EVENT_KINDS = ("terminal_exceedance", "max_exceedance")
EVENT_SIDES = ("upper", "two_sided")


def parse_event(value: object, *, state_names: tuple[str, ...]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("event must be a mapping")
    kind = value.get("kind")
    if kind not in EVENT_KINDS:
        raise ValueError(f"event.kind must be one of {list(EVENT_KINDS)}")
    side = value.get("side", "upper")
    if side not in EVENT_SIDES:
        raise ValueError(f"event.side must be one of {list(EVENT_SIDES)}")
    threshold = value.get("threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(float(threshold)):
        raise ValueError("event.threshold must be a finite number")
    event: dict[str, object] = {"kind": kind, "threshold": float(threshold), "side": side}
    if kind == "terminal_exceedance":
        state = value.get("state", state_names[0])
        if state not in state_names:
            raise ValueError(f"event.state must be one of {list(state_names)}")
        event["state"] = state
    else:
        states = value.get("states", list(state_names))
        if not isinstance(states, list) or not states or any(name not in state_names for name in states):
            raise ValueError(f"event.states must be a non-empty list of {list(state_names)}")
        event["states"] = list(states)
    return event


def evaluate_event(event: Mapping[str, object], trace: Mapping[str, np.ndarray]) -> dict[str, object]:
    """Return ``c_event`` and the values the event was judged on."""
    threshold = float(event["threshold"])
    two_sided = event["side"] == "two_sided"
    if event["kind"] == "terminal_exceedance":
        values = np.asarray(trace[str(event["state"])], dtype=float)
        if values.size == 0:
            raise ValueError("trace is empty")
        terminal = float(values[-1])
        exceeded = abs(terminal) > threshold if two_sided else terminal > threshold
        return {"c_event": int(exceeded), "event_value": terminal}
    stacked = np.vstack([np.asarray(trace[str(name)], dtype=float) for name in event["states"]])
    if stacked.size == 0:
        raise ValueError("trace is empty")
    judged = np.abs(stacked) if two_sided else stacked
    peak = float(np.max(judged))
    return {"c_event": int(peak > threshold), "event_value": peak}


__all__ = ["EVENT_KINDS", "EVENT_SIDES", "evaluate_event", "parse_event"]
