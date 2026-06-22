#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import time
import os
import signal
import sys
import argparse
import importlib
import json
import csv
import ray
from typing import Optional

from redis_cluster.process_controller import ProcessController, InfraTask

# ==============================================================================
# 1. 引数解析と設定の動的読み込み
# ==============================================================================
def load_config():
    parser = argparse.ArgumentParser(description="Multi-Scenario Autonomous Driving Test Manager")
    parser.add_argument("--type", type=str, default="uturn", help="Scenario type (e.g., uturn, cutin)")
    parser.add_argument("--mode", type=str, choices=["explore", "focus", "margin", "jama_edge", "ttc_edge", "worst_ttc", "dkw", "dkw_fixed", "verify_consistency"], default="explore", help="Search mode: explore (default), focus, margin, jama_edge, ttc_edge, worst_ttc, dkw, dkw_fixed, or verify_consistency")
    parser.add_argument("--focus_points", type=str, default=None, help="JSON string for focus points (e.g., '[{\"dx0\": 15.0}]')")
    parser.add_argument("--headless", action="store_true", help="Run with Xvfb (No GUI)")
    parser.add_argument("--ext_mode", type=str, default="cvm", help="Kinematics Extractor Mode for Checker (cvm/ctrv/maude)")
    parser.add_argument("--dkw_bounds", type=str, default=None, help="JSON string defining the specific region for DKW")
    parser.add_argument("--dkw_region", type=str, default="custom", help="Extraction condition string (e.g. 'emp_safe and jama_safe')")
    parser.add_argument("--dkw_pure_smc", action="store_true", help="DKWモードで過去の探索データを再利用せず、純粋なSMCデータのみで評価する")
    parser.add_argument("--dkw_simultaneous", action="store_true", help="DKWモードで複数指標を同時に評価し、ボンフェローニ補正を用いた同時保証を行う")
    args = parser.parse_args()

    try:
        # configs フォルダ内のモジュールを動的にインポート
        config_module = importlib.import_module(f"configs.{args.type}")
        print(f"[System] シナリオ設定 'configs.{args.type}' を正常に読み込みました。")
    except ImportError:
        print(f"[Fatal] 設定ファイル configs/{args.type}.py が見つかりません。")
        print("  -> configs/ フォルダ内にファイルがあるか、__init__.py が存在するか確認してください。")
        sys.exit(1)

    focus_points = None
    if args.mode == "focus":
        if args.focus_points:
            try:
                focus_points = json.loads(args.focus_points)
                print(f"[System] CLI引数からフォーカス(集中)モードを有効化しました: {focus_points}")
            except json.JSONDecodeError as e:
                print(f"[Fatal] --focus_points 引数のJSONパースに失敗しました: {e}")
                sys.exit(1)
        else:
            focus_points = getattr(config_module, 'FOCUS_POINTS', None)
            if focus_points:
                print(f"[System] Configからフォーカス(集中)モードを有効化しました: {focus_points}")
            else:
                # 分散ワーカーとしてはマスターの指示（タスク）に従うだけなので、ここでプロセスを落とさない
                print("[System] ConfigにFOCUS_POINTSがありませんが、マスターからの指示に従って動作します。")

    return args.type, config_module, args.mode, focus_points, args.headless, args.ext_mode

SCENARIO_NAME, cfg, RUN_MODE, FOCUS_POINTS, HEADLESS_MODE, EXT_MODE = load_config()

LAUNCH_DIR = os.path.dirname(os.path.abspath(__file__))
if LAUNCH_DIR not in sys.path:
    sys.path.append(LAUNCH_DIR)

from redis_cluster.cluster_config import MASTER_IP, RAY_PORT
from param_logger import log_parameters

# ==============================================================================
# 2. コンフィグレーション
# ==============================================================================
HOME = "/home/passd"
SETUP_BASH = os.path.join(HOME, "autoware/install/setup.bash")

# ==============================================================================
# [追加] 実行号機(マスターかリモートか)の判定と出力先ディレクトリの分岐
# ==============================================================================
ROS_DOMAIN_ID = os.environ.get("ROS_DOMAIN_ID", "0")
IS_MASTER = (ROS_DOMAIN_ID == "21")
IS_HOST_MODE = (os.environ.get("EXEC_MODE") == "host")
WORKER_NAME = f"{ROS_DOMAIN_ID}号機"

REPEAT_COUNT = cfg.REPEAT_COUNT
if IS_HOST_MODE:
    OUTPUT_DIR = os.path.join(HOME, "simulation_traces_host")
else:
    OUTPUT_DIR = os.path.join(HOME, "simulation_traces")
os.environ["AW_OUTPUT_DIR"] = OUTPUT_DIR

FILE_PATTERN = f"{SCENARIO_NAME}_test_*.json"
# シナリオの設定(config)に TIMEOUT_SEC があればそれを使い、なければデフォルトで200秒とする
TIMEOUT_SEC = getattr(cfg, 'TIMEOUT_SEC', 200)
INTERVAL_SEC = 1
REFRESH_INTERVAL = 10

