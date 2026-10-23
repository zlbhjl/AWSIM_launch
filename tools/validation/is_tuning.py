"""Record pilot trials of proposal Q candidates, and fix the chosen Q for final runs.

The user proposes candidates; the tool runs each as a fixed-N pilot comparison
(phase ``pilot``, so its random streams never overlap the final runs), stores
every trial with its settings, fingerprint, diagnostics and cost, ranks them,
and plots the ranking. Choosing is left to the user: ``fix`` writes the final
plan for the trial the user names, with the tuning record as provenance
(docs/sde_importance_sampling_plan.md §4, §10, §13).

    python3 -m tools.validation.is_tuning run \\
        --base-plan configs/dynamics_importance_ou_baseline.yaml \\
        --candidates configs/is_tuning/ou_a_3sd_upper.yaml \\
        --output-dir artifacts/is_tuning/ou_a_3sd_upper --samples 1000 --root-entropy 1
    python3 -m tools.validation.is_tuning fix \\
        --output-dir artifacts/is_tuning/ou_a_3sd_upper --trial q3_affine_b \\
        --plan-out configs/is_final/ou_a_3sd_upper_q3.yaml

Candidates file::

    budget: 10            # optional, at most this many trials (default 10)
    trials:
      - name: q1_piecewise
        noise: {kind: gaussian, mean_rule: {...}}      # or noise_path_q: {...}
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

import yaml

from orchestration.dynamics_importance_sampling import (
    DynamicsImportanceSamplingRunConfig,
    DynamicsImportanceSamplingRunner,
)
from runtime.repository.importance_experiment_manifest import fingerprint
from targets.dynamics.backend import DynamicsBackend, DynamicsBackendConfig
from targets.dynamics.result_interpreter import DynamicsResultInterpreter

DEFAULT_BUDGET = 10
RECORD_NAME = "tuning_record.json"
PROPOSAL_KEYS = ("noise", "noise_path_q")


def load_candidates(path: str | Path) -> tuple[int, list[dict[str, object]]]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping) or not isinstance(raw.get("trials"), list) or not raw["trials"]:
        raise ValueError("candidates file must have a non-empty 'trials' list")
    budget = int(raw.get("budget", DEFAULT_BUDGET))
    trials = [dict(trial) for trial in raw["trials"]]
    if len(trials) > budget:
        raise ValueError(f"{len(trials)} trials exceed the budget of {budget}")
    names = [trial.get("name") for trial in trials]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        raise ValueError("every trial needs a unique name")
    for trial in trials:
        given = [key for key in PROPOSAL_KEYS if trial.get(key) is not None]
        if len(given) > 1:
            raise ValueError(f"trial {trial['name']} must give noise or noise_path_q, not both")
    return budget, trials


def trial_plan(base: Mapping[str, object], trial: Mapping[str, object]) -> dict[str, object]:
    """The base plan with its proposal replaced by the trial's (target unchanged)."""
    plan = copy.deepcopy(dict(base))
    definition = {
        key: value for key, value in dict(plan["proposal"]["definition"]).items() if key not in PROPOSAL_KEYS
    }
    for key in PROPOSAL_KEYS:
        if trial.get(key) is not None:
            definition[key] = copy.deepcopy(trial[key])
    plan["proposal"] = {"id": str(trial["name"]), "mode": "manual_proposal", "definition": definition}
    plan["experiment_id"] = f"{plan['experiment_id']}__{trial['name']}"
    return plan


def run_trials(
    *,
    base_plan: str | Path,
    candidates: str | Path,
    output_dir: str | Path,
    samples: int,
    root_entropy: int,
) -> dict[str, object]:
    base = yaml.safe_load(Path(base_plan).read_text(encoding="utf-8"))
    budget, trials = load_candidates(candidates)
    output = Path(output_dir)
    results = []
    for trial in trials:
        plan = trial_plan(base, trial)
        trial_dir = output / "trials" / str(trial["name"])
        trial_dir.mkdir(parents=True, exist_ok=True)
        plan_path = trial_dir / "plan.yaml"
        plan_path.write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")
        runner = DynamicsImportanceSamplingRunner(
            backend=DynamicsBackend(DynamicsBackendConfig(output_root=trial_dir / "artifacts", artifact_policy="summary")),
            result_interpreter=DynamicsResultInterpreter(),
        )
        started = time.perf_counter()
        comparison = runner.run_fixed_samples(
            DynamicsImportanceSamplingRunConfig(
                sampling_plan_path=plan_path, output_root=trial_dir / "run", max_samples=samples,
                phase="pilot", root_entropy=root_entropy,
            )
        )["comparison"]
        elapsed = time.perf_counter() - started
        results.append(_trial_summary(trial, plan, comparison, elapsed, samples))
    record = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "base_plan": str(base_plan),
        "base_plan_fingerprint": fingerprint(base),
        "candidates": str(candidates),
        "budget": budget,
        "samples_per_trial": samples,
        "phase": "pilot",
        "root_entropy": str(root_entropy),
        "ranking_rule": "complete trials with an observed event, by relative standard error of the IS estimate",
        "trials": results,
        "ranking": _ranking(results),
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / RECORD_NAME).write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _plot(record, output / "tuning_record.png")
    return record


def fix_trial(*, output_dir: str | Path, trial: str, plan_out: str | Path) -> dict[str, object]:
    """Write the final plan of one recorded trial, with the tuning record as provenance."""
    output = Path(output_dir)
    record = json.loads((output / RECORD_NAME).read_text(encoding="utf-8"))
    names = [entry["name"] for entry in record["trials"]]
    if trial not in names:
        raise ValueError(f"trial {trial!r} is not in the record; recorded: {names}")
    entry = record["trials"][names.index(trial)]
    plan = yaml.safe_load((output / "trials" / trial / "plan.yaml").read_text(encoding="utf-8"))
    plan["experiment_id"] = f"{plan['experiment_id']}__final"
    plan["tuning_provenance"] = {
        "record": str(output / RECORD_NAME),
        "trial": trial,
        "proposal_fingerprint": entry["proposal_fingerprint"],
        "pilot_root_entropy": record["root_entropy"],
        "pilot_samples": record["samples_per_trial"],
        "rank": record["ranking"].index(trial) + 1 if trial in record["ranking"] else None,
        "fixed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    Path(plan_out).parent.mkdir(parents=True, exist_ok=True)
    Path(plan_out).write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")
    return plan


def _trial_summary(trial, plan, comparison, elapsed, samples) -> dict[str, object]:
    importance = comparison.get("diagnostic", {}).get("importance", {})
    estimate = importance.get("estimate")
    error = importance.get("standard_error")
    relative = error / estimate if isinstance(error, (int, float)) and estimate else None
    return {
        "name": trial["name"],
        "proposal": plan["proposal"]["definition"],
        "proposal_fingerprint": fingerprint(plan["proposal"]),
        "final_status": comparison.get("final_status"),
        "estimate_is": estimate,
        "estimate_mc": comparison.get("diagnostic", {}).get("ordinary", {}).get("estimate"),
        "standard_error": error,
        "relative_standard_error": relative,
        "diagnostic_interval_width": importance.get("interval_width"),
        "effective_sample_size": importance.get("effective_sample_size"),
        "max_log_importance_weight": importance.get("max_log_importance_weight"),
        "weight_bound": (comparison.get("guarantee", {}).get("hoeffding", {})
                         .get("importance", {}).get("max_importance_weight")),
        "wall_time_sec": elapsed,
        "seconds_per_path": elapsed / (2 * samples),
    }


def _ranking(results: Sequence[Mapping[str, object]]) -> list[str]:
    usable = [
        entry for entry in results
        if entry["final_status"] == "complete" and isinstance(entry["relative_standard_error"], float)
        and math.isfinite(entry["relative_standard_error"])
    ]
    return [entry["name"] for entry in sorted(usable, key=lambda entry: entry["relative_standard_error"])]


def _plot(record: Mapping[str, object], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    trials = record["trials"]
    names = [entry["name"] for entry in trials]
    values = [entry["relative_standard_error"] or float("nan") for entry in trials]
    figure, axis = plt.subplots(figsize=(max(4.0, 0.7 * len(names) + 2.0), 3.2))
    axis.bar(range(len(names)), values, color="#4c72b0")
    axis.set_xticks(range(len(names)), names, rotation=45, ha="right")
    axis.set_ylabel("relative standard error (IS)")
    axis.set_title(f"Pilot trials, {record['samples_per_trial']} paths each")
    figure.tight_layout()
    figure.savefig(path, dpi=120)
    plt.close(figure)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run and record pilot trials")
    run.add_argument("--base-plan", required=True)
    run.add_argument("--candidates", required=True)
    run.add_argument("--output-dir", required=True, type=Path)
    run.add_argument("--samples", type=int, default=1000)
    run.add_argument("--root-entropy", type=int, required=True)
    fix = commands.add_parser("fix", help="write the final plan of a recorded trial")
    fix.add_argument("--output-dir", required=True, type=Path)
    fix.add_argument("--trial", required=True)
    fix.add_argument("--plan-out", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        record = run_trials(base_plan=args.base_plan, candidates=args.candidates, output_dir=args.output_dir,
                            samples=args.samples, root_entropy=args.root_entropy)
        for entry in record["trials"]:
            relative = entry["relative_standard_error"]
            print(f"{entry['name']:24s} {entry['final_status']:10s} rel.se="
                  f"{'n/a' if relative is None else f'{relative:.4f}'} ess={entry['effective_sample_size']}")
        print("ranking:", ", ".join(record["ranking"]))
    else:
        fix_trial(output_dir=args.output_dir, trial=args.trial, plan_out=args.plan_out)
        print(f"wrote {args.plan_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
