"""Measure the coverage of the statistical modes on a target with known truth.

A confidence interval at level 1 - delta should contain the true value in at
least 1 - delta of repeated experiments. AWSIM cannot check this because the
true collision probability is unknown. The bernoulli target
(`targets/bernoulli`) has exactly known statistics, so each scenario below
repeats a whole experiment (sampling, stopping, final interval) with different
seeds and counts how often the result is correct.

The experiments drive the production stopping logic, not a re-implementation:

- engine ``iid``: the strategy that `Orchestrator._build_strategy` builds for
  IID-sampling targets (`orchestration.random_parameter_sampling`), as used by
  `--target dynamics` and `--target bernoulli`.
- engine ``dkw_runner``: `orchestration.dkw_mode.DKWModeRunner`, as used by the
  AWSIM and PRISM `--mode dkw` / `dkw_fixed`.
- engine ``importance_fixed``: the fixed-N importance-sampling comparison of
  `apps/cli/dynamics_importance_main.py` for a v2 plan, on the scalar OU model
  whose Euler--Maruyama terminal law is exact (`targets/dynamics/models/exact.py`).
  Each run is judged separately for every interval it reports: ordinary MC
  (Wilson), IS normal approximation, and the bounded Hoeffding and empirical
  Bernstein intervals when Q has a weight bound.

Only the dataset storage is replaced with an in-memory table, and each result is
appended before the next decision. This is identical to running the CLI with
``--queue-high-water 1`` (checked for several seeds). By default the CLI keeps
``4 x workers`` cases queued, so it re-evaluates every few samples, and after
deciding to stop it still completes the cases already queued; the final report
covers all completed samples. Evaluating after every sample is the case with
the most checks, i.e. the least favourable one for sequential rules.

    python3 -m tools.validation.coverage_experiment --reps 2000 --output-dir artifacts/coverage
"""

from __future__ import annotations

import os

# Each worker process runs one experiment at a time; keep numerical libraries
# single-threaded so that the processes do not oversubscribe the CPU.
for _variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_variable, "1")

import argparse
import json
import math
import random
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from multiprocessing import Pool
from pathlib import Path

import pandas as pd
import yaml
from scipy.stats import beta as beta_distribution

from contracts.execution import TestCase
from evaluation.dkw import DKWService
from orchestration.dkw_mode import DKWModeRunner, DKWModeState
from orchestration.dynamics_importance_sampling import (
    DynamicsImportanceSamplingRunConfig,
    DynamicsImportanceSamplingRunner,
)
from orchestration.orchestrator import Orchestrator, OrchestratorConfig
from orchestration.seeding import INPUT_STREAM, derive_seed
from targets.bernoulli.backend import BernoulliBackend
from targets.bernoulli.model import (
    INPUT_NAMES,
    BernoulliParameters,
    failure_probability,
    score_mean,
    time_to_failure_quantile,
)
from targets.bernoulli.result_interpreter import BernoulliResultInterpreter
from targets.dynamics.backend import DynamicsBackend, DynamicsBackendConfig
from targets.dynamics.models.exact import (
    ou_augmented_terminal_exceedance_probability,
    ou_euler_terminal_variance,
    ou_terminal_exceedance_probability,
)
from targets.dynamics.result_interpreter import DynamicsResultInterpreter

DKW_QUANTILE = 0.05  # q used by every DKW request in the framework
OU_MODEL_A = {"theta": 1.0, "sigma": 1.0, "dt_sec": 0.01, "horizon_sec": 1.0, "threshold_sd": 3.0, "side": "upper"}
INTERVAL_TYPES = ("mc_wilson", "is_normal", "is_hoeffding", "is_bernstein")
UNIFORM_INPUTS = {name: {"distribution": "uniform", "min": 0.0, "max": 1.0} for name in INPUT_NAMES}


@dataclass(frozen=True)
class Scenario:
    """One experimental condition, repeated with different seeds."""

    name: str
    engine: str  # "iid" or "dkw_runner"
    mode: str
    description: str
    parameters: Mapping[str, float] = field(default_factory=dict)
    config: Mapping[str, object] = field(default_factory=dict)
    nominal: float = 0.95  # the level the result should reach
    truth_key: str | None = None  # key of TRUTHS; None for the bernoulli scenarios


