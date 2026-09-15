from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from contracts.execution import TestCase

from .model_catalog import (
    PrismModelDefinition,
    get_model_definition,
    normalize_constants,
    resolve_horizon,
)


SUPPORTED_EXECUTION_KINDS = frozenset({"model_check", "sample_path"})


@dataclass(frozen=True)
class PrismExecutionProfile:
    definition: PrismModelDefinition
    constants: dict[str, float]
    horizon: int
    execution_kind: str
    record_kind: str
    prism_executable: str
    timeout_sec: float
    output_root: Path


def resolve_prism_execution_profile(
    test_case: TestCase,
    *,
    default_output_root: str | Path,
    default_prism_executable: str = "prism",
    default_timeout_sec: float = 30.0,
) -> PrismExecutionProfile:
    inputs = test_case.input
    model_id = str(inputs.get("model", "simple_reliability_dtmc"))
    definition = get_model_definition(model_id)
    constants = normalize_constants(definition, inputs)
    horizon = resolve_horizon(inputs)
    execution_kind = str(inputs.get("execution_kind", "sample_path"))
    if execution_kind not in SUPPORTED_EXECUTION_KINDS:
        supported = ", ".join(sorted(SUPPORTED_EXECUTION_KINDS))
        raise ValueError(
            f"Unsupported PRISM execution_kind: {execution_kind!r}; "
            f"supported: {supported}"
        )
    expected_record_kind = (
        "exact_model_check" if execution_kind == "model_check" else "sample"
    )
    record_kind = str(inputs.get("record_kind", expected_record_kind))
    if record_kind != expected_record_kind:
        raise ValueError(
            f"PRISM execution_kind={execution_kind!r} requires "
            f"record_kind={expected_record_kind!r}"
        )
    timeout_sec = float(inputs.get("timeout_sec", default_timeout_sec))
    if timeout_sec <= 0.0:
        raise ValueError("PRISM timeout_sec must be positive")
    prism_executable = str(
        inputs.get("prism_executable", default_prism_executable)
    ).strip()
    if not prism_executable:
        raise ValueError("PRISM executable must not be empty")
    output_root = Path(
        str(inputs.get("output_root", default_output_root))
    ).expanduser().resolve()
    return PrismExecutionProfile(
        definition=definition,
        constants=constants,
        horizon=horizon,
        execution_kind=execution_kind,
        record_kind=record_kind,
        prism_executable=prism_executable,
        timeout_sec=timeout_sec,
        output_root=output_root,
    )


__all__ = [
    "PrismExecutionProfile",
    "SUPPORTED_EXECUTION_KINDS",
    "resolve_prism_execution_profile",
]
