#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import sys
import os
import importlib

# 1. パスの設定（ライブラリと自作configへのパスを通す）
HOME = os.path.expanduser("~")
LIB_DIR = os.path.join(HOME, "AWSIMScriptPy")
LAUNCH_DIR = os.path.dirname(os.path.abspath(__file__))

for path in [LIB_DIR, LAUNCH_DIR]:
    if path not in sys.path:
        sys.path.append(path)

from core.scenario_manager import ScenarioManager, LaneOffset

def main():
    # --- 引数解析：--type 以外は未知の引数として柔軟に受け取る ---
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", type=str, required=True, help="Scenario type (e.g., uturn, cutin)")
    args, unknown = parser.parse_known_args()

    # 未知の引数（--dx0 10.0 など）を辞書形式に変換
    dynamic_params = {}
    for i in range(0, len(unknown), 2):
        key = unknown[i].lstrip("-")
        val = float(unknown[i+1])
        dynamic_params[key] = val

    # --- 動的な設定読み込み ---
    try:
        cfg = importlib.import_module(f"configs.{args.type}")
        fixed = cfg.FIXED_PARAMS
    except ImportError:
        print(f"[Error] Config for '{args.type}' not found in configs/ directory.")
        return

    scenario_manager = ScenarioManager()
    scenario = None

    # ==========================================================
    # 2. シナリオごとの振り分けロジック
    # ==========================================================

    # 【A】Uターン (uturn)
    if args.type == "uturn":
        from scenarios.uturn.base import make_uturn_scenario
        
        # 単位変換
        v_ego = dynamic_params["ego_speed"] / 3.6
        v_npc = dynamic_params["npc_speed"] / 3.6

        # Uターン特有の速度帯別オフセット計算
        target_lane = fixed["ego_init_lane"]
        if dynamic_params["ego_speed"] < 32.5:
            ego_init = LaneOffset(target_lane, 38)
        elif dynamic_params["ego_speed"] < 37.5:
            ego_init = LaneOffset(target_lane, 17)
        else:
            ego_init = LaneOffset(target_lane, 4)

        scenario = make_uturn_scenario(
            network=scenario_manager.network,
            ego_init_laneoffset=ego_init,
            ego_goal_laneoffset=LaneOffset(fixed["ego_goal_lane"], fixed["ego_goal_offset"]),
            npc_init_laneoffset=LaneOffset(fixed["npc_init_lane"], fixed["npc_init_offset"]),
            uturn_next_lane=fixed["uturn_next_lane"],
            _ego_speed=v_ego,
            _npc_speed=v_npc,
            dx0=dynamic_params["dx0"],
            acceleration=fixed["acceleration"]
        )

    # 【B】割り込み (cutin) - 将来の拡張例
    elif args.type == "cutin":
        # from scenarios.cutin.base import make_cutin_scenario
        # scenario = make_cutin_scenario(...)
        print(">>> [Runner] Cut-in scenario is not yet implemented, but ready to plug in!")
        return

    # --- 実行 ---
    if scenario:
        print(f">>> [Runner] Starting '{args.type}' simulation...")
        scenario_manager.run([scenario])
    else:
        print(f"[Error] Scenario object could not be created for type: {args.type}")

if __name__ == "__main__":
    main()
