from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

from contracts.evaluation import EvaluationRecord, ensure_evaluation_meta
from contracts.execution import RunStatus
from verifiers.maude.prism_checker import PrismMaudeChecker
from verifiers.maude.prism_trace_evaluator import PrismTraceEvaluator


@dataclass(frozen=True)
class InterpretationContext:
    target: str = "prism"
    verifier_name: str = "maude_prism_trace"
    source_module: str = "targets.prism.result_interpreter"
    schema_version: int = 1


class PrismResultInterpreter:
    def __init__(
        self,
        context: InterpretationContext | None = None,
        *,
        checker: PrismMaudeChecker | None = None,
        trace_evaluator: PrismTraceEvaluator | None = None,
    ):
        self.context = context or InterpretationContext()
        self.checker = checker or PrismMaudeChecker()
        self.trace_evaluator = trace_evaluator or PrismTraceEvaluator()

    def interpret_raw_run_result(self, raw_run_result) -> EvaluationRecord:
        raw_path = raw_run_result.evidence.get("raw_result_json")
        if not raw_path:
            return self._error(raw_run_result, "missing_raw_result_json")
        try:
            payload = json.loads(Path(raw_path).read_text(encoding="utf-8"))
            return self.interpret_payload(payload, case_id=raw_run_result.case_id, raw_path=raw_path)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            return self._error(raw_run_result, str(exc))

    def interpret_payload(self, payload: Mapping[str, object], *, case_id: str = "prism", raw_path: str | Path | None = None) -> EvaluationRecord:
        record_kind = str(payload.get("record_kind", "sample"))
        required = {"model", "constants", "horizon"}
        if record_kind == "exact_model_check":
            required.add("property_results")
        elif record_kind == "sample":
            required.add("trace_summary")
        else:
            raise ValueError(f"unsupported PRISM record_kind: {record_kind!r}")
        missing = sorted(required - set(payload))
        if missing:
            raise ValueError("missing PRISM result fields: " + ",".join(missing))
        constants = payload["constants"]
        if not isinstance(constants, Mapping):
            raise ValueError("PRISM raw result has invalid field types")
        maude: dict[str, object] | None = None
        if record_kind == "exact_model_check":
            property_results = payload["property_results"]
            if not isinstance(property_results, Mapping):
                raise ValueError("PRISM property results must be a mapping")
            output = {
                "prism_eventual_failure_probability": float(
                    property_results["eventual_failure"]
                ),
                "prism_bounded_failure_probability": float(
                    property_results["bounded_failure"]
                ),
            }
        else:
            trace_summary = payload["trace_summary"]
            if not isinstance(trace_summary, Mapping):
                raise ValueError("PRISM trace summary must be a mapping")
            checker_method = getattr(self.checker, "check", None)
            if callable(checker_method):
                checker_result = checker_method(trace_summary)
                maude = self.trace_evaluator.evaluate(checker_result)
            else:
                # Compatibility with injected pre-Phase-7 checker doubles.
                maude = self.checker.evaluate(trace_summary)
            output = {
                "c_failure": int(trace_summary["failure_reached"]),
                "steps_to_failure_capped": int(
                    trace_summary["steps_to_failure_capped"]
                ),
                "degraded_visit_count": int(trace_summary["degraded_visit_count"]),
                "absorbing_state_valid": int(trace_summary["absorbing_state_valid"]),
                **maude,
            }
        evidence = {} if raw_path is None else {"raw_result_json": str(Path(raw_path).resolve())}
        return EvaluationRecord(
            case_id=case_id,
            target=self.context.target,
            case_kind=str(payload["model"]),
            status=RunStatus.SUCCESS,
            input={"model": str(payload["model"]), "steps": int(payload["horizon"]), **{str(k).lower(): float(v) for k, v in constants.items()}},
            output=output,
            evidence=evidence,
            meta=ensure_evaluation_meta(
                {
                    "verifier_name": (
                        "prism_exact_model_checker"
                        if record_kind == "exact_model_check"
                        else self.context.verifier_name
                    ),
                    "record_kind": record_kind,
                    **(
                        {"maude_verdicts": maude["maude_verdicts"]}
                        if maude is not None
                        else {}
                    ),
                    "trace_csv": payload.get("trace_csv"),
                },
                source_module=self.context.source_module,
                schema_version=self.context.schema_version,
                created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            ),
        )

    def _error(self, raw, message: str) -> EvaluationRecord:
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.ANALYSIS_ERROR if raw.status == RunStatus.SUCCESS else raw.status,
            evidence=dict(raw.evidence),
            meta=ensure_evaluation_meta({"error_message": message, "raw_run_status": raw.status.value}, source_module=self.context.source_module, schema_version=self.context.schema_version),
        )
