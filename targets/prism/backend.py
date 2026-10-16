from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from contracts.execution import RawRunResult, RunStatus, TestCase

from .profile import resolve_prism_execution_profile
from .runner import (
    PrismExecutionError,
    run_prism_model_check,
    run_prism_sample_path,
)
from .sampling import run_importance_sample_path, run_target_sample_path
from .trace_parser import parse_simpath_csv, summarize_trace


@dataclass(frozen=True)
class PrismBackendConfig:
    output_root: Path = Path("artifacts/prism")
    prism_executable: str = "prism"
    timeout_sec: float = 30.0
    source_module: str = "targets.prism.backend"


class PrismBackend:
    def __init__(self, config: PrismBackendConfig | None = None):
        self.config = config or PrismBackendConfig()

    def run(self, test_case: TestCase) -> RawRunResult:
        try:
            profile = resolve_prism_execution_profile(
                test_case,
                default_output_root=self.config.output_root,
                default_prism_executable=self.config.prism_executable,
                default_timeout_sec=self.config.timeout_sec,
            )
            definition = profile.definition
            constants = profile.constants
            horizon = profile.horizon
            run_dir = self._run_dir(
                test_case.case_id,
                output_root=profile.output_root,
            )
            prism_executable = profile.prism_executable
            timeout_sec = profile.timeout_sec
            execution_kind = profile.execution_kind
            if execution_kind == "model_check":
                model_check = run_prism_model_check(
                    definition,
                    constants=constants,
                    horizon=horizon,
                    output_dir=run_dir,
                    prism_executable=prism_executable,
                    timeout_sec=timeout_sec,
                )
                payload = {
                    "record_kind": "exact_model_check",
                    "model": definition.model_id,
                    "constants": constants,
                    "horizon": horizon,
                    "property_results": model_check.property_results,
                    "executions": {
                        "properties": _execution_payload(model_check.execution),
                    },
                }
                evidence = {
                    "raw_result_json": str(run_dir / "raw_result.json"),
                    "property_results_csv": str(model_check.result_csv),
                }
            elif execution_kind == "sample_path":
                sampling_plan_path = test_case.input.get("sampling_plan_path")
                sampling_metadata: dict[str, object] | None = None
                target_adapter = test_case.input.get("sampling_engine") == "target_adapter"
                if target_adapter:
                    sample = run_target_sample_path(
                        definition, constants=constants, horizon=horizon, output_dir=run_dir,
                        seed=_optional_seed(test_case.input.get("sampling_seed")),
                    )
                elif sampling_plan_path is None:
                    sample = run_prism_sample_path(
                        definition,
                        constants=constants,
                        horizon=horizon,
                        output_dir=run_dir,
                        prism_executable=prism_executable,
                        timeout_sec=timeout_sec,
                    )
                else:
                    sample = run_importance_sample_path(
                        definition,
                        constants=constants,
                        horizon=horizon,
                        output_dir=run_dir,
                        sampling_plan_path=str(sampling_plan_path),
                        seed=_optional_seed(test_case.input.get("sampling_seed")),
                        proposal_definition=test_case.input.get("proposal_definition"),
                        proposal_id=_optional_string(test_case.input.get("proposal_id")),
                        proposal_version=_optional_positive_int(test_case.input.get("proposal_version")),
                        sampling_mode=_optional_string(test_case.input.get("proposal_sampling_mode")),
                    )
                    sampling_metadata = sample.sampling_metadata
                trace_execution = (
                    _execution_payload(sample.execution)
                    if sampling_metadata is None and not target_adapter
                    else {
                        "command": ["targets.prism.sampling.run_importance_sample_path"],
                        "returncode": 0,
                        "elapsed_sec": None,
                    }
                )
                trace = parse_simpath_csv(sample.trace_csv)
                trace_summary = summarize_trace(trace, horizon=horizon)
                payload = {
                    "record_kind": "sample",
                    "model": definition.model_id,
                    "constants": constants,
                    "horizon": horizon,
                    "trace_summary": trace_summary,
                    "trace_csv": str(sample.trace_csv),
                    "executions": {
                        "trace": trace_execution,
                    },
                }
                evidence = {
                    "raw_result_json": str(run_dir / "raw_result.json"),
                    "trace_csv": str(sample.trace_csv),
                }
            else:
                raise ValueError(
                    f"Unsupported PRISM execution_kind: {execution_kind!r}"
                )
            raw_path = run_dir / "raw_result.json"
            raw_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            evidence["raw_result_json"] = str(raw_path)
            meta: dict[str, object] = {
                "source_module": self.config.source_module,
                "path_root": str(run_dir),
            }
            if execution_kind == "sample_path" and sampling_metadata is not None:
                meta["sampling"] = sampling_metadata
            if execution_kind == "sample_path" and target_adapter:
                meta["sampling_engine"] = "target_adapter"
            return RawRunResult(
                case_id=test_case.case_id,
                target="prism",
                case_kind=test_case.case_kind,
                status=RunStatus.SUCCESS,
                evidence=evidence,
                meta=meta,
            )
        except PrismExecutionError as exc:
            return self._error_result(test_case, RunStatus.EXECUTION_ERROR, str(exc), execution=exc.execution)
        except (FileNotFoundError, ValueError, OSError) as exc:
            return self._error_result(test_case, RunStatus.EXECUTION_ERROR, str(exc))

    def _run_dir(
        self,
        case_id: str,
        *,
        output_root: object | None = None,
    ) -> Path:
        safe_id = "".join(char if char.isalnum() or char in "-_" else "_" for char in case_id)
        root = (
            Path(str(output_root))
            if output_root is not None
            else self.config.output_root
        )
        path = root.expanduser().resolve() / f"{safe_id}-{uuid.uuid4().hex[:8]}"
        path.mkdir(parents=True, exist_ok=False)
        return path

    def _error_result(self, test_case: TestCase, status: RunStatus, message: str, *, execution=None) -> RawRunResult:
        evidence: dict[str, str] = {}
        meta: dict[str, object] = {"source_module": self.config.source_module, "error_message": message}
        if execution is not None:
            meta["execution"] = _execution_payload(execution)
        return RawRunResult(test_case.case_id, "prism", test_case.case_kind, status, evidence=evidence, meta=meta)


def _execution_payload(execution) -> dict[str, object]:
    return {"command": list(execution.command), "returncode": execution.returncode, "elapsed_sec": execution.elapsed_sec}


def _optional_seed(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("optional proposal string must be a non-empty string")
    return value


def _optional_positive_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("proposal_version must be a positive integer")
    return value
