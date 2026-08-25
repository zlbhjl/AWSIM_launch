from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from contracts.execution import RawRunResult, RunStatus, TestCase
from targets.bbsl.dataset_adapter import BBSLExperimentAdapter
from targets.bbsl.profile import build_execution_profile
from targets.bbsl.runner import run_bbsl_experiment


@dataclass(frozen=True)
class BBSLBackendConfig:
    source_module: str = "targets.bbsl.backend"
    default_target_repo: str = "/home/passd/BBSL-test"


class BBSLBackend:
    def __init__(
        self,
        config: BBSLBackendConfig | None = None,
        *,
        adapter: BBSLExperimentAdapter | None = None,
        experiment_runner=None,
    ):
        self.config = config or BBSLBackendConfig()
        self.adapter = adapter or BBSLExperimentAdapter()
        self.experiment_runner = experiment_runner or run_bbsl_experiment

    def run(self, test_case: TestCase) -> RawRunResult:
        if "fixture_path" in test_case.input or "raw_result_json" in test_case.input:
            return self._run_fixture(test_case)
        if not test_case.input:
            raise ValueError(
                "BBSL backend requires fixture_path/raw_result_json or explicit execution parameters"
            )
        return self._run_execution(test_case)

    def _run_fixture(self, test_case: TestCase) -> RawRunResult:
        fixture_path = self._resolve_fixture_path(test_case)
        return RawRunResult(
            case_id=test_case.case_id,
            target=test_case.target,
            case_kind=test_case.case_kind,
            status=RunStatus.SUCCESS,
            evidence={"raw_result_json": str(fixture_path)},
            meta={
                "source_module": self.config.source_module,
                "path_root": str(fixture_path.parent),
                "backend_mode": "fixture",
            },
        )

    def _run_execution(self, test_case: TestCase) -> RawRunResult:
        profile = build_execution_profile(
            test_case.input,
            default_target_repo=self.config.default_target_repo,
        )

        if profile.reuse_existing_output:
            output_json_path = self.adapter.default_output_path(
                profile.target_repo,
                profile.mini,
                prefer_raw=True,
            )
        else:
            output_json_path = self.experiment_runner(
                profile.target_repo,
                **profile.runner_kwargs(),
            )

        resolved_output_path = Path(str(output_json_path)).expanduser().resolve()
        if not resolved_output_path.exists():
            raise FileNotFoundError(f"BBSL raw result not found: {resolved_output_path}")

        return RawRunResult(
            case_id=test_case.case_id,
            target=test_case.target,
            case_kind=test_case.case_kind,
            status=RunStatus.SUCCESS,
            evidence={"raw_result_json": str(resolved_output_path)},
            meta={
                "source_module": self.config.source_module,
                "path_root": str(resolved_output_path.parent),
                "backend_mode": "execution",
                **profile.to_meta(),
            },
        )

    def _resolve_fixture_path(self, test_case: TestCase) -> Path:
        fixture_value = test_case.input.get("fixture_path") or test_case.input.get("raw_result_json")
        if not fixture_value:
            raise ValueError(
                "BBSL backend requires test_case.input['fixture_path'] or ['raw_result_json']"
            )

        fixture_path = Path(str(fixture_value)).expanduser().resolve()
        if not fixture_path.exists():
            raise FileNotFoundError(f"BBSL fixture not found: {fixture_path}")
        return fixture_path
