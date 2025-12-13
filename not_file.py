#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import time
import os
import signal
import glob
from dataclasses import dataclass

# ==============================================================================
# 1. コンフィグレーション
# ==============================================================================
HOME = os.path.expanduser("~")
SETUP_BASH = os.path.join(HOME, "autoware/install/setup.bash")

# ★ 繰り返し回数
REPEAT_COUNT = 6

# ★ 監視設定
# 画像に基づき、結果ファイルが保存される「親フォルダ」を指定
OUTPUT_DIR = os.path.join(HOME, "simulation_traces")
# 監視するファイル名のパターン（* はワイルドカード）
# これで uturn_test_sim1.json, sim2.json... をすべて捉えます
FILE_PATTERN = "uturn_test_*.json"

# ★ リセット後の休憩時間
INTERVAL_SEC = 5

@dataclass
class Task:
    name: str
    work_dir: str
    command: str
    delay: int = 2
    source_setup: bool = False

# 【A】常駐（AWSIM）
PERMANENT_SERVICES = [
    Task(
        name="AWSIM Labs",
        work_dir=os.path.join(HOME, "awsim_labs"),
        command="./awsim_labs.x86_64 -noise false",
        delay=15
    ),
]

# 【B】ループ実行（Autoware + Monitor + Client）
LOOP_TASKS = [
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
        # Monitorには「ベースの名前」を渡しておけば、ツール側で自動で _sim1, _sim2 をつけてくれる想定
        command=(
            "source .venv/bin/activate && "
            f"python3 main.py -o {os.path.join(OUTPUT_DIR, 'uturn_test')} -v false"
        ),
        delay=5,
        source_setup=True
    ),
    Task(
        name="Script Client",
        work_dir=os.path.join(HOME, "AWSIM-Script-Client"),
        command=f"python3 client.py {HOME}/awscript/example_scenario.script",
        delay=0,
        source_setup=True
    ),
]

# ==============================================================================
# 2. プロセスマネージャー（ファイル数カウント版）
# ==============================================================================

class ProcessManager:
    def __init__(self):
        self.permanent_procs = []
        self.loop_procs = []

    def _build_command(self, task: Task) -> str:
        cmd = task.command
        if task.source_setup:
            cmd = f"source {SETUP_BASH} && {cmd}"
        return cmd

    def _start_process(self, task: Task):
        full_cmd = self._build_command(task)
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

    def _kill_process_group(self, pid, name):
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except:
            pass

    def count_target_files(self):
        """監視対象のファイル数を数える"""
        search_path = os.path.join(OUTPUT_DIR, FILE_PATTERN)
        files = glob.glob(search_path)
        return len(files)

    def execute(self):
        print("=== テスト自動化システム（ファイル数増減検知型） ===")
        print(f"監視対象: {os.path.join(OUTPUT_DIR, FILE_PATTERN)}")

        # 1. 常駐プロセス
        print("\n=== Phase 1: AWSIM起動 (常駐) ===")
        for task in PERMANENT_SERVICES:
            p = self._start_process(task)
            if p: self.permanent_procs.append((task.name, p))

        # 2. ループ
        print(f"\n=== Phase 2: ループ実行開始 ({REPEAT_COUNT}回) ===")

        try:
            for i in range(REPEAT_COUNT):
                print(f"\n---------------------------------------------")
                print(f" Loop {i+1} / {REPEAT_COUNT} 開始")
                print(f"---------------------------------------------")

                # A. 現在のファイル数を記録
                initial_count = self.count_target_files()
                print(f"  [確認] 現在の完了ファイル数: {initial_count} 個")

                # B. プロセス起動
                self.loop_procs = []
                for task in LOOP_TASKS:
                    proc = self._start_process(task)
                    if proc:
                        self.loop_procs.append((task.name, proc))

                # C. 監視ループ（ファイルが増えるのを待つ）
                print(f"  >>> シナリオ実行中... (ファイルが増えるのを待っています)")
                start_wait = time.time()

                while True:
                    time.sleep(2) # 2秒おきにチェック

                    current_count = self.count_target_files()

                    # ファイル数が増えていれば成功
                    if current_count > initial_count:
                        print(f"  >>> 検知: ファイル数が {initial_count} -> {current_count} に増えました！")
                        print("      (データの書き込み完了を3秒待ちます...)")
                        time.sleep(3)
                        break

                    # タイムアウト安全装置（例: 5分経っても増えなければ強制リセット）
                    if time.time() - start_wait > 300:
                        print("  [警告] タイムアウト: 5分経過してもファイルが増えませんでした。")
                        break

                # D. クリーンアップ
                print(f"  [リセット] Loop {i+1} 終了。プロセスを全消去します...")
                for name, proc in reversed(self.loop_procs):
                    self._kill_process_group(proc.pid, name)

                print(f"  [待機] {INTERVAL_SEC}秒...")
                time.sleep(INTERVAL_SEC)

        except KeyboardInterrupt:
            print("\nユーザー中断")
        finally:
            self.cleanup_all()

    def cleanup_all(self):
        print("\n=== 全システム完全終了 ===")
        for name, proc in self.loop_procs:
            self._kill_process_group(proc.pid, name)
        for name, proc in self.permanent_procs:
            self._kill_process_group(proc.pid, name)
        print("完了。")

if __name__ == "__main__":
    manager = ProcessManager()
    manager.execute()