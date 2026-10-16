"""Execution boundary for the pure U-turn ODE model."""

from __future__ import annotations

import csv
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from contracts.execution import RawRunResult, RunStatus, TestCase

from .models.uturn import UTurnSimulationResult, simulate_uturn
from .models.uturn_sde import simulate_uturn_sde
from .profile import DynamicsExecutionProfile, build_execution_profile


@dataclass(frozen=True)
class DynamicsBackendConfig:
    output_root: Path = Path("artifacts/dynamics")
    source_module: str = "targets.dynamics.backend"


class DynamicsBackend:
    def __init__(self, config: DynamicsBackendConfig | None = None):
        self.config = config or DynamicsBackendConfig()

    def run(self, test_case: TestCase) -> RawRunResult:
        try:
            profile = build_execution_profile(
                test_case.input,
                default_output_root=self.config.output_root,
            )
        except ValueError as exc:
            return self._error_result(test_case, RunStatus.INVALID, str(exc))

        try:
            result = (
                simulate_uturn(profile.ode_config)
                if profile.solver_kind == "ode"
                else simulate_uturn_sde(profile.sde_config)
            )
            run_dir = self._create_run_dir(profile.output_root, test_case.case_id)
            raw_path, trace_path = self._write_artifacts(
                run_dir=run_dir,
                test_case=test_case,
                profile=profile,
                result=result,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return self._error_result(test_case, RunStatus.EXECUTION_ERROR, str(exc))

        return RawRunResult(
            case_id=test_case.case_id,
            target=test_case.target,
            case_kind=test_case.case_kind,
            status=RunStatus.SUCCESS,
            evidence={
                "raw_result_json": str(raw_path),
                "trace_csv": str(trace_path),
            },
            meta={
                "source_module": self.config.source_module,
                "path_root": str(run_dir),
                "backend_mode": profile.solver_kind,
                **profile.to_meta(),
            },
        )

    def _create_run_dir(self, output_root: Path, case_id: str) -> Path:
        safe_case_id = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in case_id
        )
        run_dir = output_root / f"{safe_case_id}-{uuid.uuid4().hex[:8]}"
        run_dir.mkdir(parents=True, exist_ok=False)
        return run_dir

    def _write_artifacts(
        self,
        *,
        run_dir: Path,
        test_case: TestCase,
        profile: DynamicsExecutionProfile,
        result: UTurnSimulationResult,
    ) -> tuple[Path, Path]:
        trace_path = run_dir / "trajectory.csv"
        with trace_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "time_sec",
                    "ego_x_m",
                    "ego_y_m",
                    "ego_heading_rad",
                    "ego_speed_mps",
                    "npc_x_m",
                    "npc_y_m",
                    "npc_heading_rad",
                    "npc_speed_mps",
                    "turn_angle_rad",
                ]
            )
            for time_sec, state in zip(result.times_sec, result.states, strict=True):
                writer.writerow([float(time_sec), *[float(value) for value in state]])

        raw_path = run_dir / "raw_result.json"
        payload = {
            "schema_version": 1,
            "model_id": profile.model_id,
            "case_id": test_case.case_id,
            "case_kind": test_case.case_kind,
            "input": profile.source_input,
            "execution": profile.to_meta(),
            "events": {
                "npc_start_time_sec": result.npc_start_time_sec,
                "uturn_start_time_sec": result.uturn_start_time_sec,
                "uturn_end_time_sec": result.uturn_end_time_sec,
            },
            "trace_csv": str(trace_path),
        }
        raw_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return raw_path, trace_path

    def _error_result(
        self,
        test_case: TestCase,
        status: RunStatus,
        message: str,
    ) -> RawRunResult:
        return RawRunResult(
            case_id=test_case.case_id,
            target=test_case.target,
            case_kind=test_case.case_kind,
            status=status,
            meta={
                "source_module": self.config.source_module,
                "error_message": message,
            },
        )


__all__ = ["DynamicsBackend", "DynamicsBackendConfig"]
