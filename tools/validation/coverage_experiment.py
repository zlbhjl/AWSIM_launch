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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from multiprocessing import Pool
from pathlib import Path

import pandas as pd
from scipy.stats import beta as beta_distribution

from contracts.execution import TestCase
from evaluation.dkw import DKWService
from orchestration.dkw_mode import DKWModeRunner, DKWModeState
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

DKW_QUANTILE = 0.05  # q used by every DKW request in the framework
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


ENGINES: dict[str, Callable[[Scenario, int, Path], dict[str, object]]] = {
    "iid": run_iid_once,
    "dkw_runner": run_dkw_runner_once,
}


def judge(scenario: Scenario, result: Mapping[str, object]) -> dict[str, object]:
    """Decide whether one experiment's result is correct for its scenario."""
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
        if scenario.mode == "sprt":
            entry["undecided_rate"] = float(rows["verdict"].isin(["continue"]).mean() + rows["verdict"].isna().mean())
        summary.append(entry)
    return summary


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