# The default set of scenarios. Sample sizes are kept moderate so that a few
# thousand repetitions finish in minutes.
SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "binomial_wilson_fixed_n100_p0.05", "iid", "binomial_ci",
        "Wilson, one evaluation at n=100, p=0.05",
        {"p_failure": 0.05},
        {"binomial_method": "wilson", "binomial_target_width": 1.0, "binomial_min_samples": 100, "max_samples": 100},
    ),
    Scenario(
        "binomial_cp_fixed_n100_p0.05", "iid", "binomial_ci",
        "Clopper-Pearson, one evaluation at n=100, p=0.05",
        {"p_failure": 0.05},
        {"binomial_method": "clopper-pearson", "binomial_target_width": 1.0, "binomial_min_samples": 100,
         "max_samples": 100},
    ),
    Scenario(
        "binomial_wilson_seq_w0.05_p0.05", "iid", "binomial_ci",
        "Wilson, re-evaluated after every sample, stop at width 0.05, p=0.05",
        {"p_failure": 0.05},
        {"binomial_method": "wilson", "binomial_target_width": 0.05, "max_samples": 5000},
    ),
    Scenario(
        "binomial_cp_seq_w0.05_p0.05", "iid", "binomial_ci",
        "Clopper-Pearson, re-evaluated after every sample, stop at width 0.05, p=0.05",
        {"p_failure": 0.05},
        {"binomial_method": "clopper-pearson", "binomial_target_width": 0.05, "max_samples": 5000},
    ),
    Scenario(
        "binomial_cp_anytime_seq_w0.05_p0.05", "iid", "binomial_ci",
        "Clopper-Pearson with --binomial-anytime-valid, stop at width 0.05, p=0.05",
        {"p_failure": 0.05},
        {"binomial_method": "clopper-pearson", "binomial_target_width": 0.05, "binomial_anytime_valid": True,
         "max_samples": 20000},
    ),
    Scenario(
        "binomial_wilson_seq_w0.1_p0.2", "iid", "binomial_ci",
        "Wilson, re-evaluated after every sample, stop at width 0.1, p=0.2",
        {"p_failure": 0.2},
        {"binomial_method": "wilson", "binomial_target_width": 0.1, "max_samples": 5000},
    ),
    Scenario(
        "binomial_cp_seq_w0.1_p0.2", "iid", "binomial_ci",
        "Clopper-Pearson, re-evaluated after every sample, stop at width 0.1, p=0.2",
        {"p_failure": 0.2},
        {"binomial_method": "clopper-pearson", "binomial_target_width": 0.1, "max_samples": 5000},
    ),
    Scenario(
        "sprt_true_p0", "iid", "sprt",
        "SPRT H0: p=0.1 vs H1: p=0.05, alpha=beta=0.05, true p=0.1 (error = accepting H1)",
        {"p_failure": 0.1},
        {"sprt_p0": 0.1, "sprt_p1": 0.05, "sprt_beta": 0.05, "sprt_confidence": 0.95, "max_samples": 20000},
    ),
    Scenario(
        "sprt_true_p1", "iid", "sprt",
        "SPRT H0: p=0.1 vs H1: p=0.05, alpha=beta=0.05, true p=0.05 (error = accepting H0)",
        {"p_failure": 0.05},
        {"sprt_p0": 0.1, "sprt_p1": 0.05, "sprt_beta": 0.05, "sprt_confidence": 0.95, "max_samples": 20000},
    ),
    Scenario(
        "dkw_iid_seq_eps0.15", "iid", "dkw",
        "IID path (dynamics/bernoulli) --mode dkw: one evaluation per stage (n=50 s^2, budget delta/2^s), "
        "stop at width 0.15; 5% quantile of Exponential(1)",
        {"rate": 1.0},
        {"prism_dkw_confidence": 0.95, "prism_dkw_target_epsilon": 0.15, "prism_dkw_base_samples": 50,
         "max_samples": 5000},
    ),
    Scenario(
        "dkw_iid_fixed_n500", "iid", "dkw_fixed",
        "IID path (dynamics/bernoulli) --mode dkw_fixed: one evaluation at n=500; 5% quantile of Exponential(1)",
        {"rate": 1.0},
        {"prism_dkw_confidence": 0.95, "prism_dkw_target_epsilon": 1.0, "max_samples": 500},
    ),
    Scenario(
        "dkw_runner_fixed_n500", "dkw_runner", "dkw_fixed",
        "DKWModeRunner --mode dkw_fixed (AWSIM/PRISM): one evaluation at n=500; 5% quantile of Exponential(1)",
        {"rate": 1.0},
        {"total_delta": 0.05, "target_epsilon": 1.0, "base_samples": 50, "max_samples": 500},
    ),
    Scenario(
        "dkw_runner_seq_eps0.15", "dkw_runner", "dkw",
        "DKWModeRunner --mode dkw (AWSIM/PRISM): one evaluation per stage (n=50 s^2, budget delta/2^s), "
        "stop at width 0.15",
        {"rate": 1.0},
        {"total_delta": 0.05, "target_epsilon": 0.15, "base_samples": 50, "max_samples": 5000},
    ),
    # --stopping-rule: the rules with an explicit schedule and guarantee (evaluation/stopping_rules.py).
    Scenario(
        "rule_binomial_fixed_strict_n100_p0.05", "iid", "binomial_ci",
        "--stopping-rule binomial_ci_fixed_strict: Clopper-Pearson, one evaluation at n=100, p=0.05",
        {"p_failure": 0.05},
        {"stopping_rule": "binomial_ci_fixed_strict", "max_samples": 100},
    ),
    Scenario(
        "rule_binomial_seq_anytime_w0.05_p0.05", "iid", "binomial_ci",
        "--stopping-rule binomial_ci_sequential_anytime: stop at width 0.05, p=0.05",
        {"p_failure": 0.05},
        {"stopping_rule": "binomial_ci_sequential_anytime", "binomial_target_width": 0.05, "max_samples": 20000},
    ),
    Scenario(
        "rule_binomial_seq_anytime_w0.1_p0.2", "iid", "binomial_ci",
        "--stopping-rule binomial_ci_sequential_anytime: stop at width 0.1, p=0.2",
        {"p_failure": 0.2},
        {"stopping_rule": "binomial_ci_sequential_anytime", "binomial_target_width": 0.1, "max_samples": 20000},
    ),
    Scenario(
        "rule_dkw_fixed_strict_n1000", "iid", "dkw_fixed",
        "--stopping-rule dkw_fixed_strict: one evaluation at n=1000; 5% quantile of Exponential(1)",
        {"rate": 1.0},
        {"stopping_rule": "dkw_fixed_strict", "prism_dkw_confidence": 0.95, "max_samples": 1000},
    ),
    Scenario(
        "rule_dkw_seq_anytime_eps0.15", "iid", "dkw",
        "--stopping-rule dkw_sequential_anytime: stop at width 0.15; 5% quantile of Exponential(1)",
        {"rate": 1.0},
        {"stopping_rule": "dkw_sequential_anytime", "prism_dkw_confidence": 0.95, "prism_dkw_target_epsilon": 0.15,
         "max_samples": 20000},
    ),
    Scenario(
        "rule_sprt_wald_true_p0", "iid", "sprt",
        "--stopping-rule sprt_wald: H0 p=0.1 vs H1 p=0.05, alpha=beta=0.05, true p=0.1",
        {"p_failure": 0.1},
        {"stopping_rule": "sprt_wald", "sprt_p0": 0.1, "sprt_p1": 0.05, "sprt_beta": 0.05, "sprt_confidence": 0.95,
         "max_samples": 20000},
    ),
    Scenario(
        "rule_sprt_wald_true_p1", "iid", "sprt",
        "--stopping-rule sprt_wald: H0 p=0.1 vs H1 p=0.05, alpha=beta=0.05, true p=0.05",
        {"p_failure": 0.05},
        {"stopping_rule": "sprt_wald", "sprt_p0": 0.1, "sprt_p1": 0.05, "sprt_beta": 0.05, "sprt_confidence": 0.95,
         "max_samples": 20000},
    ),
    Scenario(
        "rule_ebstop_anytime_beta25_eps0.1", "iid", "ebstop",
        "--stopping-rule ebstop_anytime: relative error 0.1 for the mean of Beta(2,5)",
        {"score_alpha": 2.0, "score_beta": 5.0},
        {"stopping_rule": "ebstop_anytime", "ebstop_epsilon": 0.1, "ebstop_value_range": (0.0, 1.0),
         "ebstop_confidence": 0.95, "max_samples": 20000},
    ),
    # Exploration data: rows drawn with u_time = v**2 (v uniform), concentrated on short
    # times to failure, like boundary-seeking exploration. Without --dkw-pure-smc the
    # DKW runner reuses them (sequential: with KDE weights; fixed: unweighted).
    Scenario(
        "dkw_runner_seq_kde_explore300", "dkw_runner", "dkw",
        "DKWModeRunner --mode dkw without --dkw-pure-smc: 300 biased exploration rows reused with KDE weights",
        {"rate": 1.0},
        {"total_delta": 0.05, "target_epsilon": 0.15, "base_samples": 50, "max_samples": 5000,
         "pure_smc": False, "exploration_rows": 300},
    ),
    Scenario(
        "dkw_runner_fixed_mixed_explore300", "dkw_runner", "dkw_fixed",
        "DKWModeRunner --mode dkw_fixed without --dkw-pure-smc: 300 biased exploration rows mixed in unweighted, n=500 new",
        {"rate": 1.0},
        {"total_delta": 0.05, "target_epsilon": 1.0, "base_samples": 50, "max_samples": 500,
         "pure_smc": False, "exploration_rows": 300},
    ),
    Scenario(
        "dkw_runner_seq_pure_explore300", "dkw_runner", "dkw",
        "DKWModeRunner --mode dkw with --dkw-pure-smc: the same 300 exploration rows are present but ignored",
        {"rate": 1.0},
        {"total_delta": 0.05, "target_epsilon": 0.15, "base_samples": 50, "max_samples": 5000,
         "pure_smc": True, "exploration_rows": 300},
    ),
    Scenario(
        "ebstop_beta25_eps0.1", "iid", "ebstop",
        "EBStop, relative error 0.1 for the mean of Beta(2,5); success = |estimate - mean| <= 0.1 * mean",
        {"score_alpha": 2.0, "score_beta": 5.0},
        {"ebstop_epsilon": 0.1, "ebstop_value_range": (0.0, 1.0), "ebstop_confidence": 0.95, "max_samples": 20000},
    ),
    # Importance sampling on OU model A (docs/sde_importance_sampling_plan.md §12-13):
    # dX = -X dt + dW, h = 0.01, T = 1, event X_K > 3 sd, P = 1.35e-3.
    Scenario(
        "is_ou_a_3sd_q_equals_p_n1000", "importance_fixed", "importance_probability",
        "OU model A, Q = P (weights 1), n=1000 ordinary + 1000 importance paths",
        OU_MODEL_A, {"max_samples": 1000, "noise_path_q": None}, truth_key="ou_terminal",
    ),
    Scenario(
        "is_ou_a_3sd_gaussian_u3_n1000", "importance_fixed", "importance_probability",
        "OU model A, direct Gaussian drift u=3 on dW (no weight bound), n=1000",
        OU_MODEL_A,
        {"max_samples": 1000, "noise_path_q": {"distribution": "gaussian_drift", "channel_drifts": {"w": 3.0}}},
        truth_key="ou_terminal",
    ),
    Scenario(
        "is_ou_a_3sd_defensive_u3_b0.2_n1000", "importance_fixed", "importance_probability",
        "OU model A, defensive mixture 0.2 P + 0.8 Gaussian drift u=3 (weight bound 5), n=1000",
        OU_MODEL_A,
        {"max_samples": 1000, "noise_path_q": {"distribution": "defensive_gaussian_drift", "base_weight": 0.2,
                                                "channel_drifts": {"w": 3.0}}},
        truth_key="ou_terminal",
    ),
    *(
        Scenario(
            f"is_final_{plan.stem}_n{samples}", "importance_fixed", "importance_probability",
            f"Fixed final plan {plan.name} (pilot-tuned Q), n={samples} ordinary + {samples} importance paths",
            {"plan": str(plan)}, {"max_samples": samples}, truth_key="plan",
        )
        for samples in (1000, 10000)
        for plan in sorted(Path(__file__).resolve().parents[2].joinpath("configs", "is_final").glob("*.yaml"))
    ),
)


