from __future__ import annotations

from pathlib import Path

from contracts.evaluation import EvaluationRecord, validate_evaluation_meta
from contracts.execution import RunStatus
from contracts.verification import VerificationInput


TREE_PATH = Path(__file__).resolve().parents[2] / "verification_core" / "ft4d" / "config" / "tree_prism_demo.json"


def build_verification_input(record: EvaluationRecord) -> VerificationInput:
    if record.target != "prism" or record.status != RunStatus.SUCCESS:
        raise ValueError("A successful PRISM EvaluationRecord is required")
    if str(record.input.get("record_kind", "sample")) != "sample":
        raise ValueError("A PRISM sample-path EvaluationRecord is required")
    validate_evaluation_meta(record.meta)
    identifier = record.case_id
    output = record.output
    events = {
        "FAILURE": _event(identifier, int(output["c_failure"])),
        "EARLY_FAILURE": _event(identifier, int(output["m_early_failure"])),
        "REPEATED_DEGRADATION": _event(identifier, int(output["m_repeated_degradation"])),
    }
    return VerificationInput(
        tree_mode="prism_demo",
        universal_dataset={identifier},
        events=events,
        assumptions={"sigma_pf_source": "dataset", "and_rule": "min", "tree_path": str(TREE_PATH)},
        meta={"schema_version": 1, "source_module": "targets.prism.verification_input", "tree_path": str(TREE_PATH), "target": "prism"},
    )


def _event(identifier: str, violated: int) -> dict[str, object]:
    dataset_e = {identifier} if violated else set()
    return {"dataset_d": {identifier}, "dataset_e": dataset_e, "total_count": 1, "error_count": violated, "sigma_pf": 0.0, "sigma_pb": float(violated)}
