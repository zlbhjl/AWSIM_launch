from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from contracts.evaluation import EvaluationRecord, validate_evaluation_meta
from contracts.execution import RunStatus
from contracts.verification import VerificationInput
from targets.prism.dataset_adapter import PrismDatasetAdapter


TREE_PATH = Path(__file__).resolve().parents[2] / "verification_core" / "ft4d" / "config" / "tree_prism_demo.json"

BASIC_EVENT_OUTPUT_KEYS = {
    "FAILURE": "c_failure",
    "EARLY_FAILURE": "m_early_failure",
    "REPEATED_DEGRADATION": "m_repeated_degradation",
}


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


def build_verification_input_from_records(
    records: Sequence[EvaluationRecord],
    *,
    assumptions: Mapping[str, object] | None = None,
    meta: Mapping[str, object] | None = None,
) -> VerificationInput:
    """Aggregate many PRISM sample-path records into a single VerificationInput.

    Unlike ``build_verification_input``, which treats one sample path as its
    own universe of one, this aggregates a whole batch of sample paths (e.g.
    a full binomial_ci/dkw experiment) into one ``universal_dataset`` so that
    each basic event's ``dataset_d``/``dataset_e`` reflects the real violation
    rate across the batch rather than a single 0/1 case.
    """
    eligible = [
        record
        for record in PrismDatasetAdapter.sample_records(records)
        if record.target == "prism" and record.status == RunStatus.SUCCESS
    ]
    if not eligible:
        raise ValueError("At least one successful PRISM sample-path EvaluationRecord is required")
    for record in eligible:
        validate_evaluation_meta(record.meta)

    universal_dataset = {record.case_id for record in eligible}
    events = {
        event_id: _aggregate_event(eligible, universal_dataset, output_key)
        for event_id, output_key in BASIC_EVENT_OUTPUT_KEYS.items()
    }

    merged_assumptions = {
        "sigma_pf_source": "dataset",
        "and_rule": "min",
        "tree_path": str(TREE_PATH),
    }
    merged_assumptions.update(assumptions or {})

    merged_meta = {
        "schema_version": 1,
        "source_module": "targets.prism.verification_input",
        "tree_path": str(TREE_PATH),
        "target": "prism",
        "record_count": len(records),
        "eligible_record_count": len(eligible),
    }
    merged_meta.update(meta or {})

    return VerificationInput(
        tree_mode="prism_demo",
        universal_dataset=universal_dataset,
        events=events,
        assumptions=merged_assumptions,
        meta=merged_meta,
    )


def _aggregate_event(
    records: Sequence[EvaluationRecord],
    universal_dataset: set[str],
    output_key: str,
) -> dict[str, object]:
    dataset_e = {
        record.case_id for record in records if int(record.output[output_key]) == 1
    }
    return {
        "dataset_d": set(universal_dataset),
        "dataset_e": dataset_e,
        "total_count": len(universal_dataset),
        "error_count": len(dataset_e),
    }
