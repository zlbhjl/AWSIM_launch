from __future__ import annotations

import math
from csv import DictReader

from contracts.execution import RunStatus, TestCase
from targets.prism.backend import PrismBackend, PrismBackendConfig
from targets.prism.sampling import run_importance_sample_path, run_target_sample_path
from targets.prism.model_catalog import SIMPLE_RELIABILITY_DTMC


def _plan(path) -> None:
    path.write_text(
        """\
schema_version: 1
experiment_id: prism_is_v1
target: prism
target_measure:
  id: original_model_v1
  definition:
    model: simple_reliability_dtmc
proposal:
  id: failure_biased_v1
  mode: manual_proposal
  definition:
    transition_probabilities:
      normal: {normal: 0.60, degraded: 0.20, failure: 0.20}
      degraded: {normal: 0.20, degraded: 0.40, failure: 0.40}
      failure: {failure: 1.00}
""",
        encoding="utf-8",
    )


def _constants() -> dict[str, float]:
    return {
        "P_NORMAL_DEGRADE": 0.10,
        "P_NORMAL_FAILURE": 0.01,
        "P_DEGRADED_NORMAL": 0.30,
        "P_DEGRADED_FAILURE": 0.10,
    }


def test_importance_sampler_generates_reproducible_q_path_and_log_weight(tmp_path) -> None:
    plan_path = tmp_path / "sampling_plan.yaml"
    _plan(plan_path)

    first = run_importance_sample_path(
        SIMPLE_RELIABILITY_DTMC,
        constants=_constants(),
        horizon=8,
        output_dir=tmp_path / "first",
        sampling_plan_path=plan_path,
        seed=17,
    )
    second = run_importance_sample_path(
        SIMPLE_RELIABILITY_DTMC,
        constants=_constants(),
        horizon=8,
        output_dir=tmp_path / "second",
        sampling_plan_path=plan_path,
        seed=17,
    )

    assert first.trace_csv.read_text(encoding="utf-8") == second.trace_csv.read_text(encoding="utf-8")
    sampling = first.sampling_metadata
    assert sampling["importance_weight_status"] == "available"
    assert math.isclose(
        float(sampling["log_importance_weight"]),
        float(sampling["log_p"]) - float(sampling["log_q"]),
    )
    assert float(sampling["importance_weight_upper_bound"]) >= math.exp(
        float(sampling["log_importance_weight"])
    )


def test_importance_sampler_keeps_p_fixed_and_calculates_path_likelihoods(tmp_path) -> None:
    """Q chooses the path; P remains the original transition measure."""
    plan_path = tmp_path / "sampling_plan.yaml"
    _plan(plan_path)
    constants = _constants()
    constants_before = dict(constants)
    sample = run_importance_sample_path(
        SIMPLE_RELIABILITY_DTMC,
        constants=constants,
        horizon=8,
        output_dir=tmp_path / "sample",
        sampling_plan_path=plan_path,
        seed=17,
    )

    p = {
        0: {0: 0.89, 1: 0.10, 2: 0.01},
        1: {0: 0.30, 1: 0.60, 2: 0.10},
        2: {2: 1.00},
    }
    q = {
        0: {0: 0.60, 1: 0.20, 2: 0.20},
        1: {0: 0.20, 1: 0.40, 2: 0.40},
        2: {2: 1.00},
    }
    with sample.trace_csv.open(encoding="utf-8", newline="") as handle:
        states = [int(row["state"]) for row in DictReader(handle)]
    assert len(states) == 9  # initial state plus the eight sampled transitions
    transitions = zip(states, states[1:])
    expected_log_p = sum(math.log(p[source][destination]) for source, destination in transitions)
    expected_log_q = sum(
        math.log(q[source][destination])
        for source, destination in zip(states, states[1:])
    )

    assert constants == constants_before
    assert sample.sampling_metadata["proposal_id"] == "failure_biased_v1"
    assert float(sample.sampling_metadata["log_p"]) == math.fsum([expected_log_p])
    assert float(sample.sampling_metadata["log_q"]) == math.fsum([expected_log_q])
    assert float(sample.sampling_metadata["log_importance_weight"]) == math.fsum(
        [expected_log_p - expected_log_q]
    )


def test_target_sampler_generates_reproducible_p_path(tmp_path) -> None:
    first = run_target_sample_path(SIMPLE_RELIABILITY_DTMC, constants=_constants(), horizon=8, output_dir=tmp_path / "first", seed=17)
    second = run_target_sample_path(SIMPLE_RELIABILITY_DTMC, constants=_constants(), horizon=8, output_dir=tmp_path / "second", seed=17)
    assert first.trace_csv.read_text(encoding="utf-8") == second.trace_csv.read_text(encoding="utf-8")


def test_prism_backend_records_importance_sampling_provenance_without_running_prism(tmp_path) -> None:
    plan_path = tmp_path / "sampling_plan.yaml"
    _plan(plan_path)
    backend = PrismBackend(PrismBackendConfig(output_root=tmp_path))

    result = backend.run(
        TestCase(
            case_id="importance-sample",
            target="prism",
            case_kind="simple_reliability_dtmc",
            input={
                "execution_kind": "sample_path",
                "steps": 8,
                "sampling_plan_path": str(plan_path),
                "sampling_seed": 17,
            },
        )
    )

    assert result.status is RunStatus.SUCCESS
    assert result.meta["sampling"]["plan_id"] == "prism_is_v1"
    assert result.meta["sampling"]["proposal_id"] == "failure_biased_v1"
    assert result.meta["sampling"]["importance_weight_status"] == "available"


def test_prism_backend_uses_candidate_q_override_and_records_its_version(tmp_path) -> None:
    plan_path = tmp_path / "sampling_plan.yaml"
    _plan(plan_path)
    backend = PrismBackend(PrismBackendConfig(output_root=tmp_path))

    result = backend.run(
        TestCase(
            case_id="candidate-sample",
            target="prism",
            case_kind="simple_reliability_dtmc",
            input={
                "execution_kind": "sample_path",
                "steps": 8,
                "sampling_plan_path": str(plan_path),
                "sampling_seed": 17,
                "proposal_id": "candidate_001",
                "proposal_version": 1,
                "proposal_sampling_mode": "adaptive",
                "proposal_definition": {
                    "transition_probabilities": {
                        "normal": {"normal": 0.94, "degraded": 0.03, "failure": 0.03},
                        "degraded": {"normal": 0.28, "degraded": 0.66, "failure": 0.06},
                        "failure": {"failure": 1.0},
                    }
                },
            },
        )
    )

    assert result.status is RunStatus.SUCCESS
    assert result.meta["sampling"]["proposal_id"] == "candidate_001"
    assert result.meta["sampling"]["proposal_version"] == 1
    assert result.meta["sampling"]["sampling_mode"] == "adaptive"
