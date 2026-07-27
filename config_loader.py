#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import importlib
import json
import sys
from dataclasses import dataclass
from typing import Any


@dataclass
class OrchestratorConfig:
    scenario_name: str
    config_module: Any
    run_mode: str
    focus_points: Any
    headless: bool
    with_host_worker: bool
    headless_host: bool
    ext_mode: str
    dkw_bounds: Any
    dkw_region: str
    dkw_pure_smc: bool
    dkw_simultaneous: bool
    resume_from: str | None
    max_samples: int | None
    binomial_target: str
    binomial_method: str
    binomial_confidence: float
    binomial_target_width: float
    binomial_min_samples: int | None


def load_config() -> OrchestratorConfig:
    parser = argparse.ArgumentParser(
        description="Master orchestrator for distributed AWSIM exploration"
    )
    parser.add_argument("--type", type=str, default="uturn",
                        help="Scenario type (e.g., uturn, cutin)")
    parser.add_argument(
        "--mode",
        type=str,
        choices=[
            "explore", "focus", "margin", "jama_edge", "ttc_edge",
            "worst_ttc", "dkw", "dkw_fixed", "verify_consistency",
            "binomial_ci",
            "boundary_gap"
        ],
        default="explore",
        help="Search mode"
    )
    parser.add_argument("--focus_points", type=str, default=None,
                        help="JSON string for focus points")
    parser.add_argument("--headless", action="store_true",
                        help="Run master-side container worker headless")
    parser.add_argument("--with_host_worker", action="store_true",
                        help="Use a host worker on the master instead of the master container")
    parser.add_argument("--headless_host", action="store_true",
                        help="Run the host worker headless with Xvfb")
    parser.add_argument("--ext_mode", type=str, default="cvm",
                        help="Kinematics Extractor Mode for Checker (cvm/ctrv/maude)")
    parser.add_argument("--dkw_bounds", type=str, default=None,
                        help="JSON string defining the specific region for DKW")
    parser.add_argument("--dkw_region", type=str, default="custom",
                        help="Extraction condition string (e.g. 'emp_safe and jama_safe')")
    parser.add_argument("--dkw_pure_smc", action="store_true",
                        help="Do not reuse prior exploration data in DKW mode")
    parser.add_argument("--dkw_simultaneous", action="store_true",
                        help="Evaluate multiple DKW metrics with simultaneous guarantee")
    parser.add_argument("--resume_from", type=str, default=None,
                        help="Restore a previous dataset directory before resuming")
    parser.add_argument("--max_samples", type=int, default=None,
                        help="Override repeat count / fixed sampling target")
    parser.add_argument("--binomial_target", type=str, default="c_collision",
                        help="Binary target column for binomial CI mode")
    parser.add_argument("--binomial_method", type=str, choices=["wilson", "clopper-pearson"],
                        default="wilson", help="Confidence interval method for binomial CI mode")
    parser.add_argument("--binomial_confidence", type=float, default=0.95,
                        help="Confidence level for binomial CI mode")
    parser.add_argument("--binomial_target_width", type=float, default=0.02,
                        help="Stop once CI width is below this threshold")
    parser.add_argument("--binomial_min_samples", type=int, default=None,
                        help="Minimum pure-random samples before allowing early stop")
    args = parser.parse_args()

    try:
        config_module = importlib.import_module(f"configs.{args.type}")
        print(f"[System] シナリオ設定 'configs.{args.type}' を正常に読み込みました。")
    except ImportError:
        print(f"[Fatal] 設定ファイル configs/{args.type}.py が見つかりません。")
        sys.exit(1)

    focus_points = None
    if args.mode == "focus":
        if args.focus_points:
            try:
                focus_points = json.loads(args.focus_points)
                print(f"[System] CLI引数からフォーカスモードを有効化しました: {focus_points}")
            except json.JSONDecodeError as e:
                print(f"[Fatal] --focus_points 引数のJSONパースに失敗しました: {e}")
                sys.exit(1)
        else:
            focus_points = getattr(config_module, "FOCUS_POINTS", None)
            if focus_points:
                print(f"[System] Configからフォーカスモードを有効化しました: {focus_points}")

    dkw_bounds = None
    if args.dkw_bounds:
        try:
            dkw_bounds = json.loads(args.dkw_bounds)
        except json.JSONDecodeError as e:
            print(f"[Fatal] --dkw_bounds 引数のJSONパースに失敗しました: {e}")
            sys.exit(1)

    return OrchestratorConfig(
        scenario_name=args.type,
        config_module=config_module,
        run_mode=args.mode,
        focus_points=focus_points,
        headless=args.headless,
        with_host_worker=args.with_host_worker,
        headless_host=args.headless_host,
        ext_mode=args.ext_mode,
        dkw_bounds=dkw_bounds,
        dkw_region=args.dkw_region,
        dkw_pure_smc=args.dkw_pure_smc,
        dkw_simultaneous=args.dkw_simultaneous,
        resume_from=args.resume_from,
        max_samples=args.max_samples,
        binomial_target=args.binomial_target,
        binomial_method=args.binomial_method,
        binomial_confidence=args.binomial_confidence,
        binomial_target_width=args.binomial_target_width,
        binomial_min_samples=args.binomial_min_samples,
    )
