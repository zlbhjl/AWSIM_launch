#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import time
import os
import signal
import glob
import random
import sys
from dataclasses import dataclass
from typing import List, Tuple, Optional

# ==============================================================================
# 設定ファイルの読み込み (ディレクトリ分離対応)
# ==============================================================================
LAUNCH_DIR = os.path.dirname(os.path.abspath(__file__))
if LAUNCH_DIR not in sys.path:
    sys.path.append(LAUNCH_DIR)
import uturn_config

# ★ 追加: 新しく作った汎用ロガーを読み込む
from param_logger import log_parameters
# ==============================================================================
# 1. コンフィグレーション
# ==============================================================================
HOME = os.path.expanduser("~")
SETUP_BASH = os.path.join(HOME, "autoware/install/setup.bash")

REPEAT_COUNT = uturn_config.REPEAT_COUNT
OUTPUT_DIR = os.path.join(HOME, "simulation_traces")
FILE_PATTERN = "uturn_test_*.json"
TIMEOUT_SEC = 300  # タイムアウト時間
INTERVAL_SEC = 1

# ★ 追加: システムの定期リフレッシュ間隔（10回ごとに再起動）
REFRESH_INTERVAL = 10

@dataclass
class Task:
    name: str
    work_dir: str
    command: str
    delay: int = 2
    source_setup: bool = False

# 【A】システム常駐（AWSIM + Autoware + Monitor）
INFRA_TASKS = [
    Task(
        name="AWSIM Labs",
        work_dir=os.path.join(HOME, "awsim_labs"),
        command="./awsim_labs.x86_64 -noise false",
        delay=15
    ),
    Task(
        name="Autoware",
        work_dir=os.path.join(HOME, "autoware"),
        command=(
            "ros2 launch autoware_launch e2e_simulator.launch.xml "
            "vehicle_model:=awsim_labs_vehicle "
            "sensor_model:=awsim_labs_sensor_kit "
            f"map_path:={HOME}/autoware_map/nishishinjuku_autoware_map "
            "launch_vehicle_interface:=true"
        ),
        delay=20,
        source_setup=True
    ),
    Task(
        name="Runtime Monitor",
        work_dir=os.path.join(HOME, "AW-Runtime-Monitor"),
        command=(
            f"python3 main.py -o {os.path.join(OUTPUT_DIR, 'uturn_test')} "
            "-n {sim_num}"
        ),
        delay=5,
        source_setup=True
    ),
]

# ==============================================================================
# 2. プロセスマネージャー
# ==============================================================================

