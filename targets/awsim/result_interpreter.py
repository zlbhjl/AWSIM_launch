from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from contracts.evaluation import EvaluationRecord, ensure_evaluation_meta
from contracts.execution import RawRunResult, RunStatus
from targets.awsim.case_kinds import load_rule_spec, resolve_case_kind_module
from targets.awsim.kinematics_bridge import extract_kinematics_metrics
from verifiers.maude.backend import MaudeRunResult, run_checker
from verifiers.maude.evaluator import FormulaSpec, evaluate_formula_results


# Continuous metrics that the legacy awchecker extracted with AWKinematicsPipeline.
KINEMATICS_OUTPUT_KEYS = ("min_ttc", "min_distance", "min_ttb", "z_margin")
REQUIRED_TOP_LEVEL_KEYS = {
    "groundtruth_size",
    "groundtruth_kinematic",
    "perception_objects",
    "ego_estimated_kinematic",
    "control_cmds",
    "planning_trajectory",
    "metadata",
}


@dataclass(frozen=True)
class InterpretationContext:
    target: str = "awsim"
    case_kind: str = "uturn"
    verifier_name: str = "maude"
    source_module: str = "targets.awsim.result_interpreter"
    schema_version: int = 1
    config_module: str = "targets.awsim.case_kinds.uturn"
    kinematics_mode: str = "cvm"