class InMemoryDataset:
    """Stands in for the dataset CSV repository used by the sampling strategies."""

    def __init__(self) -> None:
        self._rows: list[dict[str, object]] = []
        self._frame = pd.DataFrame()

    def append(self, row: Mapping[str, object]) -> None:
        self._rows.append(dict(row))

    def load_dataset(self) -> pd.DataFrame:
        if len(self._frame) != len(self._rows):
            self._frame = pd.DataFrame(self._rows)
        return self._frame


def _evaluate_case(inputs: Mapping[str, object], parameters: Mapping[str, float]) -> dict[str, object]:
    case = TestCase(case_id="coverage", target="bernoulli", case_kind="uturn", input={**parameters, **inputs})
    record = BernoulliResultInterpreter().interpret_raw_run_result(BernoulliBackend().run(case))
    return {**dict(record.output), "status": record.status.value}


def run_iid_once(scenario: Scenario, seed: int, workdir: Path) -> dict[str, object]:
    """Run one experiment through the strategy the orchestrator builds for IID targets."""
    config = OrchestratorConfig(
        output=str(workdir / "records.jsonl"),
        dataset_csv=str(workdir / "dataset.csv"),
        target="bernoulli",
        run_mode=scenario.mode,
        params=dict(scenario.parameters),
        sampling_seed=int(seed),
        dynamics_input_distribution=UNIFORM_INPUTS,
        **dict(scenario.config),
    )
    strategy = Orchestrator()._build_strategy(config)
    dataset = InMemoryDataset()
    strategy.dataset_repository = dataset
    while (case := strategy.next_test_case()) is not None:
        dataset.append({**case.input, **_evaluate_case(case.input, scenario.parameters), "reason": case.reason})
    report = strategy.latest_statistical_report
    return {
        "sample_count": len(dataset.load_dataset()),
        "stop_reason": strategy.stop_reason,
        "estimate": None if report is None else report.estimate,
        "interval": None if report is None or report.interval is None else list(report.interval),
        "verdict": None if report is None else report.diagnostics.get("verdict"),
        "next_action": None if report is None else report.next_action,
    }


