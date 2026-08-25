from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from contracts.execution import RunStatus


DEFAULT_EVALUATION_SCHEMA_VERSION = 1
REQUIRED_EVALUATION_META_KEYS = ("schema_version", "source_module")


def ensure_evaluation_meta(
    meta: Mapping[str, object] | None = None,
    *,
    source_module: str,
    schema_version: int = DEFAULT_EVALUATION_SCHEMA_VERSION,
    created_at: str | None = None,
) -> dict[str, object]:
    merged_meta = dict(meta or {})
    merged_meta.setdefault("schema_version", schema_version)
    merged_meta.setdefault("source_module", source_module)
    if created_at is not None:
        merged_meta.setdefault("created_at", created_at)
    return merged_meta


def validate_evaluation_meta(
    meta: Mapping[str, object],
    *,
    required_keys: Sequence[str] = REQUIRED_EVALUATION_META_KEYS,
) -> None:
    missing_keys = [key for key in required_keys if key not in meta]
    if missing_keys:
        raise ValueError(
            "EvaluationRecord.meta is missing required keys: "
            + ", ".join(sorted(missing_keys))
        )

    schema_version = meta["schema_version"]
    if not isinstance(schema_version, int):
        raise ValueError("EvaluationRecord.meta['schema_version'] must be an int")

    source_module = meta["source_module"]
    if not isinstance(source_module, str) or not source_module.strip():
        raise ValueError(
            "EvaluationRecord.meta['source_module'] must be a non-empty string"
        )


@dataclass
class EvaluationRecord:
    case_id: str
    target: str
    case_kind: str
    status: RunStatus
    input: dict[str, object] = field(default_factory=dict)
    output: dict[str, object] = field(default_factory=dict)
    evidence: dict[str, str] = field(default_factory=dict)
    meta: dict[str, object] = field(default_factory=dict)
