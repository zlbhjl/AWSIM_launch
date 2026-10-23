"""Mean rules of conditional innovation laws.

A rule maps the step context to the mean (location) of the innovation of each
active channel, in innovation units (the standardised Brownian increment, not
the SDE drift). Channels a rule does not name get mean 0. Rules are fixed
before the final evaluation and never learn across paths.

Supported kinds:

- ``constant``: ``{values: {channel: m}}``
- ``piecewise``: ``{values: {...}, pieces: [{time: [start, end], mode: m, values: {...}}]}``;
  matching pieces override in order (window is [start, end), ``mode`` optional).
  A piece may give ``rule: {...}`` (a non-piecewise rule) instead of ``values``,
  e.g. an affine state rule per time window; it sets the channels it names.
- ``state_threshold``: ``{state: name, threshold: c, below: {...}, above: {...}}``
  (``above`` applies when the state is >= c)
- ``affine_state``: ``{channels: [...], states: [...], offset: [...], matrix: [[...]]}``,
  mean = offset + matrix @ state[states] for the listed channels
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from .base import ProposalContext, check_known


@dataclass(frozen=True)
class ConstantMean:
    values: Mapping[str, float]

    def mean(self, context: ProposalContext) -> np.ndarray:
        return np.asarray([self.values.get(channel, 0.0) for channel in context.channels], dtype=float)

    def referenced_channels(self) -> frozenset[str]:
        return frozenset(self.values)

    def describe(self) -> dict[str, object]:
        return {"kind": "constant", "values": dict(self.values)}


@dataclass(frozen=True)
class _Piece:
    start_sec: float
    end_sec: float
    mode: str | None
    values: Mapping[str, float]
    rule: object | None = None  # a non-piecewise MeanRule, or None for constant values

    def matches(self, context: ProposalContext) -> bool:
        return (
            self.start_sec <= context.time_sec < self.end_sec
            and (self.mode is None or self.mode == context.mode)
        )


@dataclass(frozen=True)
class PiecewiseMean:
    values: Mapping[str, float]
    pieces: tuple[_Piece, ...]

    def mean(self, context: ProposalContext) -> np.ndarray:
        values = dict(self.values)
        for piece in self.pieces:
            if not piece.matches(context):
                continue
            if piece.rule is None:
                values.update(piece.values)
                continue
            named = piece.rule.referenced_channels()
            for channel, value in zip(context.channels, piece.rule.mean(context)):
                if channel in named:
                    values[channel] = float(value)
        return np.asarray([values.get(channel, 0.0) for channel in context.channels], dtype=float)

    def referenced_channels(self) -> frozenset[str]:
        names = set(self.values)
        for piece in self.pieces:
            names |= set(piece.values)
            if piece.rule is not None:
                names |= set(piece.rule.referenced_channels())
        return frozenset(names)

    def describe(self) -> dict[str, object]:
        return {
            "kind": "piecewise",
            "values": dict(self.values),
            "pieces": [
                {
                    "time": [piece.start_sec, piece.end_sec],
                    **({"mode": piece.mode} if piece.mode is not None else {}),
                    **({"rule": piece.rule.describe()} if piece.rule is not None else {"values": dict(piece.values)}),
                }
                for piece in self.pieces
            ],
        }


@dataclass(frozen=True)
class StateThresholdMean:
    state: str
    threshold: float
    below: Mapping[str, float]
    above: Mapping[str, float]

    def mean(self, context: ProposalContext) -> np.ndarray:
        values = self.above if context.state_value(self.state) >= self.threshold else self.below
        return np.asarray([values.get(channel, 0.0) for channel in context.channels], dtype=float)

    def referenced_channels(self) -> frozenset[str]:
        return frozenset(self.below) | frozenset(self.above)

    def describe(self) -> dict[str, object]:
        return {
            "kind": "state_threshold",
            "state": self.state,
            "threshold": self.threshold,
            "below": dict(self.below),
            "above": dict(self.above),
        }


@dataclass(frozen=True)
class AffineStateMean:
    channels: tuple[str, ...]
    states: tuple[str, ...]
    offset: np.ndarray
    matrix: np.ndarray

    def mean(self, context: ProposalContext) -> np.ndarray:
        x = np.asarray([context.state_value(name) for name in self.states], dtype=float)
        listed = self.offset + self.matrix @ x
        by_channel = dict(zip(self.channels, listed))
        return np.asarray([by_channel.get(channel, 0.0) for channel in context.channels], dtype=float)

    def referenced_channels(self) -> frozenset[str]:
        return frozenset(self.channels)

    def describe(self) -> dict[str, object]:
        return {
            "kind": "affine_state",
            "channels": list(self.channels),
            "states": list(self.states),
            "offset": self.offset.tolist(),
            "matrix": self.matrix.tolist(),
        }


MeanRule = ConstantMean | PiecewiseMean | StateThresholdMean | AffineStateMean


def parse_mean_rule(
    spec: object,
    *,
    channels: frozenset[str],
    state_names: tuple[str, ...],
    context: str,
) -> MeanRule:
    """Parse a mean rule; channel and state names are checked against the model."""
    if spec is None:
        return ConstantMean({})
    rule = _mapping(spec, context)
    kind = rule.get("kind")
    if kind == "constant":
        values = _channel_values(rule.get("values", {}), channels, f"{context}.values")
        return ConstantMean(values)
    if kind == "piecewise":
        raw_pieces = rule.get("pieces", [])
        if not isinstance(raw_pieces, list):
            raise ValueError(f"{context}.pieces must be a list")
        pieces = []
        for index, raw in enumerate(raw_pieces):
            piece = _mapping(raw, f"{context}.pieces[{index}]")
            window = piece.get("time", [0.0, None])
            if not isinstance(window, (list, tuple)) or len(window) != 2:
                raise ValueError(f"{context}.pieces[{index}].time must be [start, end]")
            start = _number(window[0], f"{context}.pieces[{index}].time[0]")
            end = math.inf if window[1] in {None, "inf"} else _number(window[1], f"{context}.pieces[{index}].time[1]")
            mode = piece.get("mode")
            if mode is not None and not isinstance(mode, str):
                raise ValueError(f"{context}.pieces[{index}].mode must be a string")
            if piece.get("rule") is not None:
                if piece.get("values") is not None:
                    raise ValueError(f"{context}.pieces[{index}] must give values or rule, not both")
                nested = _mapping(piece["rule"], f"{context}.pieces[{index}].rule")
                if nested.get("kind") == "piecewise":
                    raise ValueError(f"{context}.pieces[{index}].rule must not be piecewise")
                pieces.append(_Piece(start, end, mode, {}, parse_mean_rule(
                    nested, channels=channels, state_names=state_names, context=f"{context}.pieces[{index}].rule"
                )))
                continue
            pieces.append(_Piece(start, end, mode, _channel_values(
                piece.get("values", {}), channels, f"{context}.pieces[{index}].values"
            )))
        return PiecewiseMean(_channel_values(rule.get("values", {}), channels, f"{context}.values"), tuple(pieces))
    if kind == "state_threshold":
        state = rule.get("state")
        if not isinstance(state, str):
            raise ValueError(f"{context}.state must be a state name")
        check_known([state], state_names, what="states", context=context)
        return StateThresholdMean(
            state=state,
            threshold=_number(rule.get("threshold"), f"{context}.threshold"),
            below=_channel_values(rule.get("below", {}), channels, f"{context}.below"),
            above=_channel_values(rule.get("above", {}), channels, f"{context}.above"),
        )
    if kind == "affine_state":
        rule_channels = _names(rule.get("channels"), f"{context}.channels")
        states = _names(rule.get("states"), f"{context}.states")
        check_known(rule_channels, channels, what="noise channels", context=context)
        check_known(states, state_names, what="states", context=context)
        offset = _array(rule.get("offset", [0.0] * len(rule_channels)), f"{context}.offset")
        matrix = _array(rule.get("matrix"), f"{context}.matrix")
        if offset.shape != (len(rule_channels),) or matrix.shape != (len(rule_channels), len(states)):
            raise ValueError(
                f"{context}: offset must have {len(rule_channels)} entries and matrix "
                f"{len(rule_channels)}x{len(states)} (channels x states)"
            )
        return AffineStateMean(rule_channels, states, offset, matrix)
    raise ValueError(f"{context}.kind must be one of constant, piecewise, state_threshold, affine_state")


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be a mapping")
    return value


def _number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{context} must be a finite number")
    return float(value)


def _channel_values(value: object, channels: frozenset[str], context: str) -> dict[str, float]:
    mapping = _mapping(value, context)
    check_known(mapping, channels, what="noise channels", context=context)
    return {str(name): _number(number, f"{context}.{name}") for name, number in mapping.items()}


def _names(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{context} must be a non-empty list of names")
    if len(set(value)) != len(value):
        raise ValueError(f"{context} must not repeat names")
    return tuple(value)


def _array(value: object, context: str) -> np.ndarray:
    try:
        array = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{context} must be numeric") from exc
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{context} must be finite")
    return array


__all__ = [
    "AffineStateMean",
    "ConstantMean",
    "MeanRule",
    "PiecewiseMean",
    "StateThresholdMean",
    "parse_mean_rule",
]