class ProcessManager:
    def __init__(self):
        self.infra_procs: List[Tuple[str, subprocess.Popen]] = []
        self.client_proc: Optional[subprocess.Popen] = None

    def _build_command(self, task: Task, sim_num: int) -> str:
        cmd = task.command.replace("{sim_num}", str(sim_num))
        if task.source_setup:
            cmd = f"source {SETUP_BASH} && {cmd}"
        return cmd

    def _start_process(self, task: Task, sim_num: int) -> Optional[subprocess.Popen]:
        full_cmd = self._build_command(task, sim_num)
        print(f"  [起動] {task.name} ... ", end="", flush=True)
        try:
            proc = subprocess.Popen(
                full_cmd, cwd=task.work_dir, shell=True,
                executable="/bin/bash", preexec_fn=os.setsid
            )
            print(f"OK (PID: {proc.pid}) -> {task.delay}秒待機")
            if task.delay > 0:
                time.sleep(task.delay)
            return proc
        except Exception as e:
            print(f"失敗: {e}")
            return None

    def _run_trigger_once(self, task: Task, sim_num: int):
        self.kill_client()
        full_cmd = self._build_command(task, sim_num)
        print(f"  [入力] {task.name} コマンド実行")
        try:
            self.client_proc = subprocess.Popen(
                full_cmd, cwd=task.work_dir, shell=True,
                executable="/bin/bash", preexec_fn=os.setsid
            )
        except Exception as e:
            print(f"  [エラー] コマンド送信失敗: {e}")

    # 強化されたキル処理（SIGKILL）
    def _kill_process_group(self, proc: subprocess.Popen, name: str):
        if proc is None or proc.poll() is not None:
            return
            
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGINT)
            print(f"  [停止要求] {name} (PID: {proc.pid})")
        except Exception:
            pass
        
        time.sleep(1)
        
        if proc.poll() is None:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGKILL)
                print(f"  [強制終了完了] {name} (PID: {proc.pid})")
            except Exception:
                pass
        
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass

    def kill_client(self):
        if self.client_proc is not None:
            self._kill_process_group(self.client_proc, "Script Client")
            self.client_proc = None

    def kill_infra(self):
        print("\n=== システム全停止（リセット） ===")
        self.kill_client()
        for name, proc in reversed(self.infra_procs):
            self._kill_process_group(proc, name)
        self.infra_procs = []
        
        print("  [待機] プロセスの完全終了とポート解放を待機中...")
        time.sleep(3)

    def count_target_files(self):
        search_path = os.path.join(OUTPUT_DIR, FILE_PATTERN)
        all_files = glob.glob(search_path)
        valid_files = [f for f in all_files if "footage" not in os.path.basename(f)]
        return len(valid_files)

    def execute(self):
        print("=== テスト自動化システム（定期リフレッシュ機能搭載版） ===")
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        current_sim_idx = 0 

        # 外側ループ（インフラ管理）
        while current_sim_idx < REPEAT_COUNT:
            sim_num = current_sim_idx + 1

            if not self.infra_procs:
                print(f"\n=== Phase 1: システム起動 (Start from sim_num={sim_num}) ===")
                for task in INFRA_TASKS:
                    p = self._start_process(task, sim_num)
                    if p: self.infra_procs.append((task.name, p))
            
            # 内側ループ（テスト連続実行）
            while current_sim_idx < REPEAT_COUNT:
                current_loop_num = current_sim_idx + 1
                
                print(f"\n---------------------------------------------")
                print(f" Loop {current_loop_num} / {REPEAT_COUNT} 開始")
                print(f"---------------------------------------------")

                # ステップ1：今回ターゲットとなる「ファイル名」を作る
                target_json = os.path.join(OUTPUT_DIR, f"uturn_test_sim{current_loop_num}.json")

                # ステップ2：実行前に、もし前回の残骸があれば「消す」
                if os.path.exists(target_json):
                    os.remove(target_json)
                    print("前回の残骸ファイルをクリアしました")
                
                # ファジング（ランダムパラメータ）
                r_dx0 = random.uniform(*uturn_config.PARAM_RANGES["dx0"])
                r_ego_speed = random.uniform(*uturn_config.PARAM_RANGES["ego_speed"])
                r_npc_speed = random.uniform(*uturn_config.PARAM_RANGES["npc_speed"])

                print(f"  [生成パラメータ] 距離: {r_dx0:.2f}m | 自車: {r_ego_speed:.1f}km/h | 他車: {r_npc_speed:.1f}km/h")

                # =========================================================
                # ★ 修正: 外部モジュールを使った汎用的なパラメータ記録
                # =========================================================
                # 記録したいパラメータを辞書（dict）形式にまとめる
                # 別のシナリオで項目名が変わっても、ここを変更するだけでOK
                current_params = {
                    "dx0": r_dx0,
                    "ego_speed": r_ego_speed,
                    "npc_speed": r_npc_speed
                }
                
                # シナリオ名を含めたCSVファイル名にする（例: uturn_parameters.csv）
                csv_filename = f"{uturn_config.SCENARIO_TYPE}_parameters.csv"
                
                # 汎用ロガーを呼び出して記録
                log_parameters(OUTPUT_DIR, csv_filename, current_loop_num, current_params)
                # =========================================================

                dynamic_cmd = (
                    f"python3 run_scenario.py "
                    f"--type {uturn_config.SCENARIO_TYPE} "
                    f"--dx0 {r_dx0:.2f} "
                    f"--ego_speed {r_ego_speed:.2f} "
                    f"--npc_speed {r_npc_speed:.2f}"
                )

                dynamic_client_task = Task(
                    name="Dynamic Scenario Runner",
                    work_dir=LAUNCH_DIR,
                    command=dynamic_cmd,
                    delay=0,
                    source_setup=True
                )

                self._run_trigger_once(dynamic_client_task, current_loop_num)

                print(f"  >>> 実行中... (Monitorの検知待ち, Timeout: {TIMEOUT_SEC}s)")
                start_wait = time.time()
                is_timeout = False
                
                while True:
                    time.sleep(2)

                    # ステップ3：そのファイルが「現れたか」だけを見張る
                    if os.path.exists(target_json):
                        print(f"成功: {os.path.basename(target_json)} の新規生成を確認！")
                        time.sleep(3) 
                        break 

                    if time.time() - start_wait > TIMEOUT_SEC:
                        print(f"  [警告] タイムアウト ({TIMEOUT_SEC}秒)")
                        is_timeout = True
                        break 

                if is_timeout:
                    print(f"  !!! 失敗 (Loop {current_loop_num}): タイムアウトのためやり直します !!!")
                    self.kill_infra() 
                    break  # 内側を抜け、外側で起動し直す
                else:
                    print("  [完了] シナリオ正常終了")
                    self.kill_client()
                    current_sim_idx += 1
                    
                    # =========================================================
                    # ★ 追加: 10回に1回の定期リフレッシュ (再起動) ロジック
                    # =========================================================
                    if current_sim_idx % REFRESH_INTERVAL == 0 and current_sim_idx < REPEAT_COUNT:
                        print(f"\n  [定期リフレッシュ] {current_sim_idx}回のテストが完了しました。")
                        print("  システムの安定稼働（メモリ解放・DDSリセット）のため、インフラを再起動します。")
                        self.kill_infra()
                        break  # 内側を抜け、外側でクリーンなインフラを立ち上げ直す
                    
                    time.sleep(INTERVAL_SEC)

        self.cleanup_all()

    def cleanup_all(self):
        print("\n=== 全工程終了 ===")
        self.kill_infra()
        print("完了。")

if __name__ == "__main__":
    manager = ProcessManager()
    try:
        manager.execute()
    except KeyboardInterrupt:
        print("\n[!] ユーザーによる中断 (Ctrl+C)")
        manager.cleanup_all()
        time.sleep(1)
        sys.exit(1)
