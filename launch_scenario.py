#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import time
import os
import signal
import sys
from dataclasses import dataclass

# ==============================================================================
# 1. コンフィグレーション（ここだけ編集すればOKです）
# ==============================================================================

# 共通設定: ホームディレクトリの取得
HOME = os.path.expanduser("~")
SETUP_BASH = os.path.join(HOME, "autoware/install/setup.bash")

@dataclass
class Task:
    """1つの実行タスクを定義するデータ構造"""
    name: str           # 表示名
    work_dir: str       # 実行するディレクトリ
    command: str        # 実行するコマンド
    delay: int = 2      # 起動後の待機時間（秒）
    source_setup: bool = False # setup.bashを読み込むか

# 実行するシナリオのリスト（上から順に実行されます）
SCENARIO_CONFIG = [
    # 1. AWSIM (Simulator)
    Task(
        name="AWSIM Labs",
        work_dir=os.path.join(HOME, "awsim_labs"),
        command="./awsim_labs.x86_64 -noise false",
        delay=10,
        source_setup=False
    ),
    # 2. Autoware (Autonomous Driving Stack)
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
        delay=15,
        source_setup=True
    ),
    # 3. AW-Runtime-Monitor (Verification Tool)
    Task(
        name="Runtime Monitor",
        work_dir=os.path.join(HOME, "AW-Runtime-Monitor"),
        # venvのactivateも含めてコマンド化
        command=(
            "source .venv/bin/activate && "
            f"python3 main.py -o {HOME}/simulation_traces/uturn_test -v false"
        ),
        delay=5,
        source_setup=True
    ),
    # 4. Script Client (Scenario Controller)
    Task(
        name="Script Client",
        work_dir=os.path.join(HOME, "AWSIM-Script-Client"),
        command=f"python3 client.py {HOME}/awscript/example_scenario.script",
        delay=2,
        source_setup=True
    ),
]

# ==============================================================================
# 2. プロセスマネージャークラス（ロジック部分）
# ==============================================================================

class ProcessManager:
    def __init__(self):
        self.processes = [] # 実行中のプロセスリスト

    def _build_command(self, task: Task) -> str:
        """コマンドを組み立てる（setup.bashの読み込みなど）"""
        cmd = task.command
        if task.source_setup:
            cmd = f"source {SETUP_BASH} && {cmd}"
        return cmd

    def launch_task(self, task: Task):
        """1つのタスクをバックグラウンドで起動する"""
        full_cmd = self._build_command(task)
        print(f"[{task.name}] 起動中... (待機: {task.delay}秒)")

        try:
            # プロセスグループを作成して起動
            proc = subprocess.Popen(
                full_cmd,
                cwd=task.work_dir,
                shell=True,
                executable="/bin/bash",
                preexec_fn=os.setsid
            )
            self.processes.append((task.name, proc))

            # 指定時間待機
            if task.delay > 0:
                time.sleep(task.delay)

        except Exception as e:
            print(f"Error: [{task.name}] の起動に失敗しました: {e}")

    def run_scenario(self, config_list):
        """シナリオ全体を実行する"""
        print("=== シナリオ起動プロセスを開始します ===")
        for task in config_list:
            self.launch_task(task)
        print("\n=== 全システム起動完了 (Ctrl+C で一括終了) ===")
        self._wait_forever()

    def _wait_forever(self):
        """終了シグナルが来るまで待機"""
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.cleanup()

    def cleanup(self):
        """全プロセスをグループごと終了させる"""
        print("\n\n=== 終了処理を開始します ===")
        for name, proc in self.processes:
            try:
                print(f"停止中: {name}...")
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                pass # 既に終了している場合は無視
        print("=== 全プロセスを終了しました ===")

# ==============================================================================
# 3. メイン実行部
# ==============================================================================

if __name__ == "__main__":
    manager = ProcessManager()
    manager.run_scenario(SCENARIO_CONFIG)