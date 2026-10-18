import math
from types import SimpleNamespace

import pytest

from contracts.execution import RunStatus, TestCase
from orchestration.orchestrator import Orchestrator, OrchestratorConfig
from orchestration.random_parameter_sampling import RandomParameterSamplingStrategy
from targets.bernoulli.backend import BernoulliBackend
from targets.bernoulli.model import (
    BernoulliParameters,
    evaluate,
    failure_probability,
    score_mean,
    time_to_failure_quantile,
)
from targets.bernoulli.result_interpreter import BernoulliResultInterpreter
from targets.registry import build_target_components
from targets.statistical import load_statistical_target_profile, supports_iid_sampling


def _case(**inputs) -> TestCase:
    values = {"u_failure": 0.01, "u_time": 0.5, "u_score": 0.5, **inputs}
    return TestCase(case_id="b-1", target="bernoulli", case_kind="uturn", input=values)


def test_model_maps_uniform_inputs_by_inverse_transform() -> None:
    parameters = BernoulliParameters(p_failure=0.05, rate=2.0)

    outcome = evaluate(parameters, {"u_failure": 0.049, "u_time": 0.5, "u_score": 0.5})

    assert outcome["c_failure"] == 1
    assert outcome["time_to_failure"] == pytest.approx(math.log(2.0) / 2.0)
    assert 0.0 < outcome["score"] < 1.0
    assert evaluate(parameters, {"u_failure": 0.05, "u_time": 0.0, "u_score": 0.0})["c_failure"] == 0


def test_model_exposes_exact_statistics() -> None:
    parameters = BernoulliParameters(p_failure=0.2, rate=1.0, score_alpha=2.0, score_beta=5.0)

    assert failure_probability(parameters) == 0.2
    assert time_to_failure_quantile(parameters, 0.05) == pytest.approx(-math.log(0.95))
    assert score_mean(parameters) == pytest.approx(2.0 / 7.0)


def test_parameters_are_validated() -> None:
    with pytest.raises(ValueError):
        BernoulliParameters(p_failure=1.5)
    with pytest.raises(ValueError):
        BernoulliParameters(rate=0.0)


def test_backend_and_interpreter_produce_an_evaluation_record_with_truth() -> None:
    raw = BernoulliBackend().run(_case(p_failure=0.1))

    record = BernoulliResultInterpreter().interpret_raw_run_result(raw)

    assert raw.status is RunStatus.SUCCESS
    assert record.status is RunStatus.SUCCESS
    assert record.output["c_failure"] == 1
    assert record.meta["truth"]["failure_probability"] == 0.1
    assert record.meta["source_module"] == "targets.bernoulli.result_interpreter"


def test_invalid_input_becomes_an_invalid_record() -> None:
    raw = BernoulliBackend().run(_case(u_time=1.0))

    record = BernoulliResultInterpreter().interpret_raw_run_result(raw)

    assert raw.status is RunStatus.INVALID
    assert record.status is RunStatus.INVALID
    assert "u_time" in record.meta["error_message"]


def test_registry_builds_bernoulli_components() -> None:
    components = build_target_components(SimpleNamespace(target="bernoulli"))

    assert isinstance(components.backend, BernoulliBackend)
    assert isinstance(components.result_interpreter, BernoulliResultInterpreter)


def test_statistical_profile_declares_iid_sampling_and_metrics() -> None:
    profile = load_statistical_target_profile("bernoulli")

    assert supports_iid_sampling("bernoulli")
    assert supports_iid_sampling("dynamics")
    assert not supports_iid_sampling("awsim")
    assert (profile.binary_metric, profile.dkw_metric, profile.resolved_ebstop_metric) == (
        "c_failure",
        "time_to_failure",
        "score",
    )
    assert set(profile.default_input_distribution) == {"u_failure", "u_time", "u_score"}


@pytest.mark.parametrize(
    ("mode", "metric"),
    [("binomial_ci", "c_failure"), ("sprt", "c_failure"), ("dkw_fixed", "time_to_failure"), ("ebstop", "score")],
)
def test_orchestrator_uses_the_profile_metrics_for_bernoulli(tmp_path, mode, metric) -> None:
    config = OrchestratorConfig(
        output=str(tmp_path / "records.jsonl"),
        dataset_csv=str(tmp_path / "dataset.csv"),
        target="bernoulli",
        run_mode=mode,
        max_samples=10,
        dynamics_input_distribution={
            name: {"distribution": "uniform", "min": 0.0, "max": 1.0} for name in ("u_failure", "u_time", "u_score")
        },
    )

    strategy = Orchestrator()._build_strategy(config)

    assert isinstance(strategy, RandomParameterSamplingStrategy)
    assert strategy.statistical_request.metric == metric
    assert strategy.config.target == "bernoulli"
    assert strategy.config.reason_prefix == "BERNOULLI_IID_SAMPLING"
