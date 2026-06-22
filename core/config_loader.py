#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
設定読み込みモジュール。

コマンドライン引数のパースと、シナリオ設定ファイル(configs/*.py)の動的読み込みを行う。
ファイルI/Oを含む副作用（resume_from の復元等）は行わない。
"""

import argparse
import importlib
import json
import sys


def build_parser():
    """コマンドライン引数パーサーを構築する"""
    parser = argparse.ArgumentParser(
        description="Multi-Scenario Autonomous Driving Test Master Orchestrator"
    )
    parser.add_argument("--type", type=str, default="uturn",
                        help="Scenario type (e.g., uturn, cutin)")
    parser.add_argument("--mode", type=str,
                        choices=["explore", "focus", "margin", "jama_edge",
                                 "ttc_edge", "worst_ttc", "dkw", "dkw_fixed",
                                 "verify_consistency"],
                        default="explore", help="Search mode")
    parser.add_argument("--focus_points", type=str, default=None,
                        help="JSON string for focus points")
    parser.add_argument("--with_host_worker", action="store_true",
                        help="Run a local worker on the host machine")
    parser.add_argument("--headless_host", action="store_true",
                        help="Run the host worker with Xvfb (No GUI)")
    parser.add_argument("--headless", action="store_true",
                        help="Run the master container with Xvfb (No GUI)")
    parser.add_argument("--resume_from", type=str, default=None,
                        help="Directory to restore dataset from")
    parser.add_argument("--ext_mode", type=str,
                        choices=["maude", "cvm", "ctrv"], default="cvm",
                        help="Kinematics extractor mode")
    parser.add_argument("--dkw_bounds", type=str, default=None,
                        help="JSON string defining DKW region")
    parser.add_argument("--dkw_region", type=str, default="custom",
                        help="Extraction condition string")
    parser.add_argument("--dkw_pure_smc", action="store_true",
                        help="Pure SMC mode (exclude past exploration data)")
    parser.add_argument("--dkw_simultaneous", action="store_true",
                        help="Bonferroni-corrected simultaneous guarantee")
    parser.add_argument("--max_samples", type=int, default=None,
                        help="Override maximum number of simulation loops")
    return parser


def load_config_module(scenario_type):
    """configs/{scenario_type}.py を動的インポートする"""
    try:
        module = importlib.import_module(f"configs.{scenario_type}")
        print(f"[Config] シナリオ設定 'configs.{scenario_type}' を読み込みました。")
        return module
    except ImportError:
        print(f"[Fatal] 設定ファイル configs/{scenario_type}.py が見つかりません。")
        sys.exit(1)


def parse_focus_points(args, config_module):
    """フォーカスポイントをパースする"""
    if args.mode != "focus":
        return None
    if args.focus_points:
        try:
            return json.loads(args.focus_points)
        except json.JSONDecodeError as e:
            print(f"[Fatal] --focus_points のJSONパース失敗: {e}")
            sys.exit(1)
    else:
        points = getattr(config_module, 'FOCUS_POINTS', None)
        if not points:
            print("[Fatal] --mode focus ですが FOCUS_POINTS が未設定です。")
            sys.exit(1)
        return points


def parse_dkw_bounds(dkw_bounds_str):
    """DKWバウンドのJSON文字列をパースする"""
    if dkw_bounds_str is None:
        return None
    try:
        return json.loads(dkw_bounds_str)
    except json.JSONDecodeError as e:
        print(f"[Fatal] --dkw_bounds のJSONパース失敗: {e}")
        sys.exit(1)


def load_config():
    """
    全設定を読み込み、Configオブジェクトとして返す。

    Returns:
        Config: 全ての設定値を属性として持つシンプルなオブジェクト
    """
    parser = build_parser()
    args = parser.parse_args()

    config_module = load_config_module(args.type)
    focus_points = parse_focus_points(args, config_module)
    dkw_bounds = parse_dkw_bounds(args.dkw_bounds)

    # シンプルな設定オブジェクト
    class Config:
        pass

    cfg = Config()
    cfg.scenario_name = args.type
    cfg.config_module = config_module
    cfg.run_mode = args.mode
    cfg.focus_points = focus_points
    cfg.with_host_worker = args.with_host_worker
    cfg.headless_host = args.headless_host
    cfg.headless = args.headless
    cfg.ext_mode = args.ext_mode
    cfg.resume_from = args.resume_from
    cfg.dkw_bounds = dkw_bounds
    cfg.dkw_region = args.dkw_region
    cfg.dkw_pure_smc = args.dkw_pure_smc
    cfg.dkw_simultaneous = args.dkw_simultaneous
    cfg.max_samples = args.max_samples
    return cfg
