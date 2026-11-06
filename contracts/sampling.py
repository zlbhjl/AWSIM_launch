"""Common provenance contract for importance-sampled records."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from math import isclose, isfinite
from typing import Mapping


SAMPLING_META_KEY = "sampling"
REQUIRED_SAMPLING_KEYS = (
    "plan_id",
    "target_measure_id",
    "sampling_mode",
    "importance_weight_status",
)
IMPORTANCE_WEIGHT_KEYS = ("log_p", "log_q", "log_importance_weight")
SUPPORTED_SAMPLING_MODES = frozenset({"uniform", "manual_proposal", "adaptive"})
SUPPORTED_IMPORTANCE_WEIGHT_STATUSES = frozenset(
    {"available", "not_applicable", "unavailable"}
)
# ``positive``: finite log_p, log_q and log_importance_weight (the default and
# the only kind before weight_kind existed).  ``zero``: Q produced a value
# outside the support of P, so p = 0 exactly; log_p and log_importance_weight
# are None and log_q stays finite.  A zero weight is a valid sample that counts
# toward N, not a failed computation.
SUPPORTED_WEIGHT_KINDS = frozenset({"positive", "zero"})


class SamplingMetadataValidationError(ValueError):
    """Raised when sampling provenance does not satisfy the common contract."""


@dataclass(frozen=True, slots=True)
class ProposalCandidate:
    """A target-produced proposal Q passed to lifecycle orchestration.

    ``definition`` deliberately remains opaque: it is interpreted only by the
    target that produced it.  The remaining fields are shared provenance and
    lifecycle data used by orchestration and repository code.
    """

    plan_id: str
    proposal_id: str
    proposal_version: int
    definition: object
    reason: str
    diagnostics: Mapping[str, object]


def normalize_sampling_metadata(value: object) -> dict[str, object]:
    """Validate conditional sampling provenance and preserve extension fields."""

    if not isinstance(value, Mapping):
        raise SamplingMetadataValidationError("sampling metadata must be a mapping")
    missing = [key for key in REQUIRED_SAMPLING_KEYS if key not in value]
    if missing:
        raise SamplingMetadataValidationError(
            "sampling metadata is missing required keys: " + ", ".join(missing)
        )

    normalized = deepcopy(dict(value))
    for key in ("plan_id", "target_measure_id", "sampling_mode"):
        if not isinstance(normalized[key], str) or not normalized[key].strip():
            raise SamplingMetadataValidationError(
                f"sampling metadata {key} must be a non-empty string"
            )

    sampling_mode = normalized["sampling_mode"]
    if sampling_mode not in SUPPORTED_SAMPLING_MODES:
        raise SamplingMetadataValidationError(
            "sampling metadata sampling_mode must be one of "
            f"{sorted(SUPPORTED_SAMPLING_MODES)}"
        )

    weight_status = normalized["importance_weight_status"]
    if weight_status not in SUPPORTED_IMPORTANCE_WEIGHT_STATUSES:
        raise SamplingMetadataValidationError(
            "sampling metadata importance_weight_status must be one of "
            f"{sorted(SUPPORTED_IMPORTANCE_WEIGHT_STATUSES)}"
        )

    if sampling_mode in {"manual_proposal", "adaptive"}:
        _require_non_empty_string(normalized, "proposal_id")
    elif "proposal_id" in normalized:
        _require_non_empty_string(normalized, "proposal_id")

    if sampling_mode == "adaptive" and "proposal_version" not in normalized:
        raise SamplingMetadataValidationError(
            "sampling metadata proposal_version is required for adaptive sampling"
        )
    if "proposal_version" in normalized:
        proposal_version = normalized["proposal_version"]
        if (
            not isinstance(proposal_version, int)
            or isinstance(proposal_version, bool)
            or proposal_version < 1
        ):
            raise SamplingMetadataValidationError(
                "sampling metadata proposal_version must be a positive integer"
            )

    present_weight_keys = [key for key in IMPORTANCE_WEIGHT_KEYS if key in normalized]
    if weight_status == "available" and len(present_weight_keys) != len(IMPORTANCE_WEIGHT_KEYS):
        raise SamplingMetadataValidationError(
            "sampling metadata with importance_weight_status='available' "
            "must include log_p, log_q, and log_importance_weight"
        )
    if 0 < len(present_weight_keys) < len(IMPORTANCE_WEIGHT_KEYS):
        raise SamplingMetadataValidationError(
            "sampling metadata log_p, log_q, and log_importance_weight "
            "must be supplied together"
        )
    weight_kind = normalized.get("weight_kind", "positive")
    if weight_kind not in SUPPORTED_WEIGHT_KINDS:
        raise SamplingMetadataValidationError(
            "sampling metadata weight_kind must be one of "
            f"{sorted(SUPPORTED_WEIGHT_KINDS)}"
        )
    if weight_kind == "zero":
        if weight_status != "available":
            raise SamplingMetadataValidationError(
                "sampling metadata weight_kind='zero' requires "
                "importance_weight_status='available'"
            )
        _normalize_and_validate_zero_weight(normalized)
    elif len(present_weight_keys) == len(IMPORTANCE_WEIGHT_KEYS):
        _normalize_and_validate_log_weights(normalized)
    return normalized


def _require_non_empty_string(metadata: Mapping[str, object], key: str) -> None:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SamplingMetadataValidationError(
            f"sampling metadata {key} must be a non-empty string"
        )


def _normalize_and_validate_log_weights(metadata: dict[str, object]) -> None:
    for key in IMPORTANCE_WEIGHT_KEYS:
        raw_number = metadata[key]
        if isinstance(raw_number, bool) or not isinstance(raw_number, (int, float)):
            raise SamplingMetadataValidationError(
                f"sampling metadata {key} must be a finite number"
            )
        number = float(raw_number)
        if not isfinite(number):
            raise SamplingMetadataValidationError(
                f"sampling metadata {key} must be a finite number"
            )
        metadata[key] = number

    if not isclose(
        metadata["log_importance_weight"],
        metadata["log_p"] - metadata["log_q"],
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise SamplingMetadataValidationError(
            "sampling metadata log_importance_weight must equal log_p - log_q"
        )


def _normalize_and_validate_zero_weight(metadata: dict[str, object]) -> None:
    if metadata["log_p"] is not None or metadata["log_importance_weight"] is not None:
        raise SamplingMetadataValidationError(
            "sampling metadata with weight_kind='zero' must have "
            "log_p and log_importance_weight set to None"
        )
    raw_log_q = metadata["log_q"]
    if (
        isinstance(raw_log_q, bool)
        or not isinstance(raw_log_q, (int, float))
        or not isfinite(float(raw_log_q))
    ):
        raise SamplingMetadataValidationError(
            "sampling metadata log_q must be a finite number"
        )
    metadata["log_q"] = float(raw_log_q)


# Report section of capability ``internal_density`` (contracts/capabilities.py):
# meta.sampling.internal = {log_p, log_q: [one per proposal component],
# sampled_component, support}. The shared code turns it into a weight.
INTERNAL_REPORT_KEY = "internal"
TARGET_COMPONENT = "target"
SUPPORTED_INTERNAL_SUPPORTS = frozenset({"positive", "zero"})


def normalize_internal_report(value: object, *, components: int | None = None) -> dict[str, object]:
    """Validate the ``internal`` report of one sample.

    - ``log_p``: log density of the realised internal draw under P; finite for
      support ``positive``, None for ``zero`` (the draw lies outside P's support).
    - ``log_q``: one log density per proposal component, in the order of the
      proposal definition; finite or ``-inf`` (outside that component).
    - ``sampled_component``: the index of the component drawn from, or
      ``"target"`` when P itself was drawn (defensive mixture).
    - ``support``: ``positive`` or ``zero``.

    ``components`` (if given) is the number of proposal components expected.
    """

    if not isinstance(value, Mapping):
        raise SamplingMetadataValidationError("internal report must be a mapping")
    support = value.get("support", "positive")
    if support not in SUPPORTED_INTERNAL_SUPPORTS:
        raise SamplingMetadataValidationError(
            f"internal report support must be one of {sorted(SUPPORTED_INTERNAL_SUPPORTS)}"
        )
    raw_log_q = value.get("log_q")
    if isinstance(raw_log_q, (str, bytes)) or not isinstance(raw_log_q, (list, tuple)) or not raw_log_q:
        raise SamplingMetadataValidationError("internal report log_q must be a non-empty list")
    log_q: list[float] = []
    for item in raw_log_q:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise SamplingMetadataValidationError("internal report log_q entries must be numbers")
        number = float(item)
        if number != number or number == float("inf"):
            raise SamplingMetadataValidationError("internal report log_q entries must be finite or -inf")
        log_q.append(number)
    if components is not None and len(log_q) != components:
        raise SamplingMetadataValidationError(
            f"internal report log_q has {len(log_q)} entries, expected one per component ({components})"
        )
    raw_log_p = value.get("log_p")
    if support == "zero":
        if raw_log_p is not None:
            raise SamplingMetadataValidationError("internal report with support 'zero' must have log_p None")
        log_p = None
    else:
        if isinstance(raw_log_p, bool) or not isinstance(raw_log_p, (int, float)) or not isfinite(float(raw_log_p)):
            raise SamplingMetadataValidationError("internal report log_p must be a finite number")
        log_p = float(raw_log_p)
    sampled = value.get("sampled_component")
    if sampled == TARGET_COMPONENT:
        if log_p is None:
            raise SamplingMetadataValidationError("a draw from P cannot lie outside P's support")
    elif isinstance(sampled, bool) or not isinstance(sampled, int) or not 0 <= sampled < len(log_q):
        raise SamplingMetadataValidationError(
            "internal report sampled_component must be a component index or 'target'"
        )
    elif not isfinite(log_q[sampled]):
        raise SamplingMetadataValidationError("the sampled component must have a finite log density")
    return {"log_p": log_p, "log_q": log_q, "sampled_component": sampled, "support": support}


def sampling_metadata_from_meta(meta: Mapping[str, object]) -> dict[str, object] | None:
    """Return normalized sampling provenance if the record has it."""

    value = meta.get(SAMPLING_META_KEY)
    if value is None:
        return None
    return normalize_sampling_metadata(value)


def copy_sampling_metadata(
    source_meta: Mapping[str, object],
    destination_meta: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Copy normalized sampling provenance without changing other metadata."""

    copied = dict(destination_meta or {})
    sampling = sampling_metadata_from_meta(source_meta)
    if sampling is not None:
        copied[SAMPLING_META_KEY] = sampling
    return copied


__all__ = [
    "INTERNAL_REPORT_KEY",
    "REQUIRED_SAMPLING_KEYS",
    "SUPPORTED_INTERNAL_SUPPORTS",
    "TARGET_COMPONENT",
    "normalize_internal_report",
    "SAMPLING_META_KEY",
    "ProposalCandidate",
    "SamplingMetadataValidationError",
    "SUPPORTED_IMPORTANCE_WEIGHT_STATUSES",
    "SUPPORTED_SAMPLING_MODES",
    "SUPPORTED_WEIGHT_KINDS",
    "copy_sampling_metadata",
    "normalize_sampling_metadata",
    "sampling_metadata_from_meta",
]
