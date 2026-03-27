#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import time
import os
import signal
import glob
import sys
import argparse
import importlib
from dataclasses import dataclass
from typing import List, Tuple, Optional

# ==============================================================================
# 1. 引数解析と設定の動的読み込み
# ==============================================================================
def load_config():
    parser = argparse.ArgumentParser(description="Multi-Scenario Autonomous Driving Test Manager")
    parser.add_argument("--type", type=str, default="uturn", help="Scenario type (e.g., uturn, cutin)")
    args = parser.parse_args()

    try:
        # configs フォルダ内のモジュールを動的にインポート
        config_module = importlib.import_module(f"configs.{args.type}")
        print(f"[System] シナリオ設定 'configs.{args.type}' を正常に読み込みました。")
        return args.type, config_module
    except ImportError:
        print(f"[Fatal] 設定ファイル configs/{args.type}.py が見つかりません。")
        print("  -> configs/ フォルダ内にファイルがあるか、__init__.py が存在するか確認してください。")
        sys.exit(1)

SCENARIO_NAME, cfg = load_config()

LAUNCH_DIR = os.path.dirname(os.path.abspath(__file__))
if LAUNCH_DIR not in sys.path:
    sys.path.append(LAUNCH_DIR)

from param_logger import log_parameters
from strategist import ActiveLearningStrategist

# ==============================================================================
# 2. コンフィグレーション
# ==============================================================================
HOME = os.path.expanduser("~")
SETUP_BASH = os.path.join(HOME, "autoware/install/setup.bash")

REPEAT_COUNT = cfg.REPEAT_COUNT
OUTPUT_DIR = os.path.join(HOME, "simulation_traces")
FILE_PATTERN = f"{SCENARIO_NAME}_test_*.json"
TIMEOUT_SEC = 300
INTERVAL_SEC = 1
REFRESH_INTERVAL = 10

@dataclass
class Task:
    name: str
    work_dir: str
    command: str
    delay: int = 2
    source_setup: bool = False

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
            f"python3 main.py -o {os.path.join(OUTPUT_DIR, SCENARIO_NAME + '_test')} "
            "-n {sim_num}"
        ),
        delay=5,
        source_setup=True
    ),
]

