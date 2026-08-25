from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contracts.evaluation import EvaluationRecord, validate_evaluation_meta
from contracts.execution import RunStatus
from contracts.verification import VerificationInput
from targets.bbsl.dataset_adapter import BBSLExperimentAdapter
from targets.bbsl.result_interpreter import ResultInterpreter


BASIC_EVENT_MAPPING = {
    "SALT_PEPPER": "salt_pepper",
    "OCCLUSION": "occlusion",
    "BLUR": "blur",
}

COMBINED_EVENT_MAPPING = {
    **BASIC_EVENT_MAPPING,
    "SP_OCC": "sp_occ",
    "SP_BLUR": "sp_blur",
    "OCC_BLUR": "occ_blur",
    "ALL_THREE": "all_three",
}


@dataclass(frozen=True)
class VerificationContext:
    tree_mode: str | None = None
    source_module: str = "targets.bbsl.verification_input"
    verification_core_dir: Path = (
        Path(__file__).resolve().parents[2] / "verification_core" / "ft4d" / "config"
    )


class VerificationInputBuilder:
    def __init__(
        self,
        context: VerificationContext | None = None,
        adapter: BBSLExperimentAdapter | None = None,
        interpreter: ResultInterpreter | None = None,
    ):
        self.context = context or VerificationContext()
        self.adapter = adapter or BBSLExperimentAdapter()
        self.interpreter = interpreter or ResultInterpreter(adapter=self.adapter)

    def build(
        self,
        source: EvaluationRecord | Mapping[str, Any],
        *,
        tree_mode: str | None = None,
        assumptions: Mapping[str, object] | None = None,
        meta: Mapping[str, object] | None = None,
    ) -> VerificationInput:
        record, payload = self._normalize_source(source)
        self._validate_record(record)

        resolved_tree_mode = self._resolve_tree_mode(record, tree_mode)
        mapping = _event_mapping(resolved_tree_mode)
        condition_results = self._extract_condition_results(record)
        missing_conditions = [
            condition_name
            for condition_name in mapping.values()
            if condition_name not in condition_results
        ]
        if missing_conditions:
            raise ValueError(
                "missing condition_results for tree_mode "
                f"{resolved_tree_mode!r}: {missing_conditions}"
            )

        input_assumptions = dict(assumptions or {})
        input_assumptions.setdefault(
            "sigma_pf_source",
            record.input.get("sigma_pf_source", "dataset"),
        )
        input_assumptions.setdefault(
            "sigma_pf_assumptions",
            record.input.get("sigma_pf_assumptions", {}),
        )
        input_assumptions.setdefault(
            "sigma_pb_mode",
            record.input.get("sigma_pb_mode", "raw"),
        )
        input_assumptions.setdefault(
            "and_rule",
            record.input.get("and_rule", "min"),
        )
        input_assumptions.setdefault(
            "tree_path",
            str(self._tree_path_for_mode(resolved_tree_mode)),
        )
        statistical_test_config = self._extract_statistical_test_config(record, payload)
        if statistical_test_config:
            input_assumptions.setdefault(
                "statistical_test_config",
                statistical_test_config,
            )

        result_meta = {
            "schema_version": record.meta["schema_version"],
            "source_module": self.context.source_module,
            "target": record.target,
            "case_kind": record.case_kind,
            "tree_mode": resolved_tree_mode,
            "record_count": 1,
        }
        if "path_root" in record.meta:
            result_meta["path_root"] = record.meta["path_root"]
        if meta:
            result_meta.update(meta)

        return VerificationInput(
            tree_mode=resolved_tree_mode,
            universal_dataset=set(record.output.get("universal_dataset", [])),
            events=self._build_events(condition_results, mapping),
            assumptions=input_assumptions,
            meta=result_meta,
        )

    def _normalize_source(
        self,
        source: EvaluationRecord | Mapping[str, Any],
    ) -> tuple[EvaluationRecord, Mapping[str, Any] | None]:
        if isinstance(source, EvaluationRecord):
            return source, None
        if not isinstance(source, Mapping):
            raise TypeError("source must be EvaluationRecord or mapping")
        record = self.interpreter.interpret_payload(source)
        return record, source

    def _validate_record(self, record: EvaluationRecord) -> None:
        if record.target != "bbsl":
            raise ValueError(f"record.target must be 'bbsl', got {record.target!r}")
        if record.status != RunStatus.SUCCESS:
            raise ValueError(
                "VerificationInput requires a successful EvaluationRecord, "
                f"got {record.status.value!r}"
            )
        validate_evaluation_meta(record.meta)

    def _resolve_tree_mode(
        self,
        record: EvaluationRecord,
        tree_mode_override: str | None,
    ) -> str:
        candidate = (
            tree_mode_override
            or self.context.tree_mode
            or str(record.input.get("tree_mode", "")).strip()
            or str(record.meta.get("tree_mode", "")).strip()
            or "basic"
        )
        normalized = candidate.lower()
        if normalized == "all":
            raise ValueError("tree_mode 'all' is not supported in VerificationInput")
        if normalized not in {"basic", "combined"}:
            raise ValueError(f"unsupported tree_mode: {candidate!r}")
        return normalized

    def _extract_condition_results(
        self,
        record: EvaluationRecord,
    ) -> dict[str, dict[str, object]]:
        payload = record.output.get("condition_results")
        if not isinstance(payload, Mapping):
            raise ValueError("record.output['condition_results'] must be a mapping")
        result: dict[str, dict[str, object]] = {}
        for condition_name, metrics in payload.items():
            if not isinstance(metrics, Mapping):
                raise ValueError(
                    f"condition_results[{condition_name!r}] must be a mapping"
                )
            result[str(condition_name)] = dict(metrics)
        return result

    def _extract_statistical_test_config(
        self,
        record: EvaluationRecord,
        raw_payload: Mapping[str, Any] | None,
    ) -> dict[str, object]:
        if raw_payload is not None:
            payload = raw_payload.get("statistical_test_config", {})
            if isinstance(payload, Mapping):
                return dict(payload)

        value = record.input.get("statistical_test_config")
        if isinstance(value, Mapping):
            return dict(value)

        meta_value = record.meta.get("statistical_test_config")
        if isinstance(meta_value, Mapping):
            return dict(meta_value)
        return {}

    def _build_events(
        self,
        condition_results: Mapping[str, Mapping[str, object]],
        mapping: Mapping[str, str],
    ) -> dict[str, dict[str, object]]:
        events: dict[str, dict[str, object]] = {}
        for event_id, condition_name in mapping.items():
            metrics = condition_results[condition_name]
            events[event_id] = {
                "dataset_d": set(metrics.get("dataset_d", [])),
                "dataset_e": set(metrics.get("dataset_e", [])),
                "total_count": int(metrics.get("total_count", 0) or 0),
                "correct_count": int(metrics.get("correct_count", 0) or 0),
                "error_count": int(metrics.get("error_count", 0) or 0),
                "sigma_pb": float(metrics.get("sigma_pb", 0.0) or 0.0),
                "sigma_pb_mode": metrics.get("sigma_pb_mode", "raw"),
                "recognition_test": metrics.get("recognition_test"),
            }
        return events

    def _tree_path_for_mode(self, tree_mode: str) -> Path:
        filename = "tree_basic.json" if tree_mode == "basic" else "tree_bbsl.json"
        return (self.context.verification_core_dir / filename).resolve()


def build_verification_input(
    source: EvaluationRecord | Mapping[str, Any],
    *,
    tree_mode: str | None = None,
    assumptions: Mapping[str, object] | None = None,
    meta: Mapping[str, object] | None = None,
    context: VerificationContext | None = None,
) -> VerificationInput:
    return VerificationInputBuilder(context=context).build(
        source,
        tree_mode=tree_mode,
        assumptions=assumptions,
        meta=meta,
    )


def build_verification_input_from_path(
    output_json_path: str | Path,
    *,
    tree_mode: str | None = None,
    assumptions: Mapping[str, object] | None = None,
    meta: Mapping[str, object] | None = None,
    context: VerificationContext | None = None,
) -> VerificationInput:
    path = Path(output_json_path).expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return build_verification_input(
        payload,
        tree_mode=tree_mode,
        assumptions=assumptions,
        meta=meta,
        context=context,
    )


def _event_mapping(tree_mode: str) -> dict[str, str]:
    if tree_mode == "basic":
        return BASIC_EVENT_MAPPING
    if tree_mode == "combined":
        return COMBINED_EVENT_MAPPING
    raise ValueError(f"unsupported tree_mode: {tree_mode!r}")