class _LastDKWRecord:
    """History repository stub that keeps the last DKW evaluation."""

    def __init__(self) -> None:
        self.last: dict[str, object] | None = None

    def append_dkw_record(self, record: Mapping[str, object]) -> None:
        self.last = dict(record)

    def append_dkw_records(self, records: Sequence[Mapping[str, object]]) -> None:
        for record in records:
            self.append_dkw_record(record)


def run_dkw_runner_once(scenario: Scenario, seed: int, workdir: Path) -> dict[str, object]:
    """Run one experiment through DKWModeRunner, as AWSIM and PRISM do."""
    del workdir
    options = dict(scenario.config)
    history = _LastDKWRecord()
    runner = DKWModeRunner.from_runtime_config(
        param_names=list(INPUT_NAMES),
        target_metric="time_to_failure",
        target_metrics=["time_to_failure"],
        total_delta=float(options["total_delta"]),
        target_epsilon=float(options["target_epsilon"]),
        base_samples=int(options["base_samples"]),
        region="custom",
        pure_smc=bool(options.get("pure_smc", True)),
        simultaneous=False,
        max_samples=int(options["max_samples"]),
        dkw_service=DKWService(feature_names=list(INPUT_NAMES)),
        statistical_history_repository=history,
        minimum_value=0.0,
    )
    state = DKWModeState()
    dataset = InMemoryDataset()
    exploration_rng = random.Random(int(seed) * 7_919 + 17)
    for index in range(int(options.get("exploration_rows", 0))):
        inputs = {name: exploration_rng.random() for name in INPUT_NAMES}
        inputs["u_time"] = inputs["u_time"] ** 2
        dataset.append({**inputs, **_evaluate_case(inputs, scenario.parameters),
                        "reason": f"EXPLORATION: boundary search ({index + 1})", "loop_num": index + 1})

    def random_point(index: int) -> dict[str, object]:
        rng = random.Random(derive_seed(int(seed), int(index), INPUT_STREAM))
        return {name: rng.random() for name in INPUT_NAMES}

    handle = runner.handle_sequential if scenario.mode == "dkw" else runner.handle_fixed
    while True:
        payload = handle(dataset.load_dataset() if dataset._rows else None, state=state, get_random_point=random_point)
        if payload.get("system_command") == "stop":
            stop_reason = str(payload.get("reason", ""))
            break
        inputs = {name: payload[name] for name in INPUT_NAMES}
        dataset.append({**inputs, **_evaluate_case(inputs, scenario.parameters), "reason": payload["reason"]})
    last = history.last or {}
    interval = (
        [last["lower_bound"], last["upper_bound"]]
        if "lower_bound" in last and "upper_bound" in last
        else None
    )
    return {
        "sample_count": len(dataset._rows) - int(options.get("exploration_rows", 0)),
        "stop_reason": stop_reason,
        "estimate": last.get("estimate"),
        "interval": interval,
        "verdict": None,
        "next_action": None,
    }


