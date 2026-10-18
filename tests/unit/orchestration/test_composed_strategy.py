import pandas as pd
import pytest

from contracts.experiment import SampleKind
from contracts.statistics import StatisticalRequest
from evaluation.stopping_rules import BinomialFixedStrict
from orchestration.composed_strategy import StoppingRuleService
from orchestration.fixed_parameter_sampling import FixedParameterSamplingStrategy
from orchestration.orchestrator import Orchestrator, OrchestratorConfig
from orchestration.random_parameter_sampling import RandomParameterSamplingStrategy

UNIFORM = {name: {"distribution": "uniform", "min": 0.0, "max": 1.0} for name in ("u_failure", "u_time", "u_score")}


def _config(tmp_path, **overrides) -> OrchestratorConfig:
    values = dict(
        output=str(tmp_path / "records.jsonl"), dataset_csv=str(tmp_path / "dataset.csv"),
        target="bernoulli", run_mode="binomial_ci", max_samples=50, dynamics_input_distribution=UNIFORM,
    )
    values.update(overrides)
    return OrchestratorConfig(**values)


def test_service_reports_pending_until_the_rule_decides() -> None:
    service = StoppingRuleService(BinomialFixedStrict(metric="c_failure", sample_size=3), SampleKind.IID_UNIFORM)
    request = StatisticalRequest(method="binomial_ci", metric="c_failure")

    pending = service.evaluate_request(pd.DataFrame({"c_failure": [0, 1], "status": ["success"] * 2}), request)
    done = service.evaluate_request(pd.DataFrame({"c_failure": [0, 1, 0], "status": ["success"] * 3}), request)

    assert pending.next_action == "collect_more_samples"
    assert done.next_action == "stop"
    assert done.diagnostics["guarantee"] == "fixed_sample"
    assert done.diagnostics["sample_kind"] == "iid_uniform"


@pytest.mark.parametrize(
    ("rule", "mode", "metric"),
    [
        ("binomial_ci_fixed_strict", "binomial_ci", "c_failure"),
        ("binomial_ci_sequential_anytime", "binomial_ci", "c_failure"),
        ("dkw_fixed_strict", "dkw_fixed", "time_to_failure"),
        ("dkw_sequential_anytime", "dkw", "time_to_failure"),
        ("sprt_wald", "sprt", "c_failure"),
        ("ebstop_anytime", "ebstop", "score"),
    ],
)
def test_iid_targets_get_the_composed_strategy(tmp_path, rule, mode, metric) -> None:
    strategy = Orchestrator()._build_strategy(_config(tmp_path, run_mode=mode, stopping_rule=rule))

    assert isinstance(strategy, RandomParameterSamplingStrategy)
    assert isinstance(strategy.statistical_service, StoppingRuleService)
    assert strategy.statistical_service.rule.spec.name == rule
    assert strategy.statistical_request.metric == metric


def test_prism_fixed_parameters_get_the_composed_strategy(tmp_path) -> None:
    config = _config(
        tmp_path, target="prism", run_mode="dkw_fixed", params={"steps": 20}, stopping_rule="dkw_fixed_strict",
        prism_dkw_target_metric="steps_to_failure_capped",
    )

    strategy = Orchestrator()._build_strategy(config)

    assert isinstance(strategy, FixedParameterSamplingStrategy)
    assert strategy.config.run_model_check_once
    assert strategy.statistical_service.sample_kind is SampleKind.FIXED_POINT_IID
    assert strategy.statistical_request.metric == "steps_to_failure_capped"


def test_awsim_strategist_does_not_take_a_stopping_rule_yet(tmp_path) -> None:
    config = _config(tmp_path, target="awsim", case_kind="uturn", stopping_rule="binomial_ci_fixed_strict")

    with pytest.raises(ValueError, match="not available"):
        Orchestrator()._build_strategy(config)


def test_cli_checks_that_the_rule_matches_the_mode() -> None:
    from apps.cli import orchestrator_main

    argv = ["--target", "bernoulli", "--mode", "sprt", "--stopping-rule", "dkw_fixed_strict",
            "--max-samples", "10", "--output", "x.jsonl", "--dataset-csv", "x.csv"]
    with pytest.raises(ValueError, match="needs --mode"):
        orchestrator_main.validate_args(orchestrator_main.build_parser().parse_args(argv))


def test_composed_iid_run_end_to_end(tmp_path) -> None:
    import json

    from apps.cli.orchestrator_main import run_orchestrator

    output = tmp_path / "records.jsonl"
    exit_code = run_orchestrator([
        "--target", "bernoulli", "--mode", "binomial_ci", "--stopping-rule", "binomial_ci_fixed_strict",
        "--max-samples", "60", "--seed", "2", "--output", str(output), "--dataset-csv", str(tmp_path / "d.csv"),
    ])

    report = json.loads(output.with_name("records.jsonl.summary.json").read_text(encoding="utf-8"))["statistical_report"]
    assert exit_code == 0
    assert report["sample_count"] == 60
    assert report["diagnostics"]["stopping_rule"] == "binomial_ci_fixed_strict"
    assert report["diagnostics"]["guarantee"] == "fixed_sample"
