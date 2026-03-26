#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import sys
import os

# パスの設定
LIB_DIR = os.path.expanduser("~/AWSIMScriptPy")
if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from core.scenario_manager import ScenarioManager, LaneOffset
from scenarios.uturn.base import make_uturn_scenario
import uturn_config

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", type=str, required=True)
    parser.add_argument("--dx0", type=float, required=True)
    parser.add_argument("--ego_speed", type=float, required=True)
    parser.add_argument("--npc_speed", type=float, required=True)
    args = parser.parse_args()

    scenario_manager = ScenarioManager()
    
    if args.type == "uturn":
        fixed = uturn_config.FIXED_PARAMS
        ego_speed_ms = args.ego_speed / 3.6
        npc_speed_ms = args.npc_speed / 3.6
        
        # ★ configからレーン情報を完全に読み込む (これで絶対にズレません)
        target_lane = fixed["ego_init_lane"] # 例: '514'
        goal_lane = fixed["ego_goal_lane"]   # 例: '516'
        next_lane = fixed["uturn_next_lane"] # 例: '514'

        # --- 速度帯による初期位置(Offset)だけの決定 ---
        if args.ego_speed < 32.5:
            ego_init = LaneOffset(target_lane, 38)
        elif args.ego_speed < 37.5:
            ego_init = LaneOffset(target_lane, 17)
        else:
            print(f"    -> 速度帯: 高速 - 車線{target_lane}の4m地点からスタート (Goal: {goal_lane})")
            ego_init = LaneOffset(target_lane, 4)

        # ゴールとNPC位置もconfigと完全同期
        ego_goal = LaneOffset(goal_lane, fixed["ego_goal_offset"])
        npc_init = LaneOffset(fixed["npc_init_lane"], fixed["npc_init_offset"])
        
        scenario = make_uturn_scenario(
            network=scenario_manager.network,
            ego_init_laneoffset=ego_init,
            ego_goal_laneoffset=ego_goal,
            npc_init_laneoffset=npc_init,
            uturn_next_lane=next_lane, # ★ 合流先もconfigを使用
            _ego_speed=ego_speed_ms,
            _npc_speed=npc_speed_ms,
            dx0=args.dx0,
            acceleration=fixed["acceleration"]
        )
        
        # ★ 修正: エラーの原因だった「.lane_id」を削除し、安全な変数に書き換えました
        print(f">>> [Runner] Start: {target_lane}({ego_init.offset}m) -> Goal: {goal_lane}")
        scenario_manager.run([scenario])

if __name__ == "__main__":
    main()