def _ou_steps(parameters: Mapping[str, object]) -> int:
    return round(float(parameters["horizon_sec"]) / float(parameters["dt_sec"]))


def _ou_threshold(parameters: Mapping[str, object]) -> float:
    variance = ou_euler_terminal_variance(
        theta=float(parameters["theta"]), sigma=float(parameters["sigma"]),
        h=float(parameters["dt_sec"]), steps=_ou_steps(parameters),
    )
    return float(parameters["threshold_sd"]) * math.sqrt(variance)


def _ou_terminal_truth(parameters: Mapping[str, object]) -> float:
    return ou_terminal_exceedance_probability(
        theta=float(parameters["theta"]), sigma=float(parameters["sigma"]),
        h=float(parameters["dt_sec"]), steps=_ou_steps(parameters),
        threshold=_ou_threshold(parameters), side=str(parameters["side"]),
    )


def _plan_truth(parameters: Mapping[str, object]) -> float:
    """Exact probability of the numerical model named by a fixed v2 plan (models A and B)."""
    definition = yaml.safe_load(Path(str(parameters["plan"])).read_text(encoding="utf-8"))["target_measure"]["definition"]
    solver, event, params = definition["solver"], definition["event"], definition["model_params"]
    steps = round(float(solver["horizon_sec"]) / float(solver["dt_sec"]))
    if definition["model_id"] == "ou_scalar":
        return ou_terminal_exceedance_probability(
            theta=params["theta"], sigma=params["sigma"], h=solver["dt_sec"], steps=steps,
            threshold=event["threshold"], side=event.get("side", "upper"),
            x0=float(definition["initial_law"]["value"][0]),
        )
    if definition["model_id"] == "ou_augmented":
        c0, x0 = (float(value) for value in definition["initial_law"]["value"])
        return ou_augmented_terminal_exceedance_probability(
            **params, h=solver["dt_sec"], steps=steps, threshold=event["threshold"],
            side=event.get("side", "upper"), state=event.get("state", "c"), c0=c0, x0=x0,
        )
    raise ValueError(f"no exact probability for model {definition['model_id']!r}")


