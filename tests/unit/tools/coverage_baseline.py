"""Pin the results of a few coverage-experiment repetitions.

The coverage experiment drives the production stopping logic, so its result
for a fixed seed changes only when that logic changes. Regenerate with
``UPDATE_COVERAGE_BASELINE=1 python3 -m pytest tests/unit/tools`` only when a
behavior change is intended.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

BASELINE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "validation" / "coverage_baseline.json"
# Fast scenarios covering every engine and statistical family.
SCENARIOS = (
    "binomial_cp_fixed_n100_p0.05",
    "binomial_wilson_seq_w0.1_p0.2",
    "sprt_true_p1",
    "dkw_iid_eps0.15",
    "dkw_runner_seq_eps0.15",
    "dkw_runner_seq_kde_explore300",
    "rule_binomial_fixed_strict_n100_p0.05",
    "rule_sprt_wald_true_p0",
)
SEEDS = (20261018, 20261019, 20261020)


def _rounded(value: object) -> object:
    if isinstance(value, float):
        return None if math.isnan(value) else round(value, 9)
    if isinstance(value, (list, tuple)):
        return [_rounded(item) for item in value]
    return value


def build_baseline(tmp: Path) -> dict[str, object]:
    from tools.validation.coverage_experiment import SCENARIOS as ALL, ENGINES, judge

    by_name = {scenario.name: scenario for scenario in ALL}
    baseline = {}
    for name in SCENARIOS:
        scenario = by_name[name]
        for seed in SEEDS:
            result = ENGINES[scenario.engine](scenario, seed, tmp)
            baseline[f"{name}:{seed}"] = {
                key: _rounded(value)
                for key, value in {**result, **judge(scenario, result)}.items()
                if key in {"sample_count", "estimate", "interval", "verdict", "correct"}
            }
    return baseline


def load_baseline() -> dict[str, object]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def write_baseline(baseline: dict[str, object]) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(baseline, indent=1, sort_keys=True) + "\n", encoding="utf-8")
