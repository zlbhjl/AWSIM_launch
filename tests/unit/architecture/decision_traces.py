"""Record the decisions the strategies make when driven step by step.

Each strategy is built by the orchestrator exactly as for a real run, the
dataset storage is replaced with an in-memory table, and every case is answered
by a deterministic stand-in for the target. The trace records, for each step,
the case the strategy issued (inputs and reason), then why it stopped and its
final statistical report. Comparing traces before and after a refactor shows
whether the strategies still decide the same way.

Regenerate with ``UPDATE_DECISION_TRACES=1 python3 -m pytest tests/unit/architecture``
only when a behavior change is intended.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
from pathlib import Path

import pandas as pd

TRACES_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "architecture" / "decision_traces.json"
MAX_STEPS = 120
TRACED_STEPS = 25  # steps recorded in full; later steps only count


class InMemoryDataset:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def load_dataset(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)


def _uniform(*keys: object) -> float:
    """Deterministic pseudo-uniform number in [0, 1) from keys (no global RNG)."""
    text = "|".join(str(key) for key in keys)
    value = 0
    for char in text:
        value = (value * 131 + ord(char)) % 2_147_483_647
    return (value % 1_000_003) / 1_000_003


def awsim_outcome(case_input: dict[str, object], step: int) -> dict[str, object]:
    dx0 = float(case_input.get("dx0", 15.0))
    ego = float(case_input.get("ego_speed", 35.0))
    npc = float(case_input.get("npc_speed", 15.0))
    margin = dx0 - 0.35 * ego + 0.2 * npc
    min_ttc = max(0.0, 0.25 * margin + 1.5)
    return {
        "c_collision": int(margin < 3.0),
        "min_ttc": round(min_ttc, 6),
        "min_distance": round(max(0.0, margin), 6),
        "z_margin": round(margin / 5.0, 6),
        "min_ttb": round(max(0.0, 0.2 * margin + 1.0), 6),
    }


def prism_outcome(case_input: dict[str, object], step: int) -> dict[str, object]:
    if case_input.get("record_kind") == "exact_model_check":
        return {
            "record_kind": "exact_model_check",
            "prism_eventual_failure_probability": 0.42,
            "prism_bounded_failure_probability": 0.3,
        }
    draw = _uniform(case_input.get("sample_index", step), case_input.get("experiment_id", ""))
    return {
        "record_kind": case_input.get("record_kind", "sample"),
        "c_failure": int(draw < 0.3),
        "steps_to_failure_capped": int(1 + 19 * draw),
    }


def bernoulli_outcome(case_input: dict[str, object], step: int) -> dict[str, object]:
    from contracts.execution import TestCase
    from targets.bernoulli.backend import BernoulliBackend
    from targets.bernoulli.result_interpreter import BernoulliResultInterpreter

    case = TestCase(case_id=f"trace-{step}", target="bernoulli", case_kind="bernoulli", input=dict(case_input))
    record = BernoulliResultInterpreter().interpret_raw_run_result(BernoulliBackend().run(case))
    return dict(record.output)


OUTCOMES = {"awsim": awsim_outcome, "prism": prism_outcome, "bernoulli": bernoulli_outcome}

# Statistical options as in the documented examples, sized so every run stops quickly.
BINOMIAL = ("--binomial-target-width", "0.2")
SPRT = ("--sprt-p0", "0.5", "--sprt-p1", "0.2", "--sprt-beta", "0.05")
EBSTOP_RANGE = ("--ebstop-value-range-min", "0", "--ebstop-epsilon", "0.2", "--ebstop-max-samples", "100")
DKW = ("--prism-dkw-target-epsilon", "0.5")

# (target, mode, extra CLI arguments)
COMBINATIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("bernoulli", "binomial_ci", ("--seed", "7", "--max-samples", "100", *BINOMIAL)),
    ("bernoulli", "sprt", ("--seed", "7", "--max-samples", "100", *SPRT)),
    ("bernoulli", "dkw", ("--seed", "7", "--max-samples", "100", *DKW)),
    ("bernoulli", "dkw_fixed", ("--seed", "7", "--max-samples", "100", *DKW)),
    ("bernoulli", "ebstop", ("--seed", "7", "--max-samples", "100", *EBSTOP_RANGE, "--ebstop-value-range-max", "1")),
    ("prism", "binomial_ci", ("--param", "steps=20", "--max-samples", "100", *BINOMIAL)),
    ("prism", "sprt", ("--param", "steps=20", "--max-samples", "100", "--sprt-target", "c_failure", *SPRT)),
    ("prism", "ebstop", ("--param", "steps=20", "--max-samples", "100", "--ebstop-target", "steps_to_failure_capped",
                         *EBSTOP_RANGE, "--ebstop-value-range-max", "20")),
    ("prism", "dkw", ("--param", "steps=20", "--max-samples", "100", *DKW)),
    ("prism", "dkw_fixed", ("--param", "steps=20", "--max-samples", "60", *DKW)),
    ("awsim", "binomial_ci", ("--max-samples", "100", *BINOMIAL)),
    ("awsim", "sprt", ("--max-samples", "100", *SPRT)),
    ("awsim", "ebstop", ("--max-samples", "100", "--ebstop-target", "min_ttc", *EBSTOP_RANGE,
                         "--ebstop-value-range-max", "10")),
    ("awsim", "dkw", ("--max-samples", "100", "--dkw-pure-smc")),
    ("awsim", "dkw_fixed", ("--max-samples", "60", "--dkw-pure-smc")),
    ("awsim", "explore", ("--max-samples", "30")),
)


def _rounded(value: object) -> object:
    if isinstance(value, float):
        return None if math.isnan(value) else round(value, 9)
    if isinstance(value, dict):
        return {str(key): _rounded(item) for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_rounded(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def _build_strategy(target: str, mode: str, extra: tuple[str, ...], tmp: Path):
    from apps.cli import orchestrator_main
    from orchestration.orchestrator import Orchestrator

    argv = [
        "--target", target, "--mode", mode, "--output", str(tmp / "records.jsonl"),
        "--dataset-csv", str(tmp / "dataset.csv"), *extra,
    ]
    args = orchestrator_main.normalize_args(orchestrator_main.build_parser().parse_args(argv), argv=argv)
    return Orchestrator()._build_strategy(orchestrator_main.build_orchestrator_config(args))


def _final_report(strategy) -> object:
    payload_method = getattr(strategy, "statistical_report_payload", None)
    if callable(payload_method):
        return payload_method()
    final = getattr(strategy, "latest_final_report", None)
    if final is not None and hasattr(final, "to_payload"):
        return final.to_payload()
    return None


def drive(target: str, mode: str, extra: tuple[str, ...], tmp: Path) -> dict[str, object]:
    from threadpoolctl import threadpool_limits

    with threadpool_limits(limits=1):
        return _drive(target, mode, extra, tmp)


def _drive(target: str, mode: str, extra: tuple[str, ...], tmp: Path) -> dict[str, object]:
    strategy = _build_strategy(target, mode, extra, tmp)
    dataset = InMemoryDataset()
    strategy.dataset_repository = dataset
    outcome = OUTCOMES[target]
    steps: list[dict[str, object]] = []
    count = 0
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        while count < MAX_STEPS and (case := strategy.next_test_case()) is not None:
            count += 1
            if count <= TRACED_STEPS:
                steps.append({"reason": case.reason, "input": _rounded(dict(case.input))})
            dataset.rows.append(
                {**case.input, **outcome(dict(case.input), count), "status": "success",
                 "reason": case.reason, "loop_num": count}
            )
        report = _final_report(strategy)
    return {
        "steps": steps,
        "step_count": count,
        "stopped": count < MAX_STEPS,
        "stop_reason": getattr(strategy, "stop_reason", None),
        "final_report": _rounded(report),
    }


def build_traces(tmp: Path) -> dict[str, object]:
    traces = {}
    for target, mode, extra in COMBINATIONS:
        run_dir = tmp / f"{target}_{mode}"
        run_dir.mkdir(parents=True, exist_ok=True)
        traces[f"{target}:{mode}"] = drive(target, mode, extra, run_dir)
    return json.loads(json.dumps(traces).replace(str(tmp), "<tmp>"))


def load_traces() -> dict[str, object]:
    return json.loads(TRACES_PATH.read_text(encoding="utf-8"))


def write_traces(traces: dict[str, object]) -> None:
    TRACES_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRACES_PATH.write_text(json.dumps(traces, indent=1, sort_keys=True) + "\n", encoding="utf-8")
