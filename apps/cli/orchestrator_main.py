from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Sequence

from apps.cli.worker_main import LEGACY_MODES, parse_param_assignments
from orchestration.orchestrator import Orchestrator, OrchestratorConfig, build_task_payload
from runtime.container.profile import SUPPORTED_CONTAINER_PROFILES
from runtime.cluster.ray_queue import TaskQueue
from targets.awsim.case_kinds import (
    SUPPORTED_SCENARIO_PROFILES,
    build_default_case_kind_module_name,
    load_focus_points,
)
from targets.statistical import load_statistical_target_profile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the v2 orchestrator locally for queued target tasks."
    )
    parser.add_argument(
        "--fixture",
        default=None,
        help="Path to a local target fixture.",
    )
    parser.add_argument(
        "--param",
        dest="params",
        action="append",
        default=[],
        help="Direct simulation parameter in key=value form. Repeat this option to pass multiple values.",
    )
    parser.add_argument(
        "--cluster",
        action="store_true",
        help="Dispatch this invocation to the cluster-oriented v2 orchestrator entrypoint.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to the JSONL file where EvaluationRecord rows are appended.",
    )
    parser.add_argument("--case-id", default=None, help="Override case id. Defaults to fixture stem.")
    parser.add_argument("--case-kind", default="uturn", help="Case kind label.")
    parser.add_argument(
        "--mode",
        choices=LEGACY_MODES,
        default="explore",
        help="Strategy mode label. Active strategy currently supports explore/focus/margin/jama_edge/ttc_edge/worst_ttc/dkw/dkw_fixed/verify_consistency/binomial_ci/boundary_gap/sprt/ebstop.",
    )
    parser.add_argument(
        "--target",
        default="awsim",
        choices=["awsim", "bbsl", "dynamics", "prism"],
        help="Target name for the worker.",
    )
    parser.add_argument(
        "--dynamics-output-root",
        default=None,
        help="Optional directory for target=dynamics raw trajectory artifacts.",
    )
    parser.add_argument(
        "--dynamics-input-distribution",
        default=None,
        help="JSON uniform input distributions for target=dynamics statistical sampling.",
    )
    parser.add_argument(
        "--dynamics-decision-mode",
        choices=["judgment", "screening"],
        default="judgment",
        help=(
            "target=dynamics binary metric for binomial_ci/sprt: judgment=c_collision "
            "(AWSIM substitute), screening=c_screening_candidate (AWSIM re-validation candidates)."
        ),
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Deterministic seed for target=dynamics statistical sampling.",
    )
    parser.add_argument("--worker-id", default="worker_v2_local", help="Worker identifier.")
    parser.add_argument(
        "--experiment-id",
        default=None,
        help="Sampling batch identifier used to keep statistical samples separate.",
    )
    parser.add_argument("--reason", default="manual_orchestrator_run", help="Task reason label.")
    parser.add_argument(
        "--tag",
        dest="tags",
        action="append",
        default=[],
        help="Task tag. Repeat this option to add multiple tags.",
    )
    parser.add_argument(
        "--config-module",
        default=None,
        help="Rule spec module used by the AWSIM result interpreter.",
    )
    parser.add_argument(
        "--focus_points",
        default=None,
        help="Legacy JSON string for focus mode points. When omitted in focus mode, FOCUS_POINTS is loaded from the case-kind module.",
    )
    parser.add_argument(
        "--ext_mode",
        default="cvm",
        help="Kinematics extractor mode forwarded to worker/backend execution.",
    )
    parser.add_argument(
        "--path-root",
        default=None,
        help="Optional root used when relativizing evidence paths on save.",
    )
    parser.add_argument(
        "--dataset-csv",
        default=None,
        help="Optional dataset CSV path for shared-store style merged output.",
    )
    parser.add_argument(
        "--replay-csv",
        default=None,
        help=(
            "Source dataset CSV for replay mode. Only successful BINOMIAL_CI rows "
            "with a binary collision result are replayed."
        ),
    )
    parser.add_argument(
        "--replay-reason-pattern",
        default="BINOMIAL_CI:",
        help="Literal source reason text required for replay eligibility.",
    )
    parser.add_argument(
        "--replay-expected-count",
        type=int,
        default=None,
        help="Fail before dispatch when the eligible replay row count differs.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help=(
            "Cluster trace directory suffix. Required in replay mode so source "
            "and replay artifacts cannot overwrite each other."
        ),
    )
    parser.add_argument(
        "--history-path",
        default=None,
        help="Optional processed-loop history path forwarded to the worker.",
    )
    parser.add_argument(
        "--queue-actor-name",
        default="local_task_queue",
        help="Queue actor name forwarded to queue-mode workers.",
    )
    parser.add_argument(
        "--queue-namespace",
        default=None,
        help="Optional Ray namespace used when resolving the queue actor.",
    )
    parser.add_argument(
        "--queue-address",
        default=None,
        help="Optional Ray address used when resolving queue/shared actors.",
    )
    parser.add_argument(
        "--shared-store-actor-name",
        default=None,
        help="Optional detached shared-store actor name forwarded to v2 workers.",
    )
    parser.add_argument(
        "--shared-store-namespace",
        default=None,
        help="Optional Ray namespace used when resolving the shared-store actor.",
    )
    parser.add_argument(
        "--shared-store-address",
        default=None,
        help="Optional Ray address used when resolving the shared-store actor.",
    )
    parser.add_argument(
        "--dkw-bounds",
        "--dkw_bounds",
        dest="dkw_bounds",
        default=None,
        help="Optional JSON string defining the bounded region for DKW/binomial evaluation.",
    )
    parser.add_argument(
        "--dkw-region",
        "--dkw_region",
        dest="dkw_region",
        default="custom",
        help="Optional extractor region label for DKW/binomial evaluation.",
    )
    parser.add_argument(
        "--dkw-pure-smc",
        "--dkw_pure_smc",
        dest="dkw_pure_smc",
        action="store_true",
        help="Evaluate DKW using only SMC-tagged samples instead of the merged dataset.",
    )
    parser.add_argument(
        "--dkw-simultaneous",
        "--dkw_simultaneous",
        dest="dkw_simultaneous",
        action="store_true",
        help="Evaluate multiple DKW metrics with Bonferroni-style simultaneous guarantees.",
    )
    parser.add_argument(
        "--refresh-interval",
        type=int,
        default=None,
        help="Optional worker refresh policy forwarded to queue-mode worker runs.",
    )
    parser.add_argument(
        "--worker-queue-empty-wait-timeout-sec",
        type=float,
        default=300.0,
        help="Seconds worker containers keep polling when the shared queue is temporarily empty.",
    )
    parser.add_argument(
        "--worker-queue-empty-wait-interval-sec",
        type=float,
        default=5.0,
        help="Seconds between empty shared queue polling attempts in worker containers.",
    )
    parser.add_argument(
        "--worker-queue-heartbeat-interval-sec",
        type=float,
        default=60.0,
        help="Seconds between worker status heartbeat updates during a running case.",
    )
    parser.add_argument(
        "--max-strategy-cases",
        type=int,
        default=None,
        help="Optional limit for how many strategy-generated cases to enqueue in one orchestrator run.",
    )
    parser.add_argument(
        "--max-samples",
        "--max_samples",
        dest="max_samples",
        type=int,
        default=None,
        help="Optional statistical sampling cap used by dkw_fixed/binomial_ci modes.",
    )
    parser.add_argument(
        "--resume-from",
        default=None,
        help="Optional directory containing a past <case_kind>_dataset.csv to restore.",
    )
    parser.add_argument(
        "--resume-current-only",
        action="store_true",
        help="When resuming, compute the next loop number from the current dataset only.",
    )
    parser.add_argument(
        "--scenario-type",
        default=None,
        help="Optional scenario type for direct simulation mode. Defaults to case-kind.",
    )
    parser.add_argument(
        "--simulation-output-dir",
        default=None,
        help="Optional output directory used by the AWSIM backend in direct simulation mode.",
    )
    parser.add_argument(
        "--expected-trace-path",
        default=None,
        help="Optional explicit trace JSON path expected from direct simulation mode.",
    )
    parser.add_argument(
        "--local-loop-num",
        type=int,
        default=None,
        help="Optional local loop number used when naming direct simulation outputs.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Forward Xvfb-based headless execution to the v2 worker backend.",
    )
    parser.add_argument(
        "--container-profile",
        choices=SUPPORTED_CONTAINER_PROFILES,
        default=None,
        help="Optional named container runtime profile (for example: legacy, autoware171, autoware180, autoware190).",
    )
    parser.add_argument(
        "--scenario-profile",
        choices=SUPPORTED_SCENARIO_PROFILES,
        default=None,
        help="Optional named scenario spec profile (for example: legacy, autoware171).",
    )
    parser.add_argument(
        "--no-sync-awsim-launch",
        dest="sync_awsim_launch",
        action="store_false",
        default=True,
        help="Cluster mode only. Disable the default rsync of ~/AWSIM_launch to remote worker nodes.",
    )
    parser.add_argument(
        "--sync-awsim-script-py",
        action="store_true",
        help="Cluster mode only. Also rsync ~/AWSIMScriptPy to remote worker nodes.",
    )
    runtime_monitor_sync_group = parser.add_mutually_exclusive_group()
    runtime_monitor_sync_group.add_argument(
        "--sync-aw-runtime-monitor",
        dest="sync_aw_runtime_monitor",
        action="store_true",
        default=True,
        help=(
            "Cluster mode only. Rsync and validate ~/AW-Runtime-Monitor on remote "
            "worker nodes (enabled by default)."
        ),
    )
    runtime_monitor_sync_group.add_argument(
        "--no-sync-aw-runtime-monitor",
        dest="sync_aw_runtime_monitor",
        action="store_false",
        help=(
            "Cluster mode only. Disable the default AW-Runtime-Monitor sync and "
            "validation."
        ),
    )
    parser.add_argument(
        "--sync-autoware180-map",
        action="store_true",
        help="Cluster mode only. Also rsync ~/autoware180_runtime/maps to remote worker nodes.",
    )
    parser.add_argument(
        "--sync-autoware190-map",
        action="store_true",
        help="Cluster mode only. Also rsync ~/autoware190_runtime/maps to remote worker nodes.",
    )
    parser.add_argument(
        "--with-host-worker",
        action="store_true",
        help="Start a host-side v2 worker process alongside orchestrator control.",
    )
    parser.add_argument(
        "--headless-host",
        action="store_true",
        help="Run the host-side worker in headless mode.",
    )
    parser.add_argument(
        "--worker-count",
        type=int,
        default=None,
        help="Worker count used when deriving queue refill watermarks.",
    )
    parser.add_argument(
        "--worker-launch-stagger-sec",
        type=float,
        default=20.0,
        help="Cluster mode only. Seconds to wait between worker container launches.",
    )
    parser.add_argument(
        "--worker-queue-connect-retries",
        type=int,
        default=6,
        help="Cluster mode only. ray.init retry attempts passed to queue workers.",
    )
    parser.add_argument(
        "--worker-queue-connect-retry-interval-sec",
        type=float,
        default=15.0,
        help="Cluster mode only. Seconds between queue worker ray.init retries.",
    )
    parser.add_argument(
        "--auto-restart-stale-workers",
        action="store_true",
        help=(
            "Cluster mode only. Restart a stale worker container only when docker "
            "inspect confirms that the container is stopped or missing."
        ),
    )
    parser.add_argument(
        "--auto-restart-missing-workers",
        action="store_true",
        help=(
            "Cluster mode only. Also restart enabled worker containers that exited "
            "with code 1 before registering in the Ray queue."
        ),
    )
    parser.add_argument(
        "--auto-restart-gpu-workers",
        action="store_true",
        help=(
            "Cluster mode only. Quarantine and recreate worker containers when "
            "their periodic nvidia-smi probe fails."
        ),
    )
    parser.add_argument(
        "--stale-worker-timeout-sec",
        type=float,
        default=600.0,
        help="Cluster mode only. Seconds before a worker status is considered stale.",
    )
    parser.add_argument(
        "--stale-worker-check-interval-loops",
        type=int,
        default=5,
        help="Cluster mode only. Poll loops between stale worker restart checks.",
    )
    parser.add_argument(
        "--stale-worker-restart-cooldown-sec",
        type=float,
        default=600.0,
        help="Cluster mode only. Minimum seconds between restarts for the same worker.",
    )
    parser.add_argument(
        "--worker-restart-max-count",
        type=int,
        default=2,
        help="Cluster mode only. Maximum automatic worker container restarts per node.",
    )
    parser.add_argument(
        "--worker-restart-log-tail-lines",
        type=int,
        default=100,
        help="Cluster mode only. docker logs lines captured before an automatic restart.",
    )
    parser.add_argument(
        "--gpu-worker-max-restarts-per-24h",
        type=int,
        default=5,
        help="Cluster mode only. Maximum GPU recovery recreations per node in 24 hours.",
    )
    parser.add_argument(
        "--gpu-worker-max-consecutive-failures",
        type=int,
        default=3,
        help="Cluster mode only. Failed GPU recreations before terminal quarantine.",
    )
    parser.add_argument(
        "--gpu-worker-stable-reset-sec",
        type=float,
        default=1800.0,
        help="Cluster mode only. Healthy duration that resets consecutive GPU failures.",
    )
    parser.add_argument(
        "--gpu-worker-probe-timeout-sec",
        type=float,
        default=5.0,
        help="Cluster mode only. Timeout for each host/container nvidia-smi probe.",
    )
    parser.add_argument(
        "--cache-size",
        type=int,
        default=None,
        help="Optional strategy cache size override. Defaults to the legacy worker_count * 2 policy.",
    )
    parser.add_argument(
        "--queue-high-water",
        type=int,
        default=None,
        help="Explicit queue high-water size. Defaults to worker_count * 4.",
    )
    parser.add_argument(
        "--queue-low-water",
        type=int,
        default=None,
        help="Explicit queue low-water size. Defaults to worker_count * 2.",
    )
    parser.add_argument(
        "--run-external-workers",
        action="store_true",
        help="Do not spawn an inline worker; orchestrator only manages queue/refill/stop control.",
    )
    parser.add_argument(
        "--poll-interval-sec",
        type=float,
        default=2.0,
        help="Polling interval used when waiting for external workers.",
    )
    parser.add_argument(
        "--binomial-target",
        "--binomial_target",
        dest="binomial_target",
        default="c_collision",
        help="Binary metric column used in binomial_ci mode.",
    )
    parser.add_argument(
        "--binomial-method",
        "--binomial_method",
        dest="binomial_method",
        choices=["wilson", "clopper-pearson"],
        default="wilson",
        help=(
            "Confidence interval method used in binomial_ci mode. "
            "'wilson' is a CLT approximation whose actual coverage can fall "
            "below the nominal confidence level for small n or extreme p; "
            "use 'clopper-pearson' (exact, never undershoots nominal coverage) "
            "for safety-critical decisions."
        ),
    )
    parser.add_argument(
        "--binomial-confidence",
        "--binomial_confidence",
        dest="binomial_confidence",
        type=float,
        default=0.95,
        help="Confidence level used in binomial_ci mode.",
    )
    parser.add_argument(
        "--binomial-target-width",
        "--binomial_target_width",
        dest="binomial_target_width",
        type=float,
        default=0.02,
        help="Target interval width used in binomial_ci mode.",
    )
    parser.add_argument(
        "--binomial-min-samples",
        "--binomial_min_samples",
        dest="binomial_min_samples",
        type=int,
        default=None,
        help="Optional minimum sample count before binomial_ci may stop early.",
    )
    parser.add_argument(
        "--binomial-anytime-valid",
        "--binomial_anytime_valid",
        dest="binomial_anytime_valid",
        action="store_true",
        help=(
            "Opt-in fix for binomial_ci's repeated-peeking problem: build each "
            "interval with a per-sample-count alpha budget (same union-bound "
            "schedule as ebstop, evaluation/alpha_spending.py) instead of a "
            "fixed confidence, so it stays valid no matter how many times you "
            "check. Costs roughly 5-8x more samples to reach the same target "
            "width; off by default."
        ),
    )
    parser.add_argument(
        "--sprt-target",
        "--sprt_target",
        dest="sprt_target",
        default="c_collision",
        help="Binary metric column used in sprt mode.",
    )
    parser.add_argument(
        "--sprt-p0",
        "--sprt_p0",
        dest="sprt_p0",
        type=float,
        default=0.1,
        help="H0 hypothesis probability value used in sprt mode.",
    )
    parser.add_argument(
        "--sprt-p1",
        "--sprt_p1",
        dest="sprt_p1",
        type=float,
        default=0.01,
        help="H1 hypothesis probability value used in sprt mode.",
    )
    parser.add_argument(
        "--sprt-beta",
        "--sprt_beta",
        dest="sprt_beta",
        type=float,
        default=0.05,
        help="Type II error (beta) used in sprt mode.",
    )
    parser.add_argument(
        "--sprt-confidence",
        "--sprt_confidence",
        dest="sprt_confidence",
        type=float,
        default=0.95,
        help="1 - alpha (Type I error) used in sprt mode.",
    )
    parser.add_argument(
        "--sprt-min-samples",
        "--sprt_min_samples",
        dest="sprt_min_samples",
        type=int,
        default=None,
        help="Optional minimum sample count before sprt may stop early.",
    )
    parser.add_argument(
        "--ebstop-target",
        "--ebstop_target",
        dest="ebstop_target",
        default="min_ttc",
        help="Bounded, continuous-valued metric column used in ebstop mode.",
    )
    parser.add_argument(
        "--ebstop-epsilon",
        "--ebstop_epsilon",
        dest="ebstop_epsilon",
        type=float,
        default=0.1,
        help="Target relative error (|estimate-true| <= epsilon*|true|) used in ebstop mode.",
    )
    parser.add_argument(
        "--ebstop-value-range-min",
        "--ebstop_value_range_min",
        dest="ebstop_value_range_min",
        type=float,
        default=0.0,
        help="Known lower bound of the metric's value range, used in ebstop mode.",
    )
    parser.add_argument(
        "--ebstop-value-range-max",
        "--ebstop_value_range_max",
        dest="ebstop_value_range_max",
        type=float,
        default=10.0,
        help="Known upper bound of the metric's value range, used in ebstop mode.",
    )
    parser.add_argument(
        "--ebstop-confidence",
        "--ebstop_confidence",
        dest="ebstop_confidence",
        type=float,
        default=0.95,
        help="1 - delta (failure probability) used in ebstop mode.",
    )
    parser.add_argument(
        "--ebstop-max-samples",
        "--ebstop_max_samples",
        dest="ebstop_max_samples",
        type=int,
        default=2000,
        help="Maximum sample count before ebstop stops without convergence.",
    )
    parser.add_argument(
        "--prism-dkw-target-metric",
        "--prism_dkw_target_metric",
        dest="prism_dkw_target_metric",
        default="steps_to_failure_capped",
        help="Continuous-valued metric column used by target=prism dkw/dkw_fixed mode.",
    )
    parser.add_argument(
        "--prism-dkw-confidence",
        "--prism_dkw_confidence",
        dest="prism_dkw_confidence",
        type=float,
        default=0.95,
        help="1 - total_delta used by target=prism dkw/dkw_fixed mode.",
    )
    parser.add_argument(
        "--prism-dkw-target-epsilon",
        "--prism_dkw_target_epsilon",
        dest="prism_dkw_target_epsilon",
        type=float,
        default=0.15,
        help="Target DKW interval width used by target=prism dkw/dkw_fixed mode.",
    )
    parser.add_argument(
        "--prism-dkw-base-samples",
        "--prism_dkw_base_samples",
        dest="prism_dkw_base_samples",
        type=int,
        default=20,
        help="Stage-1 sample count for target=prism dkw's stage-based schedule.",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    fixture_mode = bool(getattr(args, "fixture", None))
    simulation_mode = bool(getattr(args, "params", []))
    replay_mode = bool(getattr(args, "replay_csv", None))
    strategy_mode = not fixture_mode and not simulation_mode and not replay_mode

    if sum((fixture_mode, simulation_mode, replay_mode)) > 1:
        raise ValueError(
            "Specify only one input source: --fixture, --param key=value, or --replay-csv"
        )
    if replay_mode and getattr(args, "mode", None) != "replay":
        raise ValueError("--replay-csv requires --mode replay")
    if getattr(args, "mode", None) == "replay" and not replay_mode:
        raise ValueError("--mode replay requires --replay-csv")
    if replay_mode and not getattr(args, "run_id", None):
        raise ValueError("--run-id is required in replay mode")
    run_id = getattr(args, "run_id", None)
    if run_id and re.fullmatch(r"[A-Za-z0-9_.-]+", str(run_id)) is None:
        raise ValueError("--run-id may contain only letters, numbers, '.', '_' and '-'")
    replay_expected_count = getattr(args, "replay_expected_count", None)
    if replay_expected_count is not None and replay_expected_count <= 0:
        raise ValueError("--replay-expected-count must be a positive integer")
    if replay_mode and not str(getattr(args, "replay_reason_pattern", "")).strip():
        raise ValueError("--replay-reason-pattern must not be empty")
    if replay_mode and getattr(args, "target", "awsim") != "awsim":
        raise ValueError("--replay-csv is only supported for target=awsim")
    if replay_mode and getattr(args, "dataset_csv", None):
        source = Path(str(args.replay_csv)).expanduser().resolve()
        destination = Path(str(args.dataset_csv)).expanduser().resolve()
        if source == destination:
            raise ValueError("--replay-csv and --dataset-csv must be different files")
    dynamics_statistical_mode = (
        getattr(args, "target", "awsim") == "dynamics"
        and getattr(args, "mode", "explore")
        in {"binomial_ci", "sprt", "dkw", "dkw_fixed", "ebstop"}
    )
    if (
        strategy_mode
        and getattr(args, "target", "awsim") != "awsim"
        and not dynamics_statistical_mode
    ):
        raise ValueError("Strategy mode without --fixture/--param is only supported for target=awsim")
    if getattr(args, "target", "awsim") == "dynamics" and getattr(args, "case_kind", "uturn") != "uturn":
        raise ValueError("target=dynamics currently supports only --case-kind uturn")
    if dynamics_statistical_mode and getattr(args, "max_samples", None) is None:
        raise ValueError("dynamics statistical sampling requires --max-samples")
    if dynamics_statistical_mode and getattr(args, "dataset_csv", None) is None:
        raise ValueError("dynamics statistical sampling requires --dataset-csv")
    prism_sampling_mode = (
        getattr(args, "target", "awsim") == "prism"
        and simulation_mode
        and getattr(args, "mode", "explore")
        in {"binomial_ci", "sprt", "ebstop", "dkw", "dkw_fixed"}
    )
    if prism_sampling_mode and getattr(args, "max_samples", None) is None:
        raise ValueError("PRISM binomial sampling requires --max-samples")
    if prism_sampling_mode and getattr(args, "dataset_csv", None) is None:
        raise ValueError("PRISM binomial sampling requires --dataset-csv")
    prism_supported_modes = {
        "explore",
        "binomial_ci",
        "sprt",
        "ebstop",
        "dkw",
        "dkw_fixed",
    }
    if (
        getattr(args, "target", "awsim") == "prism"
        and simulation_mode
        and getattr(args, "mode", "explore") not in prism_supported_modes
    ):
        raise ValueError(
            "target=prism with --param only supports "
            f"--mode {'/'.join(sorted(prism_supported_modes))}; "
            f"'{args.mode}' has no live PRISM execution path yet "
            "(orchestration/orchestrator.py::_build_strategy has no branch for it, "
            "so it would silently fall through to a single one-off case)"
        )
    if (strategy_mode or replay_mode) and getattr(args, "dataset_csv", None) is None:
        raise ValueError("--dataset-csv is required for AWSIM strategy mode")
    if strategy_mode and getattr(args, "mode", "explore") not in {
        "explore",
        "focus",
        "margin",
        "jama_edge",
        "ttc_edge",
        "worst_ttc",
        "dkw",
        "dkw_fixed",
        "verify_consistency",
        "binomial_ci",
        "boundary_gap",
        "replay",
        "sprt",
        "ebstop",
    }:
        raise ValueError(
            "AWSIM strategy mode currently supports "
            "--mode explore/focus/margin/jama_edge/ttc_edge/worst_ttc/dkw/dkw_fixed/verify_consistency/binomial_ci/boundary_gap/replay/sprt/ebstop only"
        )
    if getattr(args, "target", "awsim") != "awsim" and getattr(args, "headless", False):
        raise ValueError("--headless is only supported for target=awsim")
    refresh_interval = getattr(args, "refresh_interval", None)
    if refresh_interval is not None and refresh_interval <= 0:
        raise ValueError("--refresh-interval must be a positive integer")
    max_strategy_cases = getattr(args, "max_strategy_cases", None)
    if max_strategy_cases is not None and max_strategy_cases <= 0:
        raise ValueError("--max-strategy-cases must be a positive integer")
    worker_count = getattr(args, "worker_count", None)
    if worker_count is not None and worker_count <= 0:
        raise ValueError("--worker-count must be a positive integer")
    worker_launch_stagger_sec = getattr(args, "worker_launch_stagger_sec", 20.0)
    if worker_launch_stagger_sec is not None and float(worker_launch_stagger_sec) < 0.0:
        raise ValueError("--worker-launch-stagger-sec must be non-negative")
    worker_queue_connect_retries = getattr(args, "worker_queue_connect_retries", 6)
    if worker_queue_connect_retries is not None and int(worker_queue_connect_retries) <= 0:
        raise ValueError("--worker-queue-connect-retries must be a positive integer")
    worker_queue_connect_retry_interval_sec = getattr(
        args,
        "worker_queue_connect_retry_interval_sec",
        15.0,
    )
    if (
        worker_queue_connect_retry_interval_sec is not None
        and float(worker_queue_connect_retry_interval_sec) < 0.0
    ):
        raise ValueError("--worker-queue-connect-retry-interval-sec must be non-negative")
    worker_queue_empty_wait_timeout_sec = getattr(
        args,
        "worker_queue_empty_wait_timeout_sec",
        300.0,
    )
    if (
        worker_queue_empty_wait_timeout_sec is not None
        and float(worker_queue_empty_wait_timeout_sec) < 0.0
    ):
        raise ValueError("--worker-queue-empty-wait-timeout-sec must be non-negative")
    worker_queue_empty_wait_interval_sec = getattr(
        args,
        "worker_queue_empty_wait_interval_sec",
        5.0,
    )
    if (
        worker_queue_empty_wait_interval_sec is not None
        and float(worker_queue_empty_wait_interval_sec) < 0.0
    ):
        raise ValueError("--worker-queue-empty-wait-interval-sec must be non-negative")
    worker_queue_heartbeat_interval_sec = getattr(
        args,
        "worker_queue_heartbeat_interval_sec",
        60.0,
    )
    if (
        worker_queue_heartbeat_interval_sec is not None
        and float(worker_queue_heartbeat_interval_sec) < 0.0
    ):
        raise ValueError("--worker-queue-heartbeat-interval-sec must be non-negative")
    stale_worker_timeout_sec = getattr(args, "stale_worker_timeout_sec", 600.0)
    if stale_worker_timeout_sec is not None and float(stale_worker_timeout_sec) < 0.0:
        raise ValueError("--stale-worker-timeout-sec must be non-negative")
    stale_worker_check_interval_loops = getattr(
        args,
        "stale_worker_check_interval_loops",
        5,
    )
    if (
        stale_worker_check_interval_loops is not None
        and int(stale_worker_check_interval_loops) <= 0
    ):
        raise ValueError("--stale-worker-check-interval-loops must be a positive integer")
    stale_worker_restart_cooldown_sec = getattr(
        args,
        "stale_worker_restart_cooldown_sec",
        600.0,
    )
    if (
        stale_worker_restart_cooldown_sec is not None
        and float(stale_worker_restart_cooldown_sec) < 0.0
    ):
        raise ValueError("--stale-worker-restart-cooldown-sec must be non-negative")
    worker_restart_max_count = getattr(args, "worker_restart_max_count", 2)
    if worker_restart_max_count is not None and int(worker_restart_max_count) < 0:
        raise ValueError("--worker-restart-max-count must be non-negative")
    worker_restart_log_tail_lines = getattr(args, "worker_restart_log_tail_lines", 100)
    if worker_restart_log_tail_lines is not None and int(worker_restart_log_tail_lines) < 0:
        raise ValueError("--worker-restart-log-tail-lines must be non-negative")
    gpu_worker_max_restarts_per_24h = getattr(args, "gpu_worker_max_restarts_per_24h", 5)
    if int(gpu_worker_max_restarts_per_24h) <= 0:
        raise ValueError("--gpu-worker-max-restarts-per-24h must be positive")
    gpu_worker_max_consecutive_failures = getattr(
        args, "gpu_worker_max_consecutive_failures", 3
    )
    if int(gpu_worker_max_consecutive_failures) <= 0:
        raise ValueError("--gpu-worker-max-consecutive-failures must be positive")
    gpu_worker_stable_reset_sec = getattr(args, "gpu_worker_stable_reset_sec", 1800.0)
    if float(gpu_worker_stable_reset_sec) < 0.0:
        raise ValueError("--gpu-worker-stable-reset-sec must be non-negative")
    gpu_worker_probe_timeout_sec = getattr(args, "gpu_worker_probe_timeout_sec", 5.0)
    if float(gpu_worker_probe_timeout_sec) <= 0.0:
        raise ValueError("--gpu-worker-probe-timeout-sec must be positive")
    cache_size = getattr(args, "cache_size", None)
    if cache_size is not None and cache_size <= 0:
        raise ValueError("--cache-size must be a positive integer")
    queue_high_water = getattr(args, "queue_high_water", None)
    if queue_high_water is not None and queue_high_water <= 0:
        raise ValueError("--queue-high-water must be a positive integer")
    queue_low_water = getattr(args, "queue_low_water", None)
    if queue_low_water is not None and queue_low_water < 0:
        raise ValueError("--queue-low-water must be non-negative")
    poll_interval_sec = getattr(args, "poll_interval_sec", 2.0)
    if poll_interval_sec is not None and float(poll_interval_sec) < 0.0:
        raise ValueError("--poll-interval-sec must be non-negative")
    max_samples = getattr(args, "max_samples", None)
    if max_samples is not None and max_samples <= 0:
        raise ValueError("--max-samples must be a positive integer")
    binomial_confidence = getattr(args, "binomial_confidence", 0.95)
    if not 0.0 < float(binomial_confidence) < 1.0:
        raise ValueError("--binomial-confidence must satisfy 0.0 < value < 1.0")
    binomial_target_width = getattr(args, "binomial_target_width", 0.02)
    if binomial_target_width is not None and float(binomial_target_width) < 0.0:
        raise ValueError("--binomial-target-width must be non-negative")
    binomial_min_samples = getattr(args, "binomial_min_samples", None)
    if binomial_min_samples is not None and binomial_min_samples < 0:
        raise ValueError("--binomial-min-samples must be non-negative")
    if getattr(args, "target", "awsim") != "awsim" and any(
        [
            getattr(args, "scenario_type", None) is not None,
            getattr(args, "simulation_output_dir", None) is not None,
            getattr(args, "expected_trace_path", None) is not None,
            getattr(args, "local_loop_num", None) is not None,
            getattr(args, "focus_points", None) is not None,
        ]
    ):
        raise ValueError("AWSIM-only direct execution options are not supported for non-awsim targets")


def normalize_args(
    args: argparse.Namespace,
    *,
    argv: Sequence[str] | None = None,
) -> argparse.Namespace:
    raw_argv = list(argv) if argv is not None else []
    explicit_config_module = "--config-module" in raw_argv
    explicit_focus_points = "--focus_points" in raw_argv
    explicit_dkw_bounds = any(flag in raw_argv for flag in ("--dkw-bounds", "--dkw_bounds"))

    if getattr(args, "target", "awsim") != "awsim":
        if (
            getattr(args, "target", "") == "prism"
            and getattr(args, "mode", "") == "binomial_ci"
            and getattr(args, "binomial_target", "c_collision") == "c_collision"
        ):
            args.binomial_target = load_statistical_target_profile(
                "prism"
            ).binary_metric
        if getattr(args, "config_module", None) is None:
            args.config_module = (
                "scenario_specs.uturn"
                if getattr(args, "target", "") == "dynamics"
                else f"targets.awsim.case_kinds.{args.case_kind}"
            )
        if (
            getattr(args, "target", "") == "dynamics"
            and getattr(args, "mode", "explore")
            in {"binomial_ci", "sprt", "dkw", "dkw_fixed", "ebstop"}
        ):
            from orchestration.random_parameter_sampling import normalize_input_distribution
            from scenario_specs.uturn import PARAM_RANGES

            raw_distribution = getattr(args, "dynamics_input_distribution", None)
            if raw_distribution is None:
                fixed = parse_param_assignments(getattr(args, "params", []))
                raw_distribution = {
                    key: {"distribution": "uniform", "min": lower, "max": upper}
                    for key, (lower, upper) in PARAM_RANGES.items()
                    if key not in fixed
                }
            else:
                try:
                    raw_distribution = json.loads(raw_distribution)
                except json.JSONDecodeError as exc:
                    raise ValueError("--dynamics-input-distribution must be valid JSON") from exc
            if not isinstance(raw_distribution, dict):
                raise ValueError("--dynamics-input-distribution must decode to an object")
            args.dynamics_input_distribution = normalize_input_distribution(raw_distribution)
        return args

    if (
        getattr(args, "scenario_profile", None) is None
        and getattr(args, "container_profile", None) in SUPPORTED_SCENARIO_PROFILES
    ):
        args.scenario_profile = args.container_profile

    if args.config_module is None or not explicit_config_module:
        args.config_module = build_default_case_kind_module_name(
            case_kind=args.case_kind,
            scenario_profile=getattr(args, "scenario_profile", None),
        )

    if args.mode == "focus":
        args.focus_points = _resolve_focus_points(
            case_kind=args.case_kind,
            config_module=args.config_module,
            raw_focus_points=args.focus_points if explicit_focus_points else None,
        )
    else:
        args.focus_points = _resolve_focus_points(
            case_kind=args.case_kind,
            config_module=args.config_module,
            raw_focus_points=args.focus_points if explicit_focus_points else None,
            allow_config_default=False,
        )
    if explicit_dkw_bounds and args.dkw_bounds:
        try:
            parsed_bounds = json.loads(args.dkw_bounds)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--dkw-bounds must be valid JSON: {exc}") from exc
        if not isinstance(parsed_bounds, dict):
            raise ValueError("--dkw-bounds must decode to an object")
        args.dkw_bounds = parsed_bounds
    return args


def _resolve_focus_points(
    *,
    case_kind: str,
    config_module: str,
    raw_focus_points: str | None,
    allow_config_default: bool = True,
) -> list[dict[str, object]] | None:
    if raw_focus_points:
        try:
            parsed = json.loads(raw_focus_points)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--focus_points must be valid JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise ValueError("--focus_points must decode to a list of objects")
        return [dict(point) for point in parsed]
    if not allow_config_default:
        return None
    return load_focus_points(case_kind=case_kind, module_name=config_module)


def build_orchestrator_config(args: argparse.Namespace) -> OrchestratorConfig:
    return OrchestratorConfig(
        fixture=getattr(args, "fixture", None),
        output=args.output,
        params=parse_param_assignments(getattr(args, "params", [])),
        case_id=getattr(args, "case_id", None),
        case_kind=getattr(args, "case_kind", "uturn"),
        run_mode=getattr(args, "mode", "explore"),
        target=getattr(args, "target", "awsim"),
        dynamics_output_root=getattr(args, "dynamics_output_root", None),
        dynamics_input_distribution=getattr(args, "dynamics_input_distribution", None),
        dynamics_decision_mode=getattr(args, "dynamics_decision_mode", "judgment"),
        sampling_seed=getattr(args, "seed", 42),
        experiment_id=getattr(args, "experiment_id", None),
        worker_id=getattr(args, "worker_id", "worker_v2_local"),
        reason=getattr(args, "reason", "manual_orchestrator_run"),
        tags=list(getattr(args, "tags", [])),
        config_module=getattr(args, "config_module", None)
        or build_default_case_kind_module_name(
            case_kind=getattr(args, "case_kind", "uturn"),
            scenario_profile=getattr(args, "scenario_profile", None),
        ),
        container_profile=getattr(args, "container_profile", None),
        scenario_profile=getattr(args, "scenario_profile", None),
        focus_points=(
            [dict(point) for point in getattr(args, "focus_points", [])]
            if getattr(args, "focus_points", None)
            else None
        ),
        path_root=getattr(args, "path_root", None),
        dataset_csv=getattr(args, "dataset_csv", None),
        replay_csv=getattr(args, "replay_csv", None),
        replay_reason_pattern=getattr(args, "replay_reason_pattern", "BINOMIAL_CI:"),
        replay_expected_count=getattr(args, "replay_expected_count", None),
        history_path=getattr(args, "history_path", None),
        ext_mode=getattr(args, "ext_mode", "cvm"),
        queue_actor_name=getattr(args, "queue_actor_name", "local_task_queue"),
        queue_namespace=getattr(args, "queue_namespace", None),
        queue_address=getattr(args, "queue_address", None),
        shared_store_actor_name=getattr(args, "shared_store_actor_name", None),
        shared_store_namespace=getattr(args, "shared_store_namespace", None),
        shared_store_address=getattr(args, "shared_store_address", None),
        dkw_bounds=getattr(args, "dkw_bounds", None),
        dkw_region=getattr(args, "dkw_region", "custom"),
        dkw_pure_smc=getattr(args, "dkw_pure_smc", False),
        dkw_simultaneous=getattr(args, "dkw_simultaneous", False),
        refresh_interval=getattr(args, "refresh_interval", None),
        max_strategy_cases=getattr(args, "max_strategy_cases", None),
        max_samples=getattr(args, "max_samples", None),
        resume_from=getattr(args, "resume_from", None),
        resume_current_only=getattr(args, "resume_current_only", False),
        scenario_type=getattr(args, "scenario_type", None),
        simulation_output_dir=getattr(args, "simulation_output_dir", None),
        expected_trace_path=getattr(args, "expected_trace_path", None),
        local_loop_num=getattr(args, "local_loop_num", None),
        headless=getattr(args, "headless", False),
        with_host_worker=getattr(args, "with_host_worker", False),
        headless_host=getattr(args, "headless_host", False),
        worker_count=getattr(args, "worker_count", None),
        cache_size=getattr(args, "cache_size", None),
        queue_high_water=getattr(args, "queue_high_water", None),
        queue_low_water=getattr(args, "queue_low_water", None),
        run_inline_worker=not getattr(args, "run_external_workers", False),
        poll_interval_sec=getattr(args, "poll_interval_sec", 2.0),
        binomial_target=getattr(args, "binomial_target", "c_collision"),
        binomial_method=getattr(args, "binomial_method", "wilson"),
        binomial_confidence=getattr(args, "binomial_confidence", 0.95),
        binomial_target_width=getattr(args, "binomial_target_width", 0.02),
        binomial_min_samples=getattr(args, "binomial_min_samples", None),
        binomial_anytime_valid=getattr(args, "binomial_anytime_valid", False),
        sprt_target=getattr(args, "sprt_target", "c_collision"),
        sprt_p0=getattr(args, "sprt_p0", 0.1),
        sprt_p1=getattr(args, "sprt_p1", 0.01),
        sprt_beta=getattr(args, "sprt_beta", 0.05),
        sprt_confidence=getattr(args, "sprt_confidence", 0.95),
        sprt_min_samples=getattr(args, "sprt_min_samples", None),
        ebstop_target=getattr(args, "ebstop_target", "min_ttc"),
        ebstop_epsilon=getattr(args, "ebstop_epsilon", 0.1),
        ebstop_value_range=(
            getattr(args, "ebstop_value_range_min", 0.0),
            getattr(args, "ebstop_value_range_max", 10.0),
        ),
        ebstop_confidence=getattr(args, "ebstop_confidence", 0.95),
        ebstop_max_samples=getattr(args, "ebstop_max_samples", 2000),
        prism_dkw_target_metric=getattr(
            args, "prism_dkw_target_metric", "steps_to_failure_capped"
        ),
        prism_dkw_confidence=getattr(args, "prism_dkw_confidence", 0.95),
        prism_dkw_target_epsilon=getattr(args, "prism_dkw_target_epsilon", 0.15),
        prism_dkw_base_samples=getattr(args, "prism_dkw_base_samples", 20),
    )


def run_orchestrator(
    argv: Sequence[str] | None = None,
    *,
    queue: TaskQueue | None = None,
) -> int:
    argv_list = list(argv) if argv is not None else list(sys.argv[1:])
    args = build_parser().parse_args(argv_list)
    if getattr(args, "cluster", False):
        from apps.cli.orchestrator_cluster_main import run_cluster_orchestrator

        return int(run_cluster_orchestrator(argv_list))
    validate_args(args)
    args = normalize_args(args, argv=argv_list)
    orchestrator = Orchestrator(queue=queue)
    summary = orchestrator.run(build_orchestrator_config(args))
    if (
        args.target == "dynamics"
        and args.mode in {"binomial_ci", "sprt", "dkw", "dkw_fixed", "ebstop"}
    ):
        artifact_path = Path(f"{Path(args.output).expanduser()}.summary.json").resolve()
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        summary["statistical_artifact"] = str(artifact_path)
        summary["dynamics_decision_mode"] = args.dynamics_decision_mode
        artifact_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    for worker_summary in summary.get("worker_summaries", []):
        print(json.dumps(worker_summary, ensure_ascii=False))
    print(json.dumps(summary, ensure_ascii=False))
    return int(summary["worker_exit_code"])


def main(argv: Sequence[str] | None = None) -> int:
    return run_orchestrator(argv)


if __name__ == "__main__":
    raise SystemExit(main())
