from __future__ import annotations

import json
from math import log
from pathlib import Path

import pytest

from contracts.evaluation import EvaluationRecord
from contracts.execution import RawRunResult, RunStatus
from orchestration.dynamics_importance_sampling import (
    DynamicsImportanceSamplingRunConfig,
    DynamicsImportanceSamplingRunner,
    _sample_seed,
)
from runtime.repository.importance_experiment_manifest import ManifestMismatchError
from runtime.repository.importance_sampling_results import ImportanceSamplingResultsRepository
from targets.dynamics.backend import DynamicsBackend, DynamicsBackendConfig
from targets.dynamics.result_interpreter import DynamicsResultInterpreter


def test_dynamics_fixed_runner_separates_p_and_q_runs(tmp_path: Path) -> None:
    plan = tmp_path / "sampling_plan.yaml"
    plan.write_text(
        """schema_version: 1
experiment_id: dynamics_is_v1
target: dynamics
target_measure:
  id: uturn_uniform_p_v1
  definition:
    model_id: uturn
    parameters:
      dx0: {distribution: uniform, min: 10.0, max: 25.0}
      ego_speed: {distribution: uniform, min: 30.0, max: 40.0}
      npc_speed: {distribution: uniform, min: 10.0, max: 25.0}
proposal:
  id: dx0_focus_q1
  mode: manual_proposal
  definition:
    parameters:
      dx0:
        distribution: mixture_uniform_focus
        base_weight: 0.7
        focus: {min: 10.0, max: 14.0}
      ego_speed: {distribution: uniform, min: 30.0, max: 40.0}
      npc_speed: {distribution: uniform, min: 10.0, max: 25.0}
""",
        encoding="utf-8",
    )

    class Backend:
        def run(self, case):
            return RawRunResult(case.case_id, case.target, case.case_kind, RunStatus.SUCCESS)

    class Interpreter:
        def interpret_raw_run_result(self, raw):
            return EvaluationRecord(
                raw.case_id, raw.target, raw.case_kind, raw.status,
                output={"c_collision": 0}, meta=dict(raw.meta),
            )

    result = DynamicsImportanceSamplingRunner(
        backend=Backend(), result_interpreter=Interpreter(),
    ).run_fixed_samples(
        DynamicsImportanceSamplingRunConfig(
            sampling_plan_path=plan, output_root=tmp_path / "run", max_samples=2, seed=10,
        )
    )

    assert (tmp_path / "run" / "ordinary" / "records.jsonl").exists()
    assert (tmp_path / "run" / "importance" / "records.jsonl").exists()
    assert result["comparison"]["target"] == "dynamics"
    assert result["comparison"]["target_measure_id"] == "uturn_uniform_p_v1"


def test_outer_seed_uses_disjoint_sample_seed_blocks() -> None:
    first = {_sample_seed(0, index) for index in range(1, 101)}
    second = {_sample_seed(1, index) for index in range(1, 101)}

    assert first.isdisjoint(second)


# --- v2 plans (dynamics_schema_version: 2, registered models) ---------------

BASELINE = Path("configs/dynamics_importance_ou_baseline.yaml")
GAUSSIAN_Q1 = Path("configs/dynamics_importance_ou_gaussian_q1.yaml")


def _run_v2(tmp_path: Path, plan: Path, *, backend=None, **config: object) -> dict:
    root = tmp_path / "run"
    runner = DynamicsImportanceSamplingRunner(
        backend=backend or DynamicsBackend(DynamicsBackendConfig(output_root=root / "artifacts")),
        result_interpreter=DynamicsResultInterpreter(),
    )
    values = {"max_samples": 6, "root_entropy": 123}
    values.update(config)
    return runner.run_fixed_samples(
        DynamicsImportanceSamplingRunConfig(sampling_plan_path=plan, output_root=root, **values)
    )["comparison"]