TRUTHS: dict[str, Callable[[Mapping[str, object]], float]] = {
    "ou_terminal": _ou_terminal_truth,
    "plan": _plan_truth,
}


def _ou_plan(scenario: Scenario) -> dict[str, object]:
    parameters = scenario.parameters
    proposal: dict[str, object] = {"dynamics_schema_version": 2, "input": {"kind": "same_as_target"}}
    if scenario.config.get("noise_path_q") is not None:
        proposal["noise_path_q"] = dict(scenario.config["noise_path_q"])
    return {
        "schema_version": 1,
        "experiment_id": scenario.name,
        "target": "dynamics",
        "target_measure": {
            "id": f"{scenario.name}_p",
            "definition": {
                "dynamics_schema_version": 2,
                "model_id": "ou_scalar",
                "model_params": {"theta": float(parameters["theta"]), "sigma": float(parameters["sigma"])},
                "initial_law": {"kind": "point_mass", "value": [0.0]},
                "solver": {"kind": "sde", "dt_sec": float(parameters["dt_sec"]),
                           "horizon_sec": float(parameters["horizon_sec"])},
                "event": {"kind": "terminal_exceedance", "threshold": _ou_threshold(parameters),
                          "side": str(parameters["side"])},
            },
        },
        "proposal": {"id": f"{scenario.name}_q", "mode": "manual_proposal", "definition": proposal},
    }


def _interval_of(block: object) -> list[float] | None:
    if not isinstance(block, Mapping) or block.get("lower_bound") is None or block.get("upper_bound") is None:
        return None
    return [float(block["lower_bound"]), float(block["upper_bound"])]


def run_importance_fixed_once(scenario: Scenario, seed: int, workdir: Path) -> dict[str, object]:
    """Run one fixed-N ordinary vs importance comparison through the production runner."""
    # A fresh directory per run: the runner resumes from (and checks) any
    # manifest already in its output root.
    workdir = Path(tempfile.mkdtemp(prefix=f"{scenario.name}-{seed}-", dir=workdir))
    if "plan" in scenario.parameters:
        plan_path = Path(str(scenario.parameters["plan"]))
    else:
        plan_path = workdir / "plan.yaml"
        plan_path.write_text(yaml.safe_dump(_ou_plan(scenario), sort_keys=False), encoding="utf-8")
    runner = DynamicsImportanceSamplingRunner(
        backend=DynamicsBackend(DynamicsBackendConfig(output_root=workdir / "artifacts", artifact_policy="summary")),
        result_interpreter=DynamicsResultInterpreter(),
    )
    started = time.perf_counter()
    comparison = runner.run_fixed_samples(
        DynamicsImportanceSamplingRunConfig(
            sampling_plan_path=plan_path,
            output_root=workdir / "run",
            max_samples=int(scenario.config["max_samples"]),
            root_entropy=int(seed),
            phase="final",
        )
    )["comparison"]
    elapsed = time.perf_counter() - started
    diagnostic = comparison.get("diagnostic", {})
    guarantee = comparison.get("guarantee", {})
    ordinary = diagnostic.get("ordinary", {})
    importance = diagnostic.get("importance", {})
    intervals = {
        "mc_wilson": _interval_of(ordinary),
        "is_normal": _interval_of(importance),
        "is_hoeffding": _interval_of(guarantee.get("hoeffding", {}).get("importance")),
        "is_bernstein": _interval_of(guarantee.get("empirical_bernstein", {}).get("importance")),
    }
    return {
        "sample_count": int(scenario.config["max_samples"]),
        "stop_reason": comparison.get("final_status"),
        "estimate": importance.get("estimate"),
        "interval": intervals["is_normal"],
        "verdict": None,
        "next_action": None,
        "estimate_mc": ordinary.get("estimate"),
        "estimate_is": importance.get("estimate"),
        "no_event_mc": ordinary.get("estimate") == 0.0,
        "no_event_is": importance.get("estimate") == 0.0,
        "ess_is": importance.get("effective_sample_size"),
        "seconds_per_path": elapsed / (2 * int(scenario.config["max_samples"])),
        **{f"interval_{name}": interval for name, interval in intervals.items()},
    }


