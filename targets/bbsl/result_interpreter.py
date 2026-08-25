from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from contracts.evaluation import EvaluationRecord, ensure_evaluation_meta
from contracts.execution import RunStatus
from targets.bbsl.dataset_adapter import BBSLExperimentAdapter


REQUIRED_TOP_LEVEL_KEYS = {
    "active_conditions",
    "bbsl_results",
    "tree_mode",
    "statistical_test_config",
}

EXPERIMENT_TYPE_TO_CASE_KIND = {
    "full_all": "full_all",
    "全画像 × 4条件（同一画像）": "full_all",
}


@dataclass(frozen=True)
class InterpretationContext:
    target: str = "bbsl"
    verifier_name: str = "bbsl"
    source_module: str = "targets.bbsl.result_interpreter"
    schema_version: int = 1
    default_case_kind: str = "full_all"


class ResultInterpreter:
    def __init__(
        self,
        context: InterpretationContext | None = None,
        adapter: BBSLExperimentAdapter | None = None,
    ):
        self.context = context or InterpretationContext()
        self.adapter = adapter or BBSLExperimentAdapter()

    def interpret_fixture(self, fixture_path: str | Path) -> EvaluationRecord:
        return self.interpret_path(fixture_path)

    def interpret_raw_run_result(self, raw_run_result) -> EvaluationRecord:
        raw_result_json = raw_run_result.evidence.get("raw_result_json")
        if not raw_result_json:
            return EvaluationRecord(
                case_id=raw_run_result.case_id,
                target=raw_run_result.target,
                case_kind=raw_run_result.case_kind,
                status=RunStatus.ANALYSIS_ERROR,
                meta=ensure_evaluation_meta(
                    {
                        "verifier_name": self.context.verifier_name,
                        "error_message": "missing_raw_result_json",
                        "raw_run_status": raw_run_result.status.value,
                        "raw_run_meta": dict(raw_run_result.meta),
                    },
                    source_module=self.context.source_module,
                    schema_version=self.context.schema_version,
                    created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                ),
            )

        record = self.interpret_path(raw_result_json)
        record.case_id = raw_run_result.case_id
        record.target = raw_run_result.target
        record.case_kind = raw_run_result.case_kind
        record.meta.setdefault("raw_run_status", raw_run_result.status.value)
        record.meta.setdefault("raw_run_meta", dict(raw_run_result.meta))
        return record

    def interpret_path(self, fixture_path: str | Path) -> EvaluationRecord:
        path = Path(fixture_path).expanduser().resolve()
        case_id = path.stem
        common_meta = self._base_meta(path)

        try:
            payload = self.adapter.load_output(str(path))
        except OSError as exc:
            return self._error_record(
                case_id=case_id,
                case_kind=self._resolve_case_kind({}, path),
                evidence_path=path,
                status=RunStatus.EXECUTION_ERROR,
                common_meta=common_meta,
                error_message=str(exc),
            )
        except ValueError as exc:
            return self._error_record(
                case_id=case_id,
                case_kind=self._resolve_case_kind({}, path),
                evidence_path=path,
                status=RunStatus.ANALYSIS_ERROR,
                common_meta=common_meta,
                error_message=f"invalid_json:{exc}",
            )

        return self.interpret_payload(payload, case_id=case_id, evidence_path=path)

    def interpret_payload(
        self,
        payload: Mapping[str, Any],
        *,
        case_id: str = "bbsl_payload",
        evidence_path: str | Path | None = None,
    ) -> EvaluationRecord:
        resolved_path = None
        if evidence_path is not None:
            resolved_path = Path(evidence_path).expanduser().resolve()
        common_meta = self._base_meta(resolved_path)
        case_kind = self._resolve_case_kind(payload, resolved_path)

        validation_error = self._validate_payload(payload)
        if validation_error is not None:
            status, reason = validation_error
            return self._error_record(
                case_id=case_id,
                case_kind=case_kind,
                evidence_path=resolved_path,
                status=status,
                common_meta=common_meta,
                error_message=reason,
            )

        condition_results = self._normalize_condition_results(payload)
        if not condition_results:
            return self._error_record(
                case_id=case_id,
                case_kind=case_kind,
                evidence_path=resolved_path,
                status=RunStatus.INVALID,
                common_meta=common_meta,
                error_message="empty_condition_results",
            )

        universal_dataset = list(payload.get("universal_dataset", []))
        total_count = sum(
            int(summary["total_count"]) for summary in condition_results.values()
        )
        if len(universal_dataset) == 0 and total_count == 0:
            return self._error_record(
                case_id=case_id,
                case_kind=case_kind,
                evidence_path=resolved_path,
                status=RunStatus.INVALID,
                common_meta=common_meta,
                error_message="empty_universal_dataset",
            )

        output = self._build_output(payload, condition_results, universal_dataset)
        return EvaluationRecord(
            case_id=case_id,
            target=self.context.target,
            case_kind=case_kind,
            status=RunStatus.SUCCESS,
            input=self._build_input(payload),
            output=output,
            evidence=self._build_evidence(resolved_path),
            meta=self._extend_meta(common_meta, payload),
        )

    def _validate_payload(
        self,
        payload: Mapping[str, Any],
    ) -> tuple[RunStatus, str] | None:
        if not isinstance(payload, Mapping):
            return RunStatus.ANALYSIS_ERROR, "payload_not_mapping"

        missing_keys = sorted(REQUIRED_TOP_LEVEL_KEYS - set(payload.keys()))
        if missing_keys:
            return (
                RunStatus.ANALYSIS_ERROR,
                f"missing_top_level_keys:{','.join(missing_keys)}",
            )

        active_conditions = payload.get("active_conditions")
        if not isinstance(active_conditions, list):
            return RunStatus.ANALYSIS_ERROR, "active_conditions_not_list"

        bbsl_results = payload.get("bbsl_results")
        if not isinstance(bbsl_results, Mapping):
            return RunStatus.ANALYSIS_ERROR, "bbsl_results_not_mapping"

        statistical_test_config = payload.get("statistical_test_config")
        if not isinstance(statistical_test_config, Mapping):
            return RunStatus.ANALYSIS_ERROR, "statistical_test_config_not_mapping"

        if not active_conditions and not bbsl_results:
            return RunStatus.INVALID, "no_active_conditions"

        if self._is_effectively_empty(payload):
            return RunStatus.INVALID, "empty_bbsl_results"

        return None

    def _is_effectively_empty(self, payload: Mapping[str, Any]) -> bool:
        universal_dataset_size = int(payload.get("universal_dataset_size", 0) or 0)
        if universal_dataset_size > 0:
            return False

        bbsl_results = payload.get("bbsl_results", {})
        if not isinstance(bbsl_results, Mapping):
            return False

        for metrics in bbsl_results.values():
            if isinstance(metrics, Mapping):
                dataset_size = int(metrics.get("dataset_size", 0) or 0)
                effective_size = int(metrics.get("dataset_size_effective", 0) or 0)
                if dataset_size > 0 or effective_size > 0:
                    return False
            elif metrics:
                return False

        return True

    def _build_input(self, payload: Mapping[str, Any]) -> dict[str, object]:
        return {
            "tree_mode": payload.get("tree_mode"),
            "active_conditions": list(self.adapter.active_conditions(dict(payload))),
            "sigma_pf_source": payload.get("sigma_pf_source", "dataset"),
            "sigma_pb_mode": payload.get("sigma_pb_mode", "raw"),
            "and_rule": payload.get("and_rule", "min"),
            "detect_timeout": payload.get("detect_timeout"),
            "max_images": payload.get("max_images"),
            "mini_mode": payload.get("mini_mode"),
            "sigma_pf_assumptions": dict(payload.get("sigma_pf_assumptions", {})),
        }

    def _build_output(
        self,
        payload: Mapping[str, Any],
        condition_results: dict[str, dict[str, object]],
        universal_dataset: list[str],
    ) -> dict[str, object]:
        total_count = sum(
            int(summary["total_count"]) for summary in condition_results.values()
        )
        correct_count = sum(
            int(summary["correct_count"]) for summary in condition_results.values()
        )
        error_count = sum(
            int(summary["error_count"]) for summary in condition_results.values()
        )
        return {
            "universal_dataset_size": int(
                payload.get("universal_dataset_size", len(universal_dataset))
            ),
            "universal_dataset": universal_dataset,
            "active_condition_count": len(self.adapter.active_conditions(dict(payload))),
            "condition_result_count": len(condition_results),
            "total_count": total_count,
            "correct_count": correct_count,
            "error_count": error_count,
            "condition_results": condition_results,
        }

    def _normalize_condition_results(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, dict[str, object]]:
        sigma_pb_mode = str(payload.get("sigma_pb_mode", "raw"))
        bbsl_results = payload.get("bbsl_results", {})
        condition_datasets = payload.get("condition_datasets", {})
        condition_results: dict[str, dict[str, object]] = {}

        for condition_name in self.adapter.active_conditions(dict(payload)):
            metrics = bbsl_results.get(condition_name)
            if metrics is None:
                continue
            if not isinstance(metrics, Mapping):
                if metrics == []:
                    continue
                raise ValueError(
                    f"bbsl_results[{condition_name!r}] must be a mapping or empty list"
                )

            dataset_payload = condition_datasets.get(condition_name, {})
            if dataset_payload is None:
                dataset_payload = {}
            if not isinstance(dataset_payload, Mapping):
                dataset_payload = {}

            normalized = self._normalize_single_condition(
                metrics,
                dataset_payload,
                sigma_pb_mode=sigma_pb_mode,
            )
            condition_results[condition_name] = normalized

        return condition_results

    def _normalize_single_condition(
        self,
        metrics: Mapping[str, Any],
        dataset_payload: Mapping[str, Any],
        *,
        sigma_pb_mode: str,
    ) -> dict[str, object]:
        use_effective = (
            sigma_pb_mode == "delta-clean"
            and (
                "dataset_d_effective" in metrics
                or "dataset_e_effective" in metrics
                or "dataset_size_effective" in metrics
            )
        )

        if use_effective:
            dataset_d = sorted(set(metrics.get("dataset_d_effective", [])))
            dataset_e = sorted(set(metrics.get("dataset_e_effective", [])))
            total_count = int(metrics.get("dataset_size_effective", len(dataset_d)) or 0)
            correct_count = int(
                metrics.get("T_effective", total_count - len(dataset_e)) or 0
            )
            error_count = int(metrics.get("F_effective", len(dataset_e)) or 0)
        else:
            dataset_d = sorted(
                set(dataset_payload.get("dataset_d", metrics.get("dataset_d", [])))
            )
            dataset_e = sorted(
                set(dataset_payload.get("dataset_e", metrics.get("dataset_e", [])))
            )
            total_count = int(metrics.get("dataset_size", len(dataset_d)) or 0)
            correct_count = int(
                metrics.get(
                    "T",
                    dataset_payload.get("correct_count", total_count - len(dataset_e)),
                )
                or 0
            )
            error_count = int(
                metrics.get("F", dataset_payload.get("error_count", len(dataset_e))) or 0
            )

        return {
            "dataset_d": dataset_d,
            "dataset_e": dataset_e,
            "total_count": total_count,
            "correct_count": correct_count,
            "error_count": error_count,
            "sigma_pb": float(metrics.get("sigma_pb", 0.0) or 0.0),
            "sigma_pb_mode": sigma_pb_mode,
            "recognition_test": metrics.get("statistical_test"),
        }

    def _resolve_case_kind(
        self,
        payload: Mapping[str, Any],
        evidence_path: Path | None,
    ) -> str:
        mode = payload.get("mode")
        if isinstance(mode, str) and mode.strip():
            return _to_snake_case(mode)

        experiment_type = payload.get("experiment_type")
        if isinstance(experiment_type, str) and experiment_type.strip():
            mapped = EXPERIMENT_TYPE_TO_CASE_KIND.get(experiment_type.strip())
            if mapped is not None:
                return mapped

        if evidence_path is not None:
            stem = evidence_path.stem
            if stem.startswith("clean_baseline"):
                return "clean_baseline"
            if stem.startswith("noisy_batch_"):
                return "noisy_batch"
            if stem.startswith("experiment_all"):
                return "full_all"

        return self.context.default_case_kind

    def _base_meta(self, evidence_path: Path | None) -> dict[str, object]:
        meta = ensure_evaluation_meta(
            {
                "verifier_name": self.context.verifier_name,
            },
            source_module=self.context.source_module,
            schema_version=self.context.schema_version,
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        if evidence_path is not None:
            meta["path_root"] = str(evidence_path.parent)
        return meta

    def _extend_meta(
        self,
        common_meta: Mapping[str, object],
        payload: Mapping[str, Any],
    ) -> dict[str, object]:
        meta = dict(common_meta)
        meta.update(
            {
                "tree_mode": payload.get("tree_mode"),
                "sigma_pf_source": payload.get("sigma_pf_source", "dataset"),
                "sigma_pb_mode": payload.get("sigma_pb_mode", "raw"),
                "and_rule": payload.get("and_rule", "min"),
                "experiment_type": payload.get("experiment_type"),
                "mode": payload.get("mode"),
                "payload_timestamp": payload.get("timestamp"),
            }
        )
        return meta

    def _build_evidence(self, evidence_path: Path | None) -> dict[str, str]:
        if evidence_path is None:
            return {}
        return {"raw_result_json": str(evidence_path)}

    def _error_record(
        self,
        *,
        case_id: str,
        case_kind: str,
        evidence_path: Path | None,
        status: RunStatus,
        common_meta: Mapping[str, object],
        error_message: str,
    ) -> EvaluationRecord:
        meta = dict(common_meta)
        meta["analysis_pipeline"] = ["fixture_decode", "bbsl_result_normalization"]
        meta["error_message"] = error_message
        return EvaluationRecord(
            case_id=case_id,
            target=self.context.target,
            case_kind=case_kind,
            status=status,
            evidence=self._build_evidence(evidence_path),
            meta=meta,
        )


def interpret_fixture(fixture_path: str | Path) -> EvaluationRecord:
    return ResultInterpreter().interpret_fixture(fixture_path)


def interpret_path(fixture_path: str | Path) -> EvaluationRecord:
    return ResultInterpreter().interpret_path(fixture_path)


def _to_snake_case(value: str) -> str:
    return value.strip().replace("-", "_").replace(" ", "_").lower()
