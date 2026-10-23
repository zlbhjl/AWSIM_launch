"""Compare repeated fixed-N importance-sampling runs with an independent reference.

For models without a closed form (model C, the U-turn) the coverage of an
interval cannot be measured. Instead each final plan is run ``--runs`` times
with independent random roots (phase ``final``), and the estimates and
intervals are reported next to the reference estimate and its own
uncertainty (docs/sde_importance_sampling_plan.md §13).

    python3 -m tools.validation.compare_to_reference \\
        --plans configs/is_final_reference/nonlinear2d_max_1p5_q0.yaml ... \\
        --reference artifacts/model_c/reference.json \\
        --samples 2000 --runs 10 --output artifacts/model_c/comparison.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from multiprocessing import Pool
from pathlib import Path

from targets.dynamics.importance_sampling import (
    DynamicsImportanceSamplingRunConfig,
    DynamicsImportanceSamplingRunner,
)
from targets.dynamics.backend import DynamicsBackend, DynamicsBackendConfig
from targets.dynamics.result_interpreter import DynamicsResultInterpreter


def run_once(task: tuple[str, int, int]) -> dict[str, object]:
    plan, samples, root = task
    with tempfile.TemporaryDirectory(prefix="reference-") as tmp:
        runner = DynamicsImportanceSamplingRunner(
            backend=DynamicsBackend(DynamicsBackendConfig(output_root=Path(tmp) / "a", artifact_policy="summary")),
            result_interpreter=DynamicsResultInterpreter(),
        )
        started = time.perf_counter()
        comparison = runner.run_fixed_samples(DynamicsImportanceSamplingRunConfig(
            sampling_plan_path=plan, output_root=Path(tmp) / "run", max_samples=samples,
            root_entropy=root, phase="final",
        ))["comparison"]
        elapsed = time.perf_counter() - started
    diagnostic = comparison.get("diagnostic", {})
    guarantee = comparison.get("guarantee", {})

    def interval(block):
        if not isinstance(block, dict) or block.get("lower_bound") is None:
            return None
        return [block["lower_bound"], block["upper_bound"]]

    return {
        "plan": plan, "root": root, "final_status": comparison.get("final_status"),
        "estimate_mc": diagnostic.get("ordinary", {}).get("estimate"),
        "interval_mc": interval(diagnostic.get("ordinary")),
        "estimate_is": diagnostic.get("importance", {}).get("estimate"),
        "interval_is": interval(diagnostic.get("importance")),
        "interval_is_bernstein": interval(guarantee.get("empirical_bernstein", {}).get("importance")),
        "ess": diagnostic.get("importance", {}).get("effective_sample_size"),
        "seconds_per_path": elapsed / (2 * samples),
    }


def summarise(runs: list[dict[str, object]], reference: dict[str, object]) -> dict[str, object]:
    low, high = reference["ci95_clopper_pearson"]
    result = {}
    for plan in sorted({run["plan"] for run in runs}):
        rows = [run for run in runs if run["plan"] == plan]
        entry = {"runs": len(rows), "complete": sum(row["final_status"] == "complete" for row in rows)}
        for kind in ("mc", "is"):
            estimates = [row[f"estimate_{kind}"] for row in rows if row[f"estimate_{kind}"] is not None]
            intervals = [row[f"interval_{kind}"] for row in rows]
            entry[kind] = {
                "mean": statistics.fmean(estimates) if estimates else None,
                "std": statistics.stdev(estimates) if len(estimates) > 1 else None,
                "relative_std_to_reference": (statistics.stdev(estimates) / reference["estimate"]
                                              if len(estimates) > 1 else None),
                # An interval "agrees" when it overlaps the reference's own 95% interval.
                "intervals_overlapping_reference": sum(
                    1 for item in intervals if item is not None and item[0] <= high and item[1] >= low),
                "intervals_missing": sum(1 for item in intervals if item is None),
            }
        entry["mean_seconds_per_path"] = statistics.fmean(row["seconds_per_path"] for row in rows)
        result[plan] = entry
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--plans", nargs="+", required=True)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=2000)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--seed-base", type=int, default=20261201)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    tasks = [(plan, args.samples, args.seed_base + run) for plan in args.plans for run in range(args.runs)]
    with Pool(processes=args.workers) as pool:
        runs = pool.map(run_once, tasks)
    summary = summarise(runs, reference)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(
        {"reference": reference, "samples": args.samples, "runs_per_plan": args.runs,
         "seed_base": args.seed_base, "summary": summary, "runs": runs}, indent=2) + "\n", encoding="utf-8")
    for plan, entry in summary.items():
        print(Path(plan).name, json.dumps({key: entry[key] for key in ("complete", "mc", "is")}, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
