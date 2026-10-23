"""Execution boundary for the dynamics models (U-turn and registered SDE models)."""

from __future__ import annotations

import csv
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from contracts.execution import RawRunResult, RunStatus, TestCase
from contracts.sampling import copy_sampling_metadata, sampling_metadata_from_meta

from .models.base import HybridTrajectory
from .models.registry import DynamicsModelAdapter, ResolvedModelRun, get_model_adapter
from .models.uturn import UTurnSimulationResult, simulate_uturn
from .models.uturn_sde import simulate_uturn_sde
from .profile import DynamicsExecutionProfile, build_execution_profile


ARTIFACT_POLICIES = ("files", "summary")
INLINE_RESULT_KEY = "_inline_result"


@dataclass(frozen=True)
class DynamicsBackendConfig:
    output_root: Path = Path("artifacts/dynamics")
    source_module: str = "targets.dynamics.backend"
    # Registered (schema 2) models only. "files" writes trajectory.csv and
    # raw_result.json per run; "summary" keeps the trajectory in memory and
    # hands it to the interpreter, so only the judged record is stored.
    artifact_policy: str = "files"

    def __post_init__(self) -> None:
        if self.artifact_policy not in ARTIFACT_POLICIES:
            raise ValueError(f"artifact_policy must be one of {list(ARTIFACT_POLICIES)}")


