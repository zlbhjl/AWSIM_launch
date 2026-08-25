from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from contracts.evaluation import EvaluationRecord, validate_evaluation_meta
from contracts.execution import RunStatus
from contracts.verification import VerificationInput
from targets.awsim.case_kinds import load_event_definitions


Condition = str | Callable[[EvaluationRecord], bool] | None


@dataclass(frozen=True)
class VerificationContext:
    tree_mode: str = "basic"
    source_module: str = "targets.awsim.verification_input"
    config_module: str | None = None
    include_statuses: frozenset[RunStatus] = field(
        default_factory=lambda: frozenset({RunStatus.SUCCESS}),
    )


class VerificationInputBuilder:
    def __init__(self, context: VerificationContext | None = None):
        self.context = context or VerificationContext()

    def build(
        self,
        records: EvaluationRecord | Sequence[EvaluationRecord],
        event_definitions: Mapping[str, Mapping[str, object]] | None = None,
        *,
        assumptions: Mapping[str, object] | None = None,
        meta: Mapping[str, object] | None = None,
    ) -> VerificationInput:
        normalized_records = _normalize_records(records)
        if not normalized_records:
            raise ValueError("records must not be empty")

        _validate_record_scope(normalized_records)
        for record in normalized_records:
            validate_evaluation_meta(record.meta)
        eligible_records = [
            record
            for record in normalized_records
            if record.status in self.context.include_statuses
        ]
        resolved_events = event_definitions or self._resolve_event_definitions(
            eligible_records,
        )

        return VerificationInput(
            tree_mode=self.context.tree_mode,
            universal_dataset={record.case_id for record in eligible_records},
            events=self._build_events(eligible_records, resolved_events),
            assumptions=dict(assumptions or {}),
            meta=self._build_meta(normalized_records, eligible_records, meta),
        )

    def _resolve_event_definitions(
        self,
        records: Sequence[EvaluationRecord],
    ) -> dict[str, dict[str, object]]:
        case_kind = records[0].case_kind
        module_definitions = load_event_definitions(
            case_kind=case_kind,
            module_name=self.context.config_module,
        )
        if module_definitions:
            return module_definitions
        return _default_event_definitions(records)

    def _build_events(
        self,
        records: Sequence[EvaluationRecord],
        event_definitions: Mapping[str, Mapping[str, object]],
    ) -> dict[str, dict[str, object]]:
        events: dict[str, dict[str, object]] = {}

        for event_id, event_definition in event_definitions.items():
            dataset_filter = event_definition.get("dataset_filter")
            error_filter = event_definition.get("error_filter")
            dataset_d = {
                record.case_id
                for record in records
                if _resolve_condition(record, dataset_filter)
            }
            dataset_e = {
                record.case_id
                for record in records
                if record.case_id in dataset_d and _resolve_condition(record, error_filter)
            }
            events[event_id] = {
                "dataset_d": dataset_d,
                "dataset_e": dataset_e,
                "total_count": len(dataset_d),
                "error_count": len(dataset_e),
                "correct_count": len(dataset_d) - len(dataset_e),
                "target_column": event_definition.get("target_column", event_id),
            }

        return events

    def _build_meta(
        self,
        all_records: Sequence[EvaluationRecord],
        eligible_records: Sequence[EvaluationRecord],
        extra_meta: Mapping[str, object] | None,
    ) -> dict[str, object]:
        first_record = all_records[0]
        status_counts = Counter(record.status.value for record in all_records)
        meta = {
            "schema_version": first_record.meta["schema_version"],
            "source_module": self.context.source_module,
            "target": first_record.target,
            "case_kind": first_record.case_kind,
            "record_count": len(all_records),
            "eligible_record_count": len(eligible_records),
            "excluded_status_counts": dict(status_counts),
        }
        if "path_root" in first_record.meta:
            meta["path_root"] = first_record.meta["path_root"]
        if extra_meta:
            meta.update(extra_meta)
        return meta


def build_verification_input(
    records: EvaluationRecord | Sequence[EvaluationRecord],
    event_definitions: Mapping[str, Mapping[str, object]] | None = None,
    *,
    assumptions: Mapping[str, object] | None = None,
    meta: Mapping[str, object] | None = None,
    context: VerificationContext | None = None,
) -> VerificationInput:
    return VerificationInputBuilder(context=context).build(
        records,
        event_definitions,
        assumptions=assumptions,
        meta=meta,
    )


def _normalize_records(
    records: EvaluationRecord | Sequence[EvaluationRecord],
) -> list[EvaluationRecord]:
    if isinstance(records, EvaluationRecord):
        return [records]
    return list(records)


def _validate_record_scope(records: Sequence[EvaluationRecord]) -> None:
    targets = {record.target for record in records}
    case_kinds = {record.case_kind for record in records}
    if len(targets) != 1:
        raise ValueError(f"records must share the same target: {sorted(targets)}")
    if len(case_kinds) != 1:
        raise ValueError(f"records must share the same case_kind: {sorted(case_kinds)}")


def _default_event_definitions(
    records: Iterable[EvaluationRecord],
) -> dict[str, dict[str, object]]:
    labels: set[str] = set()
    for record in records:
        for key, value in record.output.items():
            if key.startswith("c_") and isinstance(value, int):
                labels.add(key)

    return {
        label: {
            "dataset_filter": None,
            "error_filter": f"output.{label}",
            "target_column": label,
        }
        for label in sorted(labels)
    }


def _resolve_condition(record: EvaluationRecord, condition: Condition) -> bool:
    if condition is None:
        return True
    if callable(condition):
        return bool(condition(record))
    return bool(_resolve_value(record, condition))


def _resolve_value(record: EvaluationRecord, condition_key: str) -> object:
    if "." in condition_key:
        root_name, nested_key = condition_key.split(".", 1)
        root = _resolve_root(record, root_name)
        if isinstance(root, Mapping):
            return root.get(nested_key)
        return None

    for mapping in (record.output, record.input, record.meta, record.evidence):
        if condition_key in mapping:
            return mapping[condition_key]

    if hasattr(record, condition_key):
        return getattr(record, condition_key)
    return None


def _resolve_root(record: EvaluationRecord, root_name: str) -> object:
    if root_name == "output":
        return record.output
    if root_name == "input":
        return record.input
    if root_name == "meta":
        return record.meta
    if root_name == "evidence":
        return record.evidence
    if hasattr(record, root_name):
        return getattr(record, root_name)
    return None