ENGINES: dict[str, Callable[[Scenario, int, Path], dict[str, object]]] = {
    "iid": run_iid_once,
    "dkw_runner": run_dkw_runner_once,
    "importance_fixed": run_importance_fixed_once,
}


def judge(scenario: Scenario, result: Mapping[str, object]) -> dict[str, object]:
    """Decide whether one experiment's result is correct for its scenario."""
    if scenario.mode == "importance_probability":
        truth = TRUTHS[str(scenario.truth_key)](scenario.parameters)
        verdicts: dict[str, object] = {"truth": truth}
        for name in INTERVAL_TYPES:
            interval = result.get(f"interval_{name}")
            verdicts[f"correct_{name}"] = None if interval is None else bool(interval[0] <= truth <= interval[1])
        # The primary verdict is the IS normal interval; a missing interval
        # (no observed event, overflow) does not count as correct.
        verdicts["correct"] = bool(verdicts["correct_is_normal"])
        return verdicts
    parameters = BernoulliParameters(**{key: float(value) for key, value in scenario.parameters.items()})
    interval = result.get("interval")
    if scenario.mode == "binomial_ci":
        truth = failure_probability(parameters)
        correct = interval is not None and interval[0] <= truth <= interval[1]
    elif scenario.mode in {"dkw", "dkw_fixed"}:
        truth = time_to_failure_quantile(parameters, DKW_QUANTILE)
        correct = interval is not None and interval[0] <= truth <= interval[1]
    elif scenario.mode == "sprt":
        truth = failure_probability(parameters)
        p0, p1 = float(scenario.config["sprt_p0"]), float(scenario.config["sprt_p1"])
        expected = "h0" if math.isclose(truth, p0) else "h1" if math.isclose(truth, p1) else None
        # Undecided runs (max_samples reached) are not errors; they are reported separately.
        correct = result.get("verdict") in {expected, "continue", None}
    elif scenario.mode == "ebstop":
        truth = score_mean(parameters)
        estimate = result.get("estimate")
        epsilon = float(scenario.config["ebstop_epsilon"])
        correct = estimate is not None and abs(float(estimate) - truth) <= epsilon * truth
    else:
        raise ValueError(f"unsupported mode {scenario.mode}")
    return {"truth": truth, "correct": bool(correct)}


def _run_task(task: tuple[Scenario, int]) -> dict[str, object]:
    scenario, seed = task
    with tempfile.TemporaryDirectory(prefix="coverage-") as tmp:
        result = ENGINES[scenario.engine](scenario, seed, Path(tmp))
    return {"scenario": scenario.name, "seed": seed, **result, **judge(scenario, result)}


def clopper_pearson(successes: int, trials: int, confidence: float = 0.95) -> tuple[float, float]:
    alpha = 1.0 - confidence
    lower = 0.0 if successes == 0 else float(beta_distribution.ppf(alpha / 2, successes, trials - successes + 1))
    upper = 1.0 if successes == trials else float(beta_distribution.ppf(1 - alpha / 2, successes + 1, trials - successes))
    return lower, upper


