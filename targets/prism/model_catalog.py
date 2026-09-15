from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class PrismModelDefinition:
    model_id: str
    case_kind: str
    model_path: Path
    properties_path: Path
    required_constants: tuple[str, ...]
    probability_groups: tuple[tuple[str, ...], ...]
    default_horizon: int = 20


SIMPLE_RELIABILITY_DTMC = PrismModelDefinition(
    model_id="simple_reliability_dtmc",
    case_kind="simple_reliability_dtmc",
    model_path=PACKAGE_DIR / "models" / "simple_reliability_dtmc.prism",
    properties_path=PACKAGE_DIR / "properties" / "simple_reliability_dtmc.props",
    required_constants=(
        "P_NORMAL_DEGRADE",
        "P_NORMAL_FAILURE",
        "P_DEGRADED_NORMAL",
        "P_DEGRADED_FAILURE",
    ),
    probability_groups=(
        ("P_NORMAL_DEGRADE", "P_NORMAL_FAILURE"),
        ("P_DEGRADED_NORMAL", "P_DEGRADED_FAILURE"),
    ),
)


_CATALOG = {SIMPLE_RELIABILITY_DTMC.model_id: SIMPLE_RELIABILITY_DTMC}


def get_model_definition(model_id: str) -> PrismModelDefinition:
    try:
        return _CATALOG[str(model_id)]
    except KeyError as exc:
        supported = ", ".join(sorted(_CATALOG))
        raise ValueError(f"Unsupported PRISM model_id: {model_id!r}; supported: {supported}") from exc


def normalize_constants(
    definition: PrismModelDefinition,
    values: Mapping[str, object],
) -> dict[str, float]:
    normalized: dict[str, float] = {}
    aliases = {
        "p_normal_degrade": "P_NORMAL_DEGRADE",
        "p_normal_failure": "P_NORMAL_FAILURE",
        "p_fail": "P_NORMAL_FAILURE",
        "p_degraded_normal": "P_DEGRADED_NORMAL",
        "p_degraded_failure": "P_DEGRADED_FAILURE",
    }
    for raw_key, raw_value in values.items():
        key = aliases.get(str(raw_key), str(raw_key).upper())
        if key in definition.required_constants:
            normalized[key] = float(raw_value)

    defaults = {
        "P_NORMAL_DEGRADE": 0.10,
        "P_NORMAL_FAILURE": 0.01,
        "P_DEGRADED_NORMAL": 0.30,
        "P_DEGRADED_FAILURE": 0.10,
    }
    for key in definition.required_constants:
        normalized.setdefault(key, defaults[key])

    for key, value in normalized.items():
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{key} must be in [0, 1], got {value}")
    for group in definition.probability_groups:
        total = sum(normalized[name] for name in group)
        if total > 1.0 + 1e-12:
            raise ValueError(f"Probabilities for {', '.join(group)} must sum to <= 1, got {total}")
    return normalized


def resolve_horizon(values: Mapping[str, object], default: int = 20) -> int:
    raw = values.get("steps", values.get("horizon", default))
    horizon = int(raw)
    if horizon < 1:
        raise ValueError("steps/horizon must be a positive integer")
    if horizon > 100_000:
        raise ValueError("steps/horizon must not exceed 100000")
    return horizon

