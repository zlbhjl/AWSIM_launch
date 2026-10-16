"""Load the common envelope of an importance-sampling plan.

Target-specific definitions deliberately remain opaque here.  They are
validated by the target adapter immediately before sampling or execution.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml


SUPPORTED_SAMPLING_PLAN_SCHEMA_VERSIONS = frozenset({1})
SUPPORTED_SAMPLING_MODES = frozenset({"uniform", "manual_proposal", "adaptive"})


class SamplingPlanValidationError(ValueError):
    """Raised when the common sampling-plan envelope is invalid."""


@dataclass(frozen=True, slots=True)
class SamplingPlan:
    """Validated common fields plus opaque target-specific definitions."""

    schema_version: int
    experiment_id: str
    target: str
    target_measure_id: str
    target_measure_definition: object
    proposal_id: str
    sampling_mode: str
    proposal_definition: object
    # These sections are intentionally opaque at the common layer.  A target
    # adapter decides whether it can interpret and safely use them.
    proposal_search: object | None = None
    proposal_update: object | None = None
    sequential_decision: object | None = None
    # Kept while existing plans still use the previous spelling.
    adaptive_update: object | None = None


def load_sampling_plan(path: str | Path) -> SamplingPlan:
    """Read a YAML plan and validate only its target-independent envelope."""

    source_path = Path(path)
    try:
        raw = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SamplingPlanValidationError(
            f"Could not read sampling plan {source_path}: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise SamplingPlanValidationError(
            f"Could not parse sampling plan {source_path}: {exc}"
        ) from exc
    return sampling_plan_from_mapping(raw)


def sampling_plan_from_mapping(raw: object) -> SamplingPlan:
    """Build a plan from decoded YAML without interpreting target definitions."""

    plan = _require_mapping(raw, "sampling plan")
    schema_version = _require_int(plan, "schema_version")
    if schema_version not in SUPPORTED_SAMPLING_PLAN_SCHEMA_VERSIONS:
        raise SamplingPlanValidationError(
            "Unsupported sampling-plan schema_version: "
            f"{schema_version}. Supported versions: "
            f"{sorted(SUPPORTED_SAMPLING_PLAN_SCHEMA_VERSIONS)}"
        )

    target_measure = _require_mapping(plan.get("target_measure"), "target_measure")
    proposal = _require_mapping(plan.get("proposal"), "proposal")
    sampling_mode = _require_string(proposal, "mode", context="proposal")
    if sampling_mode not in SUPPORTED_SAMPLING_MODES:
        raise SamplingPlanValidationError(
            f"proposal.mode must be one of {sorted(SUPPORTED_SAMPLING_MODES)}, "
            f"got {sampling_mode!r}"
        )

    return SamplingPlan(
        schema_version=schema_version,
        experiment_id=_require_string(plan, "experiment_id"),
        target=_require_string(plan, "target"),
        target_measure_id=_require_string(target_measure, "id", context="target_measure"),
        target_measure_definition=_require_key(
            target_measure, "definition", context="target_measure"
        ),
        proposal_id=_require_string(proposal, "id", context="proposal"),
        sampling_mode=sampling_mode,
        proposal_definition=_require_key(proposal, "definition", context="proposal"),
        proposal_search=deepcopy(plan.get("proposal_search")),
        proposal_update=deepcopy(plan.get("proposal_update")),
        sequential_decision=deepcopy(plan.get("sequential_decision")),
        adaptive_update=deepcopy(plan.get("adaptive_update")),
    )


def _require_mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SamplingPlanValidationError(f"{context} must be a mapping")
    return value


def _require_int(plan: Mapping[str, object], key: str) -> int:
    value = _require_key(plan, key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SamplingPlanValidationError(f"{key} must be an integer")
    return value


def _require_string(
    plan: Mapping[str, object],
    key: str,
    *,
    context: str = "sampling plan",
) -> str:
    value = _require_key(plan, key, context=context)
    if not isinstance(value, str) or not value.strip():
        raise SamplingPlanValidationError(f"{context}.{key} must be a non-empty string")
    return value


def _require_key(
    plan: Mapping[str, object],
    key: str,
    *,
    context: str = "sampling plan",
) -> object:
    if key not in plan:
        raise SamplingPlanValidationError(f"{context}.{key} is required")
    return deepcopy(plan[key])


__all__ = [
    "SUPPORTED_SAMPLING_MODES",
    "SUPPORTED_SAMPLING_PLAN_SCHEMA_VERSIONS",
    "SamplingPlan",
    "SamplingPlanValidationError",
    "load_sampling_plan",
    "sampling_plan_from_mapping",
]