class DynamicsBackend:
    def __init__(self, config: DynamicsBackendConfig | None = None):
        self.config = config or DynamicsBackendConfig()

    def run(self, test_case: TestCase) -> RawRunResult:
        try:
            adapter = get_model_adapter(str(test_case.input.get("model_id", "uturn")))
        except ValueError as exc:
            return self._error_result(test_case, RunStatus.INVALID, str(exc))
        if not adapter.legacy_execution or test_case.input.get("dynamics_schema_version") == 2:
            return self._run_registered_model(test_case, adapter)
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
                **self._result_meta(test_case, profile, result),
            },
        )

    def _run_registered_model(self, test_case: TestCase, adapter: DynamicsModelAdapter) -> RawRunResult:
        """Run a registered model; its result uses the generic trajectory format (schema 2)."""
        try:
            resolved = adapter.resolve(test_case.input)
        except ValueError as exc:
            return self._error_result(test_case, RunStatus.INVALID, str(exc))
        output_root = Path(str(test_case.input.get("output_root", self.config.output_root)))
        try:
            trajectory = resolved.solve()
            names, values, extra_events = resolved.trace(trajectory)
            payload: dict[str, object] = {
                "schema_version": 2,
                "model_id": resolved.execution_meta["model_id"],
                "case_id": test_case.case_id,
                "case_kind": test_case.case_kind,
                "input": dict(test_case.input),
                "state_names": list(names),
                "execution": {**resolved.execution_meta, **_noise_path_meta(trajectory)},
                "events": {"mode_entry_times_sec": dict(trajectory.mode_entry_times), **extra_events},
            }
            run_dir: Path | None = None
            evidence: dict[str, str] = {}
            inline: dict[str, object] = {}
            if self.config.artifact_policy == "files":
                run_dir = self._create_run_dir(output_root, test_case.case_id)
                raw_path, trace_path = self._write_generic_artifacts(
                    run_dir=run_dir, payload=payload, names=names, values=values, trajectory=trajectory,
                )
                evidence = {"raw_result_json": str(raw_path), "trace_csv": str(trace_path)}
            else:
                trace = {"time_sec": np.asarray(trajectory.times_sec, dtype=float),
                         "mode": np.asarray(trajectory.modes)}
                trace.update({name: values[:, index] for index, name in enumerate(names)})
                inline = {INLINE_RESULT_KEY: {"payload": payload, "trace": trace}}
        except (OSError, RuntimeError, ValueError) as exc:
            return self._error_result(test_case, RunStatus.EXECUTION_ERROR, str(exc))
        meta = {**resolved.execution_meta, **_noise_path_meta(trajectory)}
        if resolved.solver_kind == "sde":
            meta = _merge_noise_weight(
                test_case, meta, trajectory, has_proposal=resolved.noise_proposal is not None
            )
        return RawRunResult(
            case_id=test_case.case_id,
            target=test_case.target,
            case_kind=test_case.case_kind,
            status=RunStatus.SUCCESS,
            evidence=evidence,
            meta={
                "source_module": self.config.source_module,
                **({"path_root": str(run_dir)} if run_dir is not None else {}),
                "backend_mode": resolved.solver_kind,
                "artifact_policy": self.config.artifact_policy,
                **meta,
                **inline,
            },
        )

    @staticmethod
    def _result_meta(
        test_case: TestCase,
        profile: DynamicsExecutionProfile,
        result: UTurnSimulationResult,
    ) -> dict[str, object]:
        meta = profile.to_meta()
        if profile.sde_config is None:
            return meta
        meta.update(_noise_path_meta(result))
        return _merge_noise_weight(
            test_case, meta, result, has_proposal=profile.sde_config.noise_proposal is not None
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
        execution = {
            **profile.to_meta(),
            "sde_path_log_p": result.noise_log_p,
            "sde_path_log_q": result.noise_log_q,
            "sde_path_noise_sample_count": result.noise_sample_count,
        }
        payload = {
            "schema_version": 1,
            "model_id": profile.model_id,
            "case_id": test_case.case_id,
            "case_kind": test_case.case_kind,
            "input": profile.source_input,
            "execution": execution,
            "events": {
                "npc_start_time_sec": result.npc_start_time_sec,
                "uturn_start_time_sec": result.uturn_start_time_sec,
                "uturn_end_time_sec": result.uturn_end_time_sec,
            },
            "trace_csv": str(trace_path),
        }
        raw_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return raw_path, trace_path

    def _write_generic_artifacts(
        self,
        *,
        run_dir: Path,
        payload: dict[str, object],
        names: tuple[str, ...],
        values: np.ndarray,
        trajectory: HybridTrajectory,
    ) -> tuple[Path, Path]:
        trace_path = run_dir / "trajectory.csv"
        with trace_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["time_sec", "mode", *names])
            for time_sec, mode, row in zip(trajectory.times_sec, trajectory.modes, values, strict=True):
                writer.writerow([repr(float(time_sec)), mode, *[repr(float(value)) for value in row]])
        raw_path = run_dir / "raw_result.json"
        raw_path.write_text(
            json.dumps({**payload, "trace_csv": str(trace_path)}, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
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


def _noise_path_meta(result: HybridTrajectory | UTurnSimulationResult) -> dict[str, object]:
    return {
        "sde_path_log_p": result.noise_log_p,
        "sde_path_log_q": result.noise_log_q,
        "sde_path_noise_sample_count": result.noise_sample_count,
    }


def _merge_noise_weight(
    test_case: TestCase,
    meta: dict[str, object],
    result: HybridTrajectory | UTurnSimulationResult,
    *,
    has_proposal: bool,
) -> dict[str, object]:
    """Add the realised Brownian-path log ratio to the input sampler's ratio.

    The input sampler supplied its P_input/Q_input ratio.  When this run also
    changed the Brownian path law, append its exact realised Euler--Maruyama
    path likelihood ratio.
    """
    sampling = sampling_metadata_from_meta(test_case.meta)
    if sampling is None or sampling.get("importance_weight_status") != "available":
        return meta
    if not has_proposal:
        return meta
    if sampling.get("weight_kind") == "zero":
        # p = 0 already; only q changes.
        sampling["log_q"] = float(sampling["log_q"]) + result.noise_log_q
        return copy_sampling_metadata({"sampling": sampling}, meta)
    sampling["log_p"] = float(sampling["log_p"]) + result.noise_log_p
    sampling["log_q"] = float(sampling["log_q"]) + result.noise_log_q
    sampling["log_importance_weight"] = float(sampling["log_p"]) - float(sampling["log_q"])
    return copy_sampling_metadata({"sampling": sampling}, meta)


__all__ = ["ARTIFACT_POLICIES", "DynamicsBackend", "DynamicsBackendConfig", "INLINE_RESULT_KEY"]