# ==============================================================================
# 3. プロセスマネージャー
# ==============================================================================
class ProcessManager:
    def __init__(self):
        self.infra_procs: List[Tuple[str, subprocess.Popen]] = []
        self.client_proc: Optional[subprocess.Popen] = None
        self.strategist = ActiveLearningStrategist(SCENARIO_NAME, cfg, num_candidates=10000)

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

    def _send_signal(self, proc: subprocess.Popen, name: str, sig: int):
        if proc is None or proc.poll() is not None:
            return
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, sig)
        except Exception:
            pass

    def kill_all_processes(self):
        print("\n=== システム停止処理 ===")
        if self.client_proc:
            self._send_signal(self.client_proc, "Script Client", signal.SIGINT)
        for name, proc in reversed(self.infra_procs):
            self._send_signal(proc, name, signal.SIGINT)
        
        time.sleep(3)

        if self.client_proc:
            self._send_signal(self.client_proc, "Script Client", signal.SIGKILL)
        for name, proc in reversed(self.infra_procs):
            self._send_signal(proc, name, signal.SIGKILL)

        self.client_proc = None
        self.infra_procs = []

    def kill_client(self):
        if self.client_proc:
            self._send_signal(self.client_proc, "Script Client", signal.SIGKILL)
            self.client_proc = None

    def _force_cleanup_os(self):
        print("  [徹底掃除] 残存プロセスと共有メモリを浄化中...")
        targets = ["awsim_labs.x86_64", "run_scenario.py", "component_container", "rviz2", "autoware", "ros2"]
        for target in targets:
            os.system(f"pkill -15 -f {target} > /dev/null 2>&1")
        time.sleep(1)
        for target in targets:
            os.system(f"pkill -9 -f {target} > /dev/null 2>&1")
        os.system("ros2 daemon stop > /dev/null 2>&1")
        os.system("rm -f /dev/shm/ros2* > /dev/null 2>&1")
        os.system("rm -f /dev/shm/fastrtps* > /dev/null 2>&1")

    def kill_infra(self):
        self.kill_all_processes()
        self._force_cleanup_os()

    def count_target_files(self):
        search_path = os.path.join(OUTPUT_DIR, FILE_PATTERN)
        all_files = glob.glob(search_path)
        valid_files = [f for f in all_files if "footage" not in os.path.basename(f)]
        return len(valid_files)

    def execute(self):
        print(f"=== 自動化システム [{SCENARIO_NAME.upper()} モード] ===")
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        # --- [修正箇所1] 古いCSVファイルのクリーンアップ確認 ---
        csv_param = os.path.join(OUTPUT_DIR, f"{SCENARIO_NAME}_parameters.csv")
        csv_result = os.path.join(OUTPUT_DIR, "checker_results.csv")
        
        current_sim_idx = self.count_target_files()
        
        if current_sim_idx == 0:
            print("[System] 新規実行を検出しました。古いCSVログを初期化します。")
            if os.path.exists(csv_param):
                os.remove(csv_param)
            if os.path.exists(csv_result):
                os.remove(csv_result)
        else:
            print(f"[System] 既存のデータ (sim1 〜 sim{current_sim_idx}) から再開します。")
        # ---------------------------------------------------
        
        while current_sim_idx < REPEAT_COUNT:
            sim_num = current_sim_idx + 1

            if not self.infra_procs:
                print(f"\n--- システム起動 (Loop {sim_num}) ---")
                for task in INFRA_TASKS:
                    p = self._start_process(task, sim_num)
                    if p: self.infra_procs.append((task.name, p))
            
            while current_sim_idx < REPEAT_COUNT:
                current_loop_num = current_sim_idx + 1
                print(f"\n--- Loop {current_loop_num} / {REPEAT_COUNT} ---")

                target_json = os.path.join(OUTPUT_DIR, f"{SCENARIO_NAME}_test_sim{current_loop_num}.json")

                if os.path.exists(target_json):
                    os.remove(target_json)
                
                # Strategist に次期作戦を要求
                next_target = self.strategist.decide_next_target()
                
                # --- [修正箇所2] パラメータの抽出と reason の安全な分離 ---
                # pop() を使うことで、next_target 辞書から 'reason' を抜き取りつつ削除します。
                # これにより、シミュレータに渡す引数に文字列が混ざるのを防ぎます。
                reason_str = next_target.pop("reason", "")
                
                csv_filename = f"{SCENARIO_NAME}_parameters.csv"
                # reason は別引数として param_logger に渡す
                log_parameters(OUTPUT_DIR, csv_filename, current_loop_num, next_target, reason=reason_str)

                # 引数の動的生成 (この時点で next_target には数値パラメータしか残っていない)
                param_args = " ".join([f"--{k} {v:.2f}" for k, v in next_target.items()])
                dynamic_cmd = f"python3 run_scenario.py --type {SCENARIO_NAME} {param_args}"
                # ---------------------------------------------------

                dynamic_client_task = Task(
                    name=f"Runner ({SCENARIO_NAME})",
                    work_dir=LAUNCH_DIR,
                    command=dynamic_cmd,
                    delay=0,
                    source_setup=True
                )

                self._run_trigger_once(dynamic_client_task, current_loop_num)

                print(f"  >>> 監視中... (Timeout: {TIMEOUT_SEC}s)")
                start_wait = time.time()
                is_timeout = False
                
                while True:
                    time.sleep(2)
                    if os.path.exists(target_json):
                        print(f"  [成功] {os.path.basename(target_json)} 生成確認")
                        time.sleep(5) 
                        break 
                    if time.time() - start_wait > TIMEOUT_SEC:
                        print(f"  [警告] タイムアウト")
                        is_timeout = True
                        break 

                if is_timeout:
                    self.kill_infra() 
                    break 
                else:
                    self.kill_client()
                    current_sim_idx += 1
                    
                    if current_sim_idx % REFRESH_INTERVAL == 0 and current_sim_idx < REPEAT_COUNT:
                        print(f"\n  [定期リフレッシュ] インフラを再起動します。")
                        self.kill_infra()
                        break 
                    time.sleep(INTERVAL_SEC)

    def cleanup_all(self):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        self.kill_infra()
        print("=== 全工程終了 ===")

if __name__ == "__main__":
    manager = ProcessManager()
    try:
        manager.execute()
    except KeyboardInterrupt:
        print("\n[!] ユーザーによる中断")
    finally:
        manager.cleanup_all()
        sys.exit(0)
