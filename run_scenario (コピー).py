#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import sys
import os

# =========================================================
# 1. パスの設定 (ディレクトリ分離対応)
# =========================================================
# ★ 部品庫である AWSIMScriptPy のディレクトリパスを指定し、Pythonに認識させる
LIB_DIR = os.path.expanduser("~/AWSIMScriptPy")
if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

# パスを通したあとに、AWSIMScriptPy内のモジュールをインポートする
from core.scenario_manager import ScenarioManager, LaneOffset
from scenarios.uturn.base import make_uturn_scenario

# 同じディレクトリ (AWSIM_launch) にある設定ファイルをインポートする
import uturn_config

# =========================================================
# 2. メイン実行ロジック1
# =========================================================
def main():
    # 引数の受け取り設定
    parser = argparse.ArgumentParser(description="AWSIM Scenario Runner (Fuzzing Target)")
    parser.add_argument("--type", type=str, required=True, help="実行するシナリオの種類 (例: uturn)")
    parser.add_argument("--dx0", type=float, required=True, help="Uターン開始のトリガー距離 [m]")
    parser.add_argument("--ego_speed", type=float, required=True, help="自車の目標速度 [km/h]")
    parser.add_argument("--npc_speed", type=float, required=True, help="他車の速度 [km/h]")
    
    args = parser.parse_args()
    
    print(f"\n>>> [Scenario Runner] 起動パラメータ:")
    print(f"    シナリオ: {args.type}")
    print(f"    距離(dx0): {args.dx0:.2f} m")
    print(f"    自車速度 : {args.ego_speed:.1f} km/h")
    print(f"    他車速度 : {args.npc_speed:.1f} km/h")

    scenario_manager = ScenarioManager()
    
    if args.type == "uturn":
        # configから固定パラメータ（NPCの初期位置など）を取得
        fixed = uturn_config.FIXED_PARAMS
        
        # 単位変換 (km/h -> m/s)
        ego_speed_ms = args.ego_speed / 3.6
        npc_speed_ms = args.npc_speed / 3.6
        
        # ---------------------------------------------------------
        # ★ 修正: 自車(Ego)の目標速度に合わせて、スタートとゴール位置を自動調整
        # (元の uturn_left_15.py の物理的な辻褄合わせを再現)
        # ---------------------------------------------------------
        if args.ego_speed < 32.5:
            # 約30km/h以下帯 (短い助走でOK)
            print("    -> 速度帯: 低速 (30km/h基準) - 車線511の38m地点からスタート")
            ego_init = LaneOffset('511', 38)
            ego_goal = LaneOffset('513', 20)
            
        elif args.ego_speed < 37.5:
            # 約35km/h帯 (少し後ろから助走)
            # ※元のコードの LneOffset というタイポを修正しています
            print("    -> 速度帯: 中速 (35km/h基準) - 車線511の17m地点からスタート")
            ego_init = LaneOffset('511', 17)
            ego_goal = LaneOffset('513', 20)
            
        else:
            # 約40km/h以上帯 (長い助走が必要なので手前の別車線から)
            print("    -> 速度帯: 高速 (40km/h基準) - 車線281の4m地点からスタート")
            ego_init = LaneOffset('511', 4)
            ego_goal = LaneOffset('123', 18)

        # ---------------------------------------------------------
        # 他車(NPC)の位置はconfigファイルの固定値をそのまま使う
        npc_init = LaneOffset(fixed["npc_init_lane"], fixed["npc_init_offset"])
        
        # base.py のロジックにすべての変数を流し込む
        scenario = make_uturn_scenario(
            network=scenario_manager.network,
            ego_init_laneoffset=ego_init,
            ego_goal_laneoffset=ego_goal,
            npc_init_laneoffset=npc_init,
            uturn_next_lane=fixed["uturn_next_lane"],
            _ego_speed=ego_speed_ms,
            _npc_speed=npc_speed_ms,
            dx0=args.dx0,
            acceleration=fixed.get("acceleration", 7.0)
        )
        
        print(">>> [Scenario Runner] シミュレーションを開始します...")
        scenario_manager.run([scenario])
        
    else:
        print(f"[Error] 未知のシナリオタイプが指定されました: {args.type}")
        sys.exit(1)

if __name__ == "__main__":
    main()