def test_v2_plan_runs_ou_with_a_manifest_and_a_complete_audit(tmp_path: Path) -> None:
    comparison = _run_v2(tmp_path, GAUSSIAN_Q1)

    assert comparison["final_status"] == "complete"
    assert comparison["metric"] == "c_event"
    assert comparison["identity_check"] == "fingerprint"
    manifest = json.loads((tmp_path / "run" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["rng"] == {"policy_version": 2, "root_entropy": "123", "phase": "final", "pairing": "independent"}
    record = ImportanceSamplingResultsRepository(tmp_path / "run").load_records("importance")[0]
    assert record.meta["sampling"]["sample_id"] == "importance-000001"
    assert record.meta["sampling"]["log_importance_weight"] != 0.0


def test_v2_q_equal_to_p_has_unit_weights_and_a_guarantee_interval(tmp_path: Path) -> None:
    comparison = _run_v2(tmp_path, BASELINE)

    records = ImportanceSamplingResultsRepository(tmp_path / "run").load_records("importance")
    assert {record.meta["sampling"]["log_importance_weight"] for record in records} == {0.0}
    assert comparison["guarantee"]["hoeffding"]["importance"]["max_importance_weight"] == 1.0


def test_v2_resume_reuses_the_manifest_root_and_rejects_other_settings(tmp_path: Path) -> None:
    first = _run_v2(tmp_path, GAUSSIAN_Q1)
    resumed = _run_v2(tmp_path, GAUSSIAN_Q1, root_entropy=None)

    assert resumed["diagnostic"]["importance"]["estimate"] == first["diagnostic"]["importance"]["estimate"]
    assert len(ImportanceSamplingResultsRepository(tmp_path / "run").load_records("importance")) == 6
    with pytest.raises(ManifestMismatchError, match="rng"):
        _run_v2(tmp_path, GAUSSIAN_Q1, root_entropy=124)
    with pytest.raises(ManifestMismatchError, match="planned_samples"):
        _run_v2(tmp_path, GAUSSIAN_Q1, max_samples=7)
    with pytest.raises(ManifestMismatchError, match="proposal_fingerprint"):
        _run_v2(tmp_path, BASELINE)


def test_v2_ordinary_and_importance_streams_are_independent_unless_paired(tmp_path: Path) -> None:
    seeds: dict[str, list[int]] = {}

    class Recorder(DynamicsBackend):
        def run(self, case):
            seeds.setdefault(case.meta["sampling"]["sample_id"].split("-")[0], []).append(case.input["sde_seed"])
            return super().run(case)

    _run_v2(tmp_path / "independent", BASELINE, backend=Recorder(DynamicsBackendConfig(output_root=tmp_path / "a")))
    assert set(seeds["ordinary"]).isdisjoint(seeds["importance"])

    seeds.clear()
    _run_v2(tmp_path / "paired", BASELINE, pairing="paired",
            backend=Recorder(DynamicsBackendConfig(output_root=tmp_path / "b")))
    assert seeds["ordinary"] == seeds["importance"]


def test_v2_injected_failure_is_incomplete_until_retried(tmp_path: Path) -> None:
    class Flaky(DynamicsBackend):
        failed = False

        def run(self, case):
            if case.meta["sampling"]["sample_id"] == "importance-000003" and not Flaky.failed:
                Flaky.failed = True
                raise RuntimeError("injected failure")
            return super().run(case)

    backend = Flaky(DynamicsBackendConfig(output_root=tmp_path / "artifacts"))
    assert _run_v2(tmp_path, GAUSSIAN_Q1, backend=backend)["final_status"] == "incomplete"
    retried = _run_v2(tmp_path, GAUSSIAN_Q1, backend=backend, retry_failed=True)

    assert retried["final_status"] == "complete"
    assert retried["audit"]["importance"]["counts"]["retries"] == 1


def test_v2_plan_with_an_innovation_law_proposal(tmp_path: Path) -> None:
    import yaml

    raw = yaml.safe_load(GAUSSIAN_Q1.read_text(encoding="utf-8"))
    proposal = raw["proposal"]["definition"]
    del proposal["noise_path_q"]
    proposal["noise"] = {
        "kind": "gaussian",
        "mean_rule": {"kind": "state_threshold", "state": "x", "threshold": 1.5,
                      "below": {"w": 0.3}, "above": {"w": 0.0}},
        "covariance": {"channels": ["w"], "matrix": [[1.2]]},
    }
    plan = tmp_path / "plan.yaml"
    plan.write_text(yaml.safe_dump(raw), encoding="utf-8")

    comparison = _run_v2(tmp_path, plan)

    assert comparison["final_status"] == "complete"
    record = ImportanceSamplingResultsRepository(tmp_path / "run").load_records("importance")[0]
    assert record.input["noise_q"]["kind"] == "gaussian"
    assert record.meta["sampling"]["log_importance_weight"] != 0.0


def test_v2_defensive_mixture_gives_a_guarantee_interval(tmp_path: Path) -> None:
    import yaml

    raw = yaml.safe_load(GAUSSIAN_Q1.read_text(encoding="utf-8"))
    proposal = raw["proposal"]["definition"]
    del proposal["noise_path_q"]
    proposal["noise"] = {
        "kind": "mixture",
        "defensive_weight": 0.2,
        "components": [
            {"weight": 0.5, "law": {"kind": "gaussian", "mean_rule": {"kind": "constant", "values": {"w": 0.3}}}},
            {"weight": 0.5, "law": {"kind": "student_t", "dof": 5,
                                    "location_rule": {"kind": "constant", "values": {"w": 0.3}}}},
        ],
    }
    plan = tmp_path / "plan.yaml"
    plan.write_text(yaml.safe_dump(raw), encoding="utf-8")

    comparison = _run_v2(tmp_path, plan, max_samples=20)

    assert comparison["final_status"] == "complete"
    assert comparison["guarantee"]["hoeffding"]["importance"]["max_importance_weight"] == pytest.approx(5.0)
    records = ImportanceSamplingResultsRepository(tmp_path / "run").load_records("importance")
    assert max(record.meta["sampling"]["log_importance_weight"] for record in records) <= log(5.0) + 1e-12