def summarize(scenarios: Sequence[Scenario], runs: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    frame = pd.DataFrame(runs)
    summary = []
    for scenario in scenarios:
        rows = frame[frame["scenario"] == scenario.name]
        if rows.empty:
            continue
        correct = int(rows["correct"].sum())
        lower, upper = clopper_pearson(correct, len(rows))
        entry = {
            "scenario": scenario.name,
            "engine": scenario.engine,
            "mode": scenario.mode,
            "description": scenario.description,
            "reps": int(len(rows)),
            "correct_rate": correct / len(rows),
            "correct_rate_ci95": [lower, upper],
            "nominal": scenario.nominal,
            "below_nominal": upper < scenario.nominal,
            "mean_sample_count": float(rows["sample_count"].mean()),
            "truth": float(rows["truth"].iloc[0]),
        }
        if scenario.mode == "importance_probability":
            entry.update(_importance_summary(rows, float(entry["truth"])))
        if scenario.mode == "sprt":
            entry["undecided_rate"] = float(rows["verdict"].isin(["continue"]).mean() + rows["verdict"].isna().mean())
        summary.append(entry)
    return summary


def _importance_summary(rows: pd.DataFrame, truth: float) -> dict[str, object]:
    """Coverage of every interval type, and the accuracy of both estimators.

    ``coverage`` counts a run without that interval as not covering;
    ``missing_rate`` reports how often the interval was absent. The bounded
    (guarantee) intervals exist only when Q has a weight bound, so they are
    left out when no run reported them.
    """
    coverage: dict[str, object] = {}
    for name in INTERVAL_TYPES:
        column = rows[f"correct_{name}"]
        present = column.notna()
        if not present.any() and name in {"is_hoeffding", "is_bernstein"}:
            continue
        covered = int(column[present].astype(bool).sum())
        lower, upper = clopper_pearson(covered, len(rows))
        widths = [interval[1] - interval[0] for interval in rows[f"interval_{name}"] if interval is not None]
        coverage[name] = {
            "coverage": covered / len(rows),
            "coverage_ci95": [lower, upper],
            "below_nominal": upper < 0.95,
            "missing_rate": float(1.0 - present.mean()),
            "mean_width": float(sum(widths) / len(widths)) if widths else None,
        }
    estimators: dict[str, object] = {}
    for name in ("mc", "is"):
        estimates = pd.to_numeric(rows[f"estimate_{name}"], errors="coerce").dropna()
        if estimates.empty:
            continue
        errors = estimates - truth
        estimators[name] = {
            "mean": float(estimates.mean()),
            "bias": float(errors.mean()),
            "std": float(estimates.std(ddof=1)) if len(estimates) > 1 else None,
            "rmse": float(math.sqrt((errors ** 2).mean())),
            "relative_rmse": float(math.sqrt((errors ** 2).mean()) / truth) if truth else None,
            "no_event_rate": float(rows[f"no_event_{name}"].astype(bool).mean()),
            "missing_rate": float(1.0 - len(estimates) / len(rows)),
        }
    timing = pd.to_numeric(rows.get("seconds_per_path"), errors="coerce").dropna() if "seconds_per_path" in rows else []
    return {
        "intervals": coverage,
        "estimators": estimators,
        "mean_seconds_per_path": float(timing.mean()) if len(timing) else None,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--reps", type=int, default=2000, help="Repetitions per scenario")
    parser.add_argument("--scenarios", nargs="*", default=None, help="Scenario names (default: all)")
    parser.add_argument("--workers", type=int, default=None, help="Worker processes (default: CPU count)")
    parser.add_argument("--seed-base", type=int, default=20261018, help="Seed of the first repetition")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/coverage"))
    parser.add_argument("--list", action="store_true", help="List the scenarios and exit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list:
        for scenario in SCENARIOS:
            print(f"{scenario.name}: {scenario.description}")
        return 0
    by_name = {scenario.name: scenario for scenario in SCENARIOS}
    unknown = sorted(set(args.scenarios or []) - set(by_name))
    if unknown:
        raise SystemExit(f"unknown scenarios: {', '.join(unknown)}")
    selected = [by_name[name] for name in args.scenarios] if args.scenarios else list(SCENARIOS)
    tasks = [(scenario, args.seed_base + rep) for scenario in selected for rep in range(args.reps)]
    with Pool(processes=args.workers) as pool:
        runs = pool.map(_run_task, tasks, chunksize=max(1, len(tasks) // (8 * (args.workers or 32))))
    summary = summarize(selected, runs)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(runs).to_csv(args.output_dir / "runs.csv", index=False)
    (args.output_dir / "summary.json").write_text(
        json.dumps({"reps": args.reps, "seed_base": args.seed_base, "scenarios": summary}, indent=2) + "\n",
        encoding="utf-8",
    )
    for entry in summary:
        low, high = entry["correct_rate_ci95"]
        flag = "  BELOW NOMINAL" if entry["below_nominal"] else ""
        print(
            f"{entry['scenario']:40s} {entry['correct_rate']:.4f} [{low:.4f}, {high:.4f}] "
            f"n~{entry['mean_sample_count']:.0f}{flag}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
