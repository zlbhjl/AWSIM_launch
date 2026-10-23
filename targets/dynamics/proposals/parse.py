"""Parse the ``noise`` proposal of a v2 plan into a :class:`PathProposal`.

    noise: {kind: gaussian, mean_rule: {...}, covariance: {...}}
    noise: {kind: student_t, dof: 5, location_rule: {...}, scale: {...}}
    noise:
      kind: mixture
      defensive_weight: 0.2          # optional beta; noise weight <= 1/beta
      components:
        - {weight: 0.5, law: {kind: gaussian, ...}}
        - {weight: 0.5, law: {kind: target}}   # P itself as a component

A single law is a direct proposal. Means, locations, covariances and scales are
in innovation units (docs/sde_importance_sampling_plan.md §5); the legacy
``noise_path_q`` (``gaussian_drift`` in SDE drift units) is parsed by
``targets.dynamics.profile``. A new law family is added by registering its
parser in ``LAW_PARSERS``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from .base import InnovationLaw
from .gaussian import parse_gaussian_law
from .laplace import parse_laplace_law
from .path import TARGET, PathProposal
from .student_t import parse_student_t_law

LawParser = Callable[..., InnovationLaw]
# kind -> parser(spec, *, channels, state_names, context)
LAW_PARSERS: dict[str, LawParser] = {
    "gaussian": parse_gaussian_law,
    "student_t": parse_student_t_law,
    "laplace": parse_laplace_law,
}


def parse_law(
    spec: object,
    *,
    channels: frozenset[str],
    state_names: tuple[str, ...],
    context: str,
) -> InnovationLaw:
    if not isinstance(spec, Mapping):
        raise ValueError(f"{context} must be a mapping")
    kind = spec.get("kind")
    parser = LAW_PARSERS.get(str(kind))
    if parser is None:
        raise ValueError(f"{context}.kind must be one of {sorted(LAW_PARSERS)}")
    return parser(spec, channels=channels, state_names=state_names, context=context)


def parse_noise_proposal(
    spec: object,
    *,
    channels: frozenset[str],
    state_names: tuple[str, ...],
    context: str = "noise",
) -> PathProposal:
    """Parse ``noise`` into a path proposal checked against the model's channels and states."""
    if isinstance(spec, Mapping) and spec.get("kind") == "mixture":
        return _parse_mixture(spec, channels=channels, state_names=state_names, context=context)
    law = parse_law(spec, channels=channels, state_names=state_names, context=context)
    return PathProposal(components=(law,), weights=(1.0,))


def _parse_mixture(
    spec: Mapping[str, object],
    *,
    channels: frozenset[str],
    state_names: tuple[str, ...],
    context: str,
) -> PathProposal:
    raw_components = spec.get("components")
    if not isinstance(raw_components, list) or not raw_components:
        raise ValueError(f"{context}.components must be a non-empty list")
    components = []
    weights = []
    for index, raw in enumerate(raw_components):
        where = f"{context}.components[{index}]"
        if not isinstance(raw, Mapping):
            raise ValueError(f"{where} must be a mapping with weight and law")
        weight = raw.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise ValueError(f"{where}.weight must be a number")
        law = raw.get("law")
        if isinstance(law, Mapping) and law.get("kind") == "target":
            components.append(TARGET)
        elif isinstance(law, Mapping) and law.get("kind") == "mixture":
            raise ValueError(f"{where}.law must not be a mixture")
        else:
            components.append(parse_law(law, channels=channels, state_names=state_names, context=f"{where}.law"))
        weights.append(float(weight))
    defensive_weight = spec.get("defensive_weight")
    if defensive_weight is not None and (
        isinstance(defensive_weight, bool) or not isinstance(defensive_weight, (int, float))
    ):
        raise ValueError(f"{context}.defensive_weight must be a number")
    try:
        return PathProposal(
            tuple(components),
            tuple(weights),
            None if defensive_weight is None else float(defensive_weight),
        )
    except ValueError as exc:
        raise ValueError(f"{context}: {exc}") from exc


__all__ = ["LAW_PARSERS", "parse_law", "parse_noise_proposal"]
