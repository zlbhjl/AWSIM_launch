#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import time
import os
import signal
import glob
from dataclasses import dataclass
from typing import List, Tuple, Optional

# ==============================================================================
# 1. コンフィグレーション
# ==============================================================================
HOME = os.path.expanduser("~")
SETUP_BASH = os.path.join(HOME, "autoware/install/setup.bash")

# ★ 繰り返し回数
REPEAT_COUNT = 20

# ★ 監視設定
OUTPUT_DIR = os.path.join(HOME, "simulation_traces")
FILE_PATTERN = "uturn_test_*.json"
TIMEOUT_SEC = 300  # タイムアウト時間

# ★ リセット後の休憩時間
INTERVAL_SEC = 1

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

# 【B】トリガー（シナリオ開始コマンド）
CLIENT_TASK = Task(
    name="Script Client",
    work_dir=os.path.join(HOME, "AWSIMScriptPy"), 
    command="python3 -m scenarios.uturn.uturn_Collision2",
    delay=0,
    source_setup=True
)

# ==============================================================================
# 2. プロセスマネージャー
# ==============================================================================

class ProcessManager:
    def __init__(self):
        # インフラプロセスリスト
        self.infra_procs: List[Tuple[str, subprocess.Popen]] = []
        # ★追加: クライアントプロセスを単独で保持する変数
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
        """クライアントコマンドを実行し、そのプロセスを保持する"""
        
        # もし前のプロセスが残っていたら念の為殺しておく
        self.kill_client()

        full_cmd = self._build_command(task, sim_num)
        print(f"  [入力] {task.name} コマンド実行")
        try:
            # ★変更点: プロセスを変数で受け取る
            self.client_proc = subprocess.Popen(
                full_cmd, cwd=task.work_dir, shell=True,
                executable="/bin/bash", preexec_fn=os.setsid
            )
        except Exception as e:
            print(f"  [エラー] コマンド送信失敗: {e}")

    def _kill_process_group(self, pid, name):
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            print(f"  [停止] {name}")
        except:
            pass

    def kill_client(self):
        """クライアントプロセス単体を停止する"""
        if self.client_proc is not None:
            # プロセスがまだ動いていれば停止
            if self.client_proc.poll() is None:
                self._kill_process_group(self.client_proc.pid, "Script Client")
            self.client_proc = None

    def kill_infra(self):
        print("\n=== システム全停止（リセット） ===")
        
        # 1. まずクライアント（シナリオ）を確実に殺す
        self.kill_client()

        # 2. 次にインフラ（AWSIM, Autoware, Monitor）を殺す
        for name, proc in reversed(self.infra_procs):
            self._kill_process_group(proc.pid, name)
        self.infra_procs = []

    def count_target_files(self):
        search_path = os.path.join(OUTPUT_DIR, FILE_PATTERN)
        all_files = glob.glob(search_path)
        valid_files = [f for f in all_files if "footage" not in os.path.basename(f)]
        return len(valid_files)

    def execute(self):
        print("=== テスト自動化システム（Client管理強化版） ===")
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        current_sim_idx = 0 

        while current_sim_idx < REPEAT_COUNT:
            
            sim_num = current_sim_idx + 1

            if not self.infra_procs:
                print(f"\n=== Phase 1: システム起動 (Start from sim_num={sim_num}) ===")
                for task in INFRA_TASKS:
                    p = self._start_process(task, sim_num)
                    if p: self.infra_procs.append((task.name, p))
            
            while current_sim_idx < REPEAT_COUNT:
                current_loop_num = current_sim_idx + 1
                
                print(f"\n---------------------------------------------")
                print(f" Loop {current_loop_num} / {REPEAT_COUNT} 開始")
                print(f"---------------------------------------------")

                initial_count = self.count_target_files()
                
                # Clientを実行（同時にプロセスIDも self.client_proc に保存）
                self._run_trigger_once(CLIENT_TASK, current_loop_num)

                print(f"  >>> 実行中... (Monitorの検知待ち, Timeout: {TIMEOUT_SEC}s)")
                start_wait = time.time()
                is_timeout = False
                
                while True:
                    time.sleep(2)
                    current_count = self.count_target_files()

                    if current_count > initial_count:
                        print(f"  >>> 成功: ファイル生成を確認（{current_count}個）")
                        time.sleep(3) 
                        break 

                    if time.time() - start_wait > TIMEOUT_SEC:
                        print(f"  [警告] タイムアウト ({TIMEOUT_SEC}秒)")
                        is_timeout = True
                        break 

                if is_timeout:
                    print(f"  !!! 失敗 (Loop {current_loop_num}): 同じ番号でやり直します !!!")
                    # kill_infra の中で kill_client も呼ばれるので、ゾンビプロセスは残りません
                    self.kill_infra() 
                    break 
                else:
                    print("  [完了] 次のループへ進みます")
                    # 成功した場合も、念の為Client変数はクリアしておく（プロセス自体は終了しているはずだが）
                    self.kill_client()
                    current_sim_idx += 1
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
        print("\nユーザー中断")
        manager.cleanup_all()
