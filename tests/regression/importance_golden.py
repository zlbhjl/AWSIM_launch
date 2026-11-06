"""Golden runs of the importance-sampling runners (docs/blackbox_importance_sampling_plan.md §6).

Each case runs a production runner on a small N with fixed seeds and keeps the
per-sample records (inputs, outputs, sampling metadata) and the comparison.
The refactoring must reproduce them bit for bit; floats are compared through
JSON, whose float repr round-trips exactly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[2]
GOLDEN_PATH = REPO / "tests" / "fixtures" / "importance_golden" / "runs.json"


@dataclass(frozen=True)
class GoldenCase:
    name: str
    run: Callable[[Path], dict[str, object]]


def _sanitize(value: object, root: Path) -> object:
    if isinstance(value, dict):
        return {str(key): _sanitize(item, root) for key, item in value.items()
                if key not in {"created_at", "elapsed_sec", "seconds", "sampling_plan_path", "comparison_path"}}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, root) for item in value]
    if isinstance(value, str):
        return value.replace(str(root), "<tmp>").replace(str(REPO), "<repo>")
    return value


def _without_internal(sampling: object) -> object:
    if isinstance(sampling, dict):
        return {key: value for key, value in sampling.items() if key != "internal"}
    return sampling


def _records(output_root: Path) -> dict[str, list[dict[str, object]]]:
    from runtime.repository.importance_sampling_results import ImportanceSamplingResultsRepository

    repository = ImportanceSamplingResultsRepository(output_root)
    return {
        run_kind: [
            {
                "case_id": record.case_id,
                "status": record.status.value,
                # ``sde_component`` (B3) passes the shared code's component choice;
                # the case inputs are otherwise unchanged.
                "input": {key: value for key, value in record.input.items() if key != "sde_component"},
                "output": record.output,
                # The ``internal`` report (B2) is provenance added after the
                # golden runs were recorded; the weights themselves must not change.
                "sampling": _without_internal(record.meta.get("sampling")),
                "attempt_id": record.meta.get("attempt_id"),
            }
            for record in repository.load_records(run_kind)
        ]
        for run_kind in ("ordinary", "importance")
    }


def _dynamics_case(plan: Path, *, samples: int, suffix: str = "", **config: object) -> GoldenCase:
    def run(root: Path) -> dict[str, object]:
        from targets.dynamics.backend import DynamicsBackend, DynamicsBackendConfig
        from targets.dynamics.importance_sampling import (
            DynamicsImportanceSamplingRunConfig,
            DynamicsImportanceSamplingRunner,
        )
        from targets.dynamics.result_interpreter import DynamicsResultInterpreter

        runner = DynamicsImportanceSamplingRunner(
            backend=DynamicsBackend(DynamicsBackendConfig(output_root=root / "artifacts", artifact_policy="summary")),
            result_interpreter=DynamicsResultInterpreter(),
        )
        result = runner.run_fixed_samples(DynamicsImportanceSamplingRunConfig(
            sampling_plan_path=plan, output_root=root / "run", max_samples=samples, **config,
        ))
        return {"comparison": result["comparison"], "records": _records(root / "run")}

    return GoldenCase(f"dynamics/{plan.relative_to(REPO)}{suffix}", run)


class _EchoBackend:
    """AWSIM stand-in: returns the case so its inputs and sampling metadata are recorded."""

    def run(self, case):
        from contracts.execution import RawRunResult, RunStatus

        return RawRunResult(case.case_id, case.target, case.case_kind, RunStatus.SUCCESS,
                            meta={"input_echo": dict(case.input)})


class _CollisionFromSpeed:
    def interpret_raw_run_result(self, raw):
        from contracts.evaluation import EvaluationRecord

        inputs = raw.meta.get("input_echo", {})
        collision = int(float(inputs.get("ego_speed", 0.0)) - float(inputs.get("npc_speed", 0.0)) > 22.0)
        return EvaluationRecord(raw.case_id, raw.target, raw.case_kind, raw.status,
                                output={"c_collision": collision}, meta=dict(raw.meta))


def _awsim_case(plan: Path, *, samples: int, seed: int) -> GoldenCase:
    def run(root: Path) -> dict[str, object]:
        from targets.awsim.importance_sampling import (
            AWSIMImportanceSamplingRunConfig,
            AWSIMImportanceSamplingRunner,
        )

        runner = AWSIMImportanceSamplingRunner(backend=_EchoBackend(), result_interpreter=_CollisionFromSpeed())
        result = runner.run_fixed_samples(AWSIMImportanceSamplingRunConfig(
            sampling_plan_path=plan, output_root=root / "run", max_samples=samples, seed=seed,
        ))
        return {"comparison": result["comparison"], "records": _records(root / "run")}

    return GoldenCase(f"awsim/{plan.relative_to(REPO)}", run)


def _prism_case(plan: Path, *, samples: int, seed: int, stop_mode: str = "fixed_samples", **extra) -> GoldenCase:
    def run(root: Path) -> dict[str, object]:
        from targets.prism.backend import PrismBackend, PrismBackendConfig
        from targets.prism.importance_sampling import (
            PrismImportanceSamplingRunConfig,
            PrismImportanceSamplingRunner,
        )
        from targets.prism.result_interpreter import PrismResultInterpreter

        runner = PrismImportanceSamplingRunner(
            backend=PrismBackend(PrismBackendConfig(output_root=root / "artifacts", prism_executable="prism-not-used")),
            result_interpreter=PrismResultInterpreter(),
        )
        result = runner.run(PrismImportanceSamplingRunConfig(
            sampling_plan_path=plan, output_root=root / "run", max_samples=samples, stop_mode=stop_mode,
            seed=seed, params={"model": "simple_reliability_dtmc", "steps": 20}, **extra,
        ))
        return {"result": {key: value for key, value in result.items() if key != "comparison_path"},
                "records": _records(root / "run")}

    suffix = "" if stop_mode == "fixed_samples" else f"#{stop_mode}"
    return GoldenCase(f"prism/{plan.relative_to(REPO)}{suffix}", run)


def golden_cases() -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    # v2 plans of the coverage and reference experiments (models A-D).
    for plan in sorted((REPO / "configs" / "is_final").glob("*.yaml")):
        cases.append(_dynamics_case(plan, samples=8, root_entropy=20261101, phase="final"))
    for plan in sorted((REPO / "configs" / "is_final_reference").glob("*.yaml")):
        samples = 4 if "uturn" in plan.name else 8
        cases.append(_dynamics_case(plan, samples=samples, root_entropy=20261201, phase="final"))
    if (REPO / "configs" / "is_final_reference" / "uturn_dx10p5_q0.yaml").exists():  # held in the public version
        cases.append(_dynamics_case(REPO / "configs" / "is_final_reference" / "uturn_dx10p5_q0.yaml",
                                    samples=4, root_entropy=20261026, phase="reference", suffix="#reference"))
    # Example plans: v1 (U-turn inputs and SDE noise paths) and v2 OU.
    for name in ("dynamics_importance_baseline", "dynamics_importance_joint_example", "dynamics_importance_q2",
                 "dynamics_importance_sde_noise_paths_example", "dynamics_importance_ou_baseline",
                 "dynamics_importance_ou_gaussian_q1"):
        plan = REPO / "configs" / f"{name}.yaml"
        if "ou_" in name:
            cases.append(_dynamics_case(plan, samples=6, root_entropy=7, phase="final"))
        else:
            cases.append(_dynamics_case(plan, samples=3, seed=7))
    for name in ("awsim_importance_baseline", "awsim_importance_q1"):
        cases.append(_awsim_case(REPO / "configs" / f"{name}.yaml", samples=6, seed=9))
    for name in ("prism_importance_sampling", "prism_rare_event_q1", "prism_rare_event_q2"):
        cases.append(_prism_case(REPO / "configs" / f"{name}.yaml", samples=6, seed=11))
    cases.append(_prism_case(REPO / "configs" / "prism_rare_event_q1.yaml", samples=40, seed=11,
                             stop_mode="importance_width", target_width=0.5))
    cases.append(_prism_case(REPO / "configs" / "prism_rare_event_q1.yaml", samples=40, seed=11,
                             stop_mode="weighted_sequential",
                             sequential_options={"null_upper": 0.01, "alternative_lower": 0.2}))
    return cases


def run_case(case: GoldenCase, root: Path) -> object:
    root.mkdir(parents=True, exist_ok=True)
    # Through JSON so floats and containers compare exactly as stored.
    return json.loads(json.dumps(_sanitize(case.run(root), root.resolve()), default=str))


def load_golden() -> dict[str, object]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def save_golden(runs: dict[str, object]) -> None:
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(json.dumps(runs, indent=1, sort_keys=True) + "\n", encoding="utf-8")
