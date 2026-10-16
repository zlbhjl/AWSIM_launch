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
                sample = run_prism_sample_path(
                    definition,
                    constants=constants,
                    horizon=horizon,
                    output_dir=run_dir,
                    prism_executable=prism_executable,
                    timeout_sec=timeout_sec,
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
                        "trace": _execution_payload(sample.execution),
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
            return RawRunResult(
                case_id=test_case.case_id,
                target="prism",
                case_kind=test_case.case_kind,
                status=RunStatus.SUCCESS,
                evidence=evidence,
                meta={"source_module": self.config.source_module, "path_root": str(run_dir)},
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