if IS_MASTER:
    # 21号機(マスター): 従来通り RViz と AWSIM の画面を表示する
    AWSIM_CMD = "./awsim_labs.x86_64 -noise false"
    AUTOWARE_CMD = (
        "ros2 launch autoware_launch e2e_simulator.launch.xml "
        "vehicle_model:=awsim_labs_vehicle "
        "sensor_model:=awsim_labs_sensor_kit "
        f"map_path:={HOME}/autoware_map/nishishinjuku_autoware_map "
        "launch_vehicle_interface:=true"
    )
    AW_DELAY = 40
else:
    # 22, 23号機(リモート): Xvfb環境下で通常通り(画面・RVizありで)起動させる
    AWSIM_CMD = "./awsim_labs.x86_64 -noise false"
    AUTOWARE_CMD = (
        "ros2 launch autoware_launch e2e_simulator.launch.xml "
        "vehicle_model:=awsim_labs_vehicle "
        "sensor_model:=awsim_labs_sensor_kit "
        f"map_path:={HOME}/autoware_map/nishishinjuku_autoware_map "
        "launch_vehicle_interface:=true"
    )
    AW_DELAY = 90

INFRA_TASKS = [
    InfraTask(
        name="AWSIM Labs",
        work_dir=os.path.join(HOME, "awsim_labs"),
        command=AWSIM_CMD,
        delay=15
    ),
    InfraTask(
        name="Autoware",
        work_dir=os.path.join(HOME, "autoware"),
        command=AUTOWARE_CMD,
        delay=AW_DELAY,
        source_setup=True
    ),
    InfraTask(
        name="Runtime Monitor",
        work_dir=os.path.join(HOME, "AW-Runtime-Monitor"),
        command=(
            f"python3 main.py -o {os.path.join(OUTPUT_DIR, SCENARIO_NAME + '_test')} "
            "-n {sim_num}"
        ),
        delay=5,
        source_setup=True
    ),
    InfraTask(
        name="AW Checker (Safety Evaluator)",
        work_dir=LAUNCH_DIR,
        command=f"python3 awchecker.py --type {SCENARIO_NAME} --ext_mode {EXT_MODE}",
        delay=2,
        source_setup=False,
        resident=True
    ),
]

def get_last_processed_loop(csv_path: str) -> int:
    """結果CSVを読み込み、記録されている最大のループ番号を返す"""
    if not os.path.exists(csv_path):
        return 0
    
    last_loop = 0
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    loop_num = int(row["loop_num"])
                    if loop_num > last_loop:
                        last_loop = loop_num
                except (ValueError, KeyError):
                    continue
    except Exception:
        return 0
    return last_loop
# ==============================================================================
# 3. プロセスマネージャー
# ==============================================================================