class ResultInterpreter:
    def __init__(
        self,
        context: InterpretationContext | None = None,
        maude_runner: Callable[[Path, Sequence[str]], MaudeRunResult] | None = None,
        kinematics_extractor: Callable[[Path], Mapping[str, object]] | None = None,
    ):
        self.context = context or InterpretationContext()
        self.maude_runner = maude_runner or self._default_maude_runner
        self.kinematics_extractor = kinematics_extractor or self._default_kinematics_extractor
        self.formula_specs, self.invalid_conditions = self._load_rule_spec()

    def interpret_fixture(self, fixture_path: str | Path) -> EvaluationRecord:
        return self.interpret_path(fixture_path)

    def interpret_raw_run_result(self, raw_run_result: RawRunResult) -> EvaluationRecord:
        trace_json = raw_run_result.evidence.get("trace_json")
        if not trace_json:
            final_status = (
                raw_run_result.status
                if raw_run_result.status is not RunStatus.SUCCESS
                else RunStatus.ANALYSIS_ERROR
            )
            meta = {
                "execution_status": raw_run_result.status.value,
                "analysis_status": RunStatus.ANALYSIS_ERROR.value,
                "verifier_name": self.context.verifier_name,
                "error_message": "missing_trace_json",
                "raw_run_status": raw_run_result.status.value,
                "raw_run_meta": dict(raw_run_result.meta),
            }
            if raw_run_result.status is RunStatus.TIMEOUT:
                meta["timeout_reason"] = self._resolve_timeout_reason(raw_run_result)
            return EvaluationRecord(
                case_id=raw_run_result.case_id,
                target=raw_run_result.target,
                case_kind=raw_run_result.case_kind,
                status=final_status,
                meta=ensure_evaluation_meta(
                    meta,
                    source_module=self.context.source_module,
                    schema_version=self.context.schema_version,
                    created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                ),
            )

        record = self.interpret_path(trace_json)
        record.case_id = raw_run_result.case_id
        record.target = raw_run_result.target
        record.case_kind = raw_run_result.case_kind
        merged_evidence = dict(raw_run_result.evidence)
        merged_evidence.update(record.evidence)
        record.evidence = merged_evidence
        record.meta.setdefault("raw_run_status", raw_run_result.status.value)
        record.meta.setdefault("raw_run_meta", dict(raw_run_result.meta))
        record.meta.setdefault("execution_status", raw_run_result.status.value)
        record.meta.setdefault("analysis_status", record.status.value)
        if raw_run_result.status is RunStatus.TIMEOUT:
            record.meta.setdefault(
                "raw_timeout_reason",
                self._resolve_timeout_reason(raw_run_result),
            )
        return record

    @staticmethod
    def _resolve_timeout_reason(raw_run_result: RawRunResult) -> str:
        """Keep artifact arrival separate from the scenario completion result."""
        artifact_timing = str(raw_run_result.meta.get("artifact_timing", ""))
        if artifact_timing == "missing":
            return "artifact_timeout"
        if artifact_timing == "late":
            return "late_artifact"
        if raw_run_result.meta.get("returncode") == 124:
            return "scenario_goal_timeout"
        return "timeout_unknown"

    def interpret_path(self, fixture_path: str | Path) -> EvaluationRecord:
        path = Path(fixture_path).expanduser().resolve()
        case_id = path.stem
        common_meta = self._base_meta(path)

        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            return self._error_record(
                case_id=case_id,
                evidence_path=path,
                status=RunStatus.EXECUTION_ERROR,
                common_meta=common_meta,
                error_message=str(exc),
            )

        if content == "TIMEOUT":
            timeout_output = {
                "status_reason": "timeout_marker",
                "min_ttc": -1,
                "min_distance": -1,
                "min_ttb": -1,
                "z_margin": -1,
            }
            for spec in self.formula_specs:
                timeout_output.setdefault(spec.header, -1)
            return self._record(
                case_id=case_id,
                status=RunStatus.TIMEOUT,
                evidence_path=path,
                common_meta=common_meta,
                output=timeout_output,
            )

        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            return self._error_record(
                case_id=case_id,
                evidence_path=path,
                status=RunStatus.ANALYSIS_ERROR,
                common_meta=common_meta,
                error_message=f"invalid_json:{exc.msg}",
            )

        validation_error = self._validate_payload(payload)
        if validation_error is not None:
            status, reason = validation_error
            return self._error_record(
                case_id=case_id,
                evidence_path=path,
                status=status,
                common_meta=common_meta,
                error_message=reason,
            )

        # A recorder subscribed to a topic that no longer exists writes an empty list
        # silently (e.g. planning_trajectory on Autoware 1.9.0), so surface it per record.
        empty_trace_keys = _empty_trace_keys(payload)
        if empty_trace_keys:
            common_meta["empty_trace_keys"] = ",".join(empty_trace_keys)

        output = {
            "groundtruth_kinematic_count": len(payload["groundtruth_kinematic"]),
            "perception_objects_count": len(payload["perception_objects"]),
            "boundingbox_perception_objects_count": len(payload.get("boundingbox_perception_objects", [])),
            "vehicle_sizes_count": len(payload["groundtruth_size"]["vehicle_sizes"]),
        }
        # Added before Maude so the collision rule can still force min_distance to 0.
        kinematics_output, kinematics_error = self._extract_kinematics(path)
        output.update(kinematics_output)
        if kinematics_error is not None:
            common_meta["kinematics_error"] = kinematics_error

        try:
            maude_result = self.maude_runner(
                path,
                [spec.formula for spec in self.formula_specs],
            )
        except OSError as exc:
            return self._error_record(
                case_id=case_id,
                evidence_path=path,
                status=RunStatus.EXECUTION_ERROR,
                common_meta=common_meta,
                error_message=f"maude_execution_failed:{exc}",
                output=_invalidate_kinematics(output),
                extra_meta={"analysis_pipeline": ["fixture_decode", "structural_validation", "maude_backend"]},
            )

        if maude_result.returncode != 0:
            return self._error_record(
                case_id=case_id,
                evidence_path=path,
                status=RunStatus.ANALYSIS_ERROR,
                common_meta=common_meta,
                error_message=f"maude_returncode:{maude_result.returncode}",
                output=_invalidate_kinematics(output),
                extra_meta={
                    "analysis_pipeline": ["fixture_decode", "structural_validation", "maude_backend"],
                    "maude_stderr": maude_result.stderr,
                },
            )

        summary = evaluate_formula_results(
            maude_result.stdout,
            self.formula_specs,
            base_output=output,
            invalid_conditions=self.invalid_conditions,
        )
        output = dict(summary.output)

        if summary.has_error:
            return self._error_record(
                case_id=case_id,
                evidence_path=path,
                status=RunStatus.ANALYSIS_ERROR,
                common_meta=common_meta,
                error_message="maude_evaluation_error",
                output=_invalidate_kinematics(output),
                extra_meta={
                    "analysis_pipeline": [
                        "fixture_decode",
                        "structural_validation",
                        "maude_backend",
                        "maude_evaluator",
                    ],
                    "missing_headers": summary.missing_headers,
                    "invalid_headers": summary.invalid_headers,
                },
            )

        return self._record(
            case_id=case_id,
            status=RunStatus.SUCCESS,
            evidence_path=path,
            common_meta=common_meta,
            output=output,
            extra_meta={
                "analysis_pipeline": [
                    "fixture_decode",
                    "structural_validation",
                    "maude_backend",
                    "maude_evaluator",
                ]
            },
        )

    def _validate_payload(self, payload: Any) -> tuple[RunStatus, str] | None:
        if not isinstance(payload, dict):
            return RunStatus.ANALYSIS_ERROR, "payload_not_dict"

        missing_keys = sorted(REQUIRED_TOP_LEVEL_KEYS - set(payload.keys()))
        if missing_keys:
            return RunStatus.ANALYSIS_ERROR, f"missing_top_level_keys:{','.join(missing_keys)}"

        groundtruth_size = payload.get("groundtruth_size")
        if not isinstance(groundtruth_size, dict):
            return RunStatus.ANALYSIS_ERROR, "groundtruth_size_not_dict"

        if "vehicle_sizes" not in groundtruth_size:
            return RunStatus.ANALYSIS_ERROR, "missing_vehicle_sizes"

        groundtruth_kinematic = payload.get("groundtruth_kinematic")
        if not isinstance(groundtruth_kinematic, list):
            return RunStatus.ANALYSIS_ERROR, "groundtruth_kinematic_not_list"

        if len(groundtruth_kinematic) == 0:
            return RunStatus.INVALID, "empty_groundtruth_kinematic"

        return None

    def _base_meta(self, evidence_path: Path) -> dict[str, object]:
        return ensure_evaluation_meta(
            {
                "verifier_name": self.context.verifier_name,
                "path_root": str(evidence_path.parent),
            },
            source_module=self.context.source_module,
            schema_version=self.context.schema_version,
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )

    def _load_rule_spec(self) -> tuple[list[FormulaSpec], dict[str, object]]:
        result_labels, formulas, invalid_conditions = load_rule_spec(
            case_kind=self.context.case_kind,
            module_name=self.context.config_module,
        )

        if not formulas:
            raise ValueError(f"No FORMULAS found in {self.context.config_module}")

        specs: list[FormulaSpec] = []
        for index, formula in enumerate(formulas):
            header = (
                result_labels[index]
                if index < len(result_labels)
                else f"formula_{index + 1}"
            )
            specs.append(FormulaSpec(formula=formula, header=header))
        return specs, invalid_conditions

    def _extract_kinematics(self, path: Path) -> tuple[dict[str, object], str | None]:
        try:
            metrics = self.kinematics_extractor(path)
        except Exception as exc:  # noqa: BLE001 - a failed extraction must not drop the Maude result
            return {key: "" for key in KINEMATICS_OUTPUT_KEYS}, f"{type(exc).__name__}: {exc}"
        return {key: metrics.get(key, "") for key in KINEMATICS_OUTPUT_KEYS}, None

    def _default_kinematics_extractor(self, path: Path) -> Mapping[str, object]:
        return extract_kinematics_metrics(
            path,
            mode=self.context.kinematics_mode,
            target_npcs=self._target_npcs(),
        )

    def _target_npcs(self) -> list[str]:
        if not hasattr(self, "_cached_target_npcs"):
            module = resolve_case_kind_module(
                case_kind=self.context.case_kind,
                module_name=self.context.config_module,
            )
            self._cached_target_npcs = list(getattr(module, "TARGET_NPCS", ["npc1"]))
        return self._cached_target_npcs

    def _default_maude_runner(
        self,
        evidence_path: Path,
        formulas: Sequence[str],
    ) -> MaudeRunResult:
        return run_checker(evidence_path, formulas=formulas)

    def _record(
        self,
        *,
        case_id: str,
        status: RunStatus,
        evidence_path: Path,
        common_meta: dict[str, object],
        output: dict[str, object] | None = None,
        extra_meta: Mapping[str, object] | None = None,
    ) -> EvaluationRecord:
        meta = dict(common_meta)
        if extra_meta:
            meta.update(extra_meta)
        return EvaluationRecord(
            case_id=case_id,
            target=self.context.target,
            case_kind=self.context.case_kind,
            status=status,
            evidence=self._build_evidence(evidence_path),
            output=output or {},
            meta=meta,
        )

    def _build_evidence(self, evidence_path: Path) -> dict[str, str]:
        evidence = {"trace_json": str(evidence_path)}
        for key, artifact_path in self._optional_artifact_paths(evidence_path).items():
            if artifact_path.exists():
                evidence[key] = str(artifact_path)
        return evidence

    def _optional_artifact_paths(self, trace_json_path: Path) -> dict[str, Path]:
        trace_name = trace_json_path.name
        if not trace_name.endswith(".json"):
            return {}
        prefix = trace_name[:-5]
        footage_prefix = trace_json_path.parent / f"{prefix}_footage"
        return {
            "video": Path(str(footage_prefix) + ".mp4"),
            "video_meta_json": Path(str(footage_prefix) + ".meta.json"),
        }

    def _error_record(
        self,
        *,
        case_id: str,
        evidence_path: Path,
        status: RunStatus,
        common_meta: dict[str, object],
        error_message: str,
        output: dict[str, object] | None = None,
        extra_meta: Mapping[str, object] | None = None,
    ) -> EvaluationRecord:
        meta = dict(common_meta)
        meta["analysis_pipeline"] = ["fixture_decode", "structural_validation"]
        meta["error_message"] = error_message
        if extra_meta:
            meta.update(extra_meta)
        return self._record(
            case_id=case_id,
            status=status,
            evidence_path=evidence_path,
            common_meta=meta,
            output=output,
        )


def _empty_trace_keys(payload: Mapping[str, Any]) -> list[str]:
    return sorted(
        key
        for key in REQUIRED_TOP_LEVEL_KEYS
        if isinstance(payload.get(key), (list, dict)) and len(payload[key]) == 0
    )


def _invalidate_kinematics(output: Mapping[str, object]) -> dict[str, object]:
    return {**output, **{key: -1 for key in KINEMATICS_OUTPUT_KEYS}}


def interpret_fixture(fixture_path: str | Path) -> EvaluationRecord:
    return ResultInterpreter().interpret_fixture(fixture_path)


def interpret_path(fixture_path: str | Path) -> EvaluationRecord:
    return ResultInterpreter().interpret_path(fixture_path)