class ProcessManager:
    """ワーカーメインプロセス。司令塔からタスクを受け取り、シミュレーションを実行する。"""

    def __init__(self):
        self.controller = ProcessController(headless_mode=HEADLESS_MODE)
        self.controller.setup_xvfb()

        # --- 分散対応: 司令塔のキューに接続 ---
        print("[Manager] Rayクラスターに接続しています...")
        ray.init(address=f"{MASTER_IP}:{RAY_PORT}", namespace='awsim_cluster', ignore_reinit_error=True)

        print("[Manager] 司令塔 (TaskQueueActor) を探しています...")
        while True:
            try:
                self.task_queue = ray.get_actor("TaskQueueActor")
                print("[Manager] 司令塔 (TaskQueueActor) への接続に成功しました！")
                break
            except ValueError:
                print("  -> 司令塔がまだ起動していません。5秒後に再試行します...")
                time.sleep(5)

        print("[Manager] 共有金庫 (SharedStoreActor) を探しています...")
        try:
            self.shared_store = ray.get_actor("SharedStoreActor")
            print("[Manager] 共有金庫 (SharedStoreActor) への接続に成功しました！")
        except ValueError:
            print("[Manager] ⚠️ 共有金庫が見つかりませんでした。データ記録に失敗する可能性があります。")
            self.shared_store = None

    def execute(self):
        """タスク取得→シミュレーション実行→結果報告のメインループ"""
        print(f"=== 自動化システム [{SCENARIO_NAME.upper()} モード] ===")
        os.makedirs(OUTPUT_DIR, exist_ok=True)

        print(f"[System] 分散ワーカーとして待機を開始します。")
        local_exec_count = 0
        local_total_count = 0

        while True:
            if not self.controller.infra_procs:
                self.controller.start_all_infra(
                    INFRA_TASKS, OUTPUT_DIR, SETUP_BASH,
                    first_boot=(local_total_count == 0)
                )

            while True:
                print(f"  [Manager] 司令塔から次のタスク(パラメータ)を待機中...")
                try:
                    ray.get(self.task_queue.update_worker_status.remote(WORKER_NAME, "待機中"))
                except Exception:
                    pass

                while True:
                    try:
                        next_target = ray.get(self.task_queue.get_next_task.remote())
                        if next_target is not None:
                            break
                    except Exception as e:
                        print(f"  [警告] 司令塔との通信エラー（数秒後に再試行します）: {e}")

                    import random
                    time.sleep(1.0 + random.uniform(0.0, 2.0))

                if next_target.get("system_command") == "stop":
                    print(f"\n[Manager] 🏁 Strategistから終了シグナルを受信しました。({next_target.get('reason')})")
                    return

                reason_str = next_target.pop("reason", "")
                current_loop_num = next_target.pop("global_loop_num", local_total_count + 1)
                print(f"\n--- Global Task ID {current_loop_num} ---")

                expected_local_num = local_total_count + 1
                local_target_json = os.path.join(OUTPUT_DIR, f"{SCENARIO_NAME}_test_sim{expected_local_num}.json")
                if os.path.exists(local_target_json):
                    os.remove(local_target_json)

                global_target_json = os.path.join(OUTPUT_DIR, f"{SCENARIO_NAME}_eval_sim{current_loop_num}.json")
                if os.path.exists(global_target_json):
                    os.remove(global_target_json)

                local_prefix = os.path.join(OUTPUT_DIR, f"{SCENARIO_NAME}_test_sim{expected_local_num}_footage")
                global_prefix = os.path.join(OUTPUT_DIR, f"{SCENARIO_NAME}_eval_sim{current_loop_num}_footage")
                for ext in [".mp4", ".meta.json"]:
                    if os.path.exists(local_prefix + ext): os.remove(local_prefix + ext)
                    if os.path.exists(global_prefix + ext): os.remove(global_prefix + ext)

                csv_filename = f"{SCENARIO_NAME}_parameters.csv"
                param_args = " ".join([f"--{k} {v:.2f}" for k, v in next_target.items()])
                dynamic_cmd = f"python3 run_scenario.py --type {SCENARIO_NAME} {param_args}"

                self.controller.launch_scenario(
                    f"source {SETUP_BASH} && {dynamic_cmd}", LAUNCH_DIR
                )

                try:
                    ray.get(self.task_queue.update_worker_status.remote(WORKER_NAME, f"Sim {current_loop_num} 実行中"))
                except Exception:
                    pass

                print(f"  >>> 監視中... (Timeout: {TIMEOUT_SEC}s)")
                start_wait = time.time()
                is_timeout = False

                while True:
                    time.sleep(2)
                    if os.path.exists(local_target_json):
                        print(f"  [成功] {os.path.basename(local_target_json)} 生成確認")
                        time.sleep(5)

                        from param_logger import log_parameters
                        log_parameters(OUTPUT_DIR, csv_filename, current_loop_num, next_target, reason=reason_str)

                        os.rename(local_target_json, global_target_json)
                        print(f"  [変換] -> {os.path.basename(global_target_json)} にリネーム完了")

                        for ext in [".mp4", ".meta.json"]:
                            if os.path.exists(local_prefix + ext):
                                os.rename(local_prefix + ext, global_prefix + ext)
                        break

                    if time.time() - start_wait > TIMEOUT_SEC:
                        print(f"  [警告] タイムアウト")
                        is_timeout = True
                        break

                if is_timeout:
                    failed_reason = f"{reason_str} [ERROR: TIMEOUT]"
                    if self.shared_store:
                        result_labels = getattr(cfg, 'RESULT_LABELS', [])
                        ray.get(self.shared_store.flush_timeout_task.remote(
                            SCENARIO_NAME, current_loop_num, next_target, failed_reason, result_labels))
                    with open(global_target_json, 'w') as f:
                        f.write("TIMEOUT")
                    try:
                        ray.get(self.task_queue.update_worker_status.remote(WORKER_NAME, f"Sim {current_loop_num} タイムアウト"))
                    except Exception:
                        pass
                    local_exec_count += 1
                    local_total_count += 1
                    try:
                        ray.get(self.task_queue.report_completion.remote(current_loop_num, "timeout"))
                    except Exception:
                        pass
                    self.controller.kill_infra()
                    break
                else:
                    self.controller.kill_client()
                    local_exec_count += 1
                    local_total_count += 1
                    try:
                        ray.get(self.task_queue.report_completion.remote(current_loop_num, "success"))
                    except Exception:
                        pass
                    try:
                        ray.get(self.task_queue.update_worker_status.remote(WORKER_NAME, f"Sim {current_loop_num} 完了"))
                    except Exception:
                        pass
                    if local_exec_count % REFRESH_INTERVAL == 0:
                        print(f"\n  [定期リフレッシュ] インフラを再起動します。")
                        self.controller.kill_infra()
                        break
                    time.sleep(INTERVAL_SEC)


    def cleanup_all(self):
        """全プロセスを完全に停止する"""
        self.controller.cleanup_all()


if __name__ == "__main__":
    manager = ProcessManager()
    try:
        manager.execute()
    except KeyboardInterrupt:
        print("\n[!] ユーザーによる中断")
    finally:
        manager.cleanup_all()
        sys.exit(0)
