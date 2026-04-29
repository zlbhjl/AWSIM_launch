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
import json
import csv
import ray
from dataclasses import dataclass
from typing import List, Tuple, Optional

# ==============================================================================
# 1. 引数解析と設定の動的読み込み
# ==============================================================================
def load_config():
    parser = argparse.ArgumentParser(description="Multi-Scenario Autonomous Driving Test Manager")
    parser.add_argument("--type", type=str, default="uturn", help="Scenario type (e.g., uturn, cutin)")
    parser.add_argument("--mode", type=str, choices=["explore", "focus"], default="explore", help="Search mode: explore (default) or focus")
    parser.add_argument("--focus_points", type=str, default=None, help="JSON string for focus points (e.g., '[{\"dx0\": 15.0}]')")
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

    return args.type, config_module, args.mode, focus_points

SCENARIO_NAME, cfg, RUN_MODE, FOCUS_POINTS = load_config()

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
    resident: bool = False

# ==============================================================================
# [追加] 実行号機(マスターかリモートか)の判定と起動コマンドの分岐
# ==============================================================================
ROS_DOMAIN_ID = os.environ.get("ROS_DOMAIN_ID", "0")
IS_MASTER = (ROS_DOMAIN_ID == "21")

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
    Task(
        name="AWSIM Labs",
        work_dir=os.path.join(HOME, "awsim_labs"),
        command=AWSIM_CMD,
        delay=15
    ),
    Task(
        name="Autoware",
        work_dir=os.path.join(HOME, "autoware"),
        command=AUTOWARE_CMD,
        delay=AW_DELAY,
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
    Task(
        name="AW Checker (Safety Evaluator)",
        work_dir=LAUNCH_DIR,
        command=f"python3 awchecker.py --type {SCENARIO_NAME}",
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
    def __init__(self):
        self.infra_procs: List[Tuple[str, subprocess.Popen]] = []
        self.resident_procs: List[Tuple[str, subprocess.Popen]] = []
        self.client_proc: Optional[subprocess.Popen] = None
        
        # --- [分散対応] 司令塔 (Master) のキューに接続 ---
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

    def _build_command(self, task: Task, sim_num: int) -> str:
        cmd = task.command.replace("{sim_num}", str(sim_num))
        if task.source_setup:
            cmd = f"source {SETUP_BASH} && {cmd}"
        return cmd

    def _start_process(self, task: Task, sim_num: int) -> Optional[subprocess.Popen]:
        full_cmd = self._build_command(task, sim_num)
        print(f"  [起動] {task.name} ... ", end="", flush=True)
        
        # [修正] ログを破棄せず、専用ファイルに隔離してエラー調査を行えるようにする
        out_target = subprocess.DEVNULL
        if task.name == "AWSIM Labs":
            out_target = open(os.path.join(OUTPUT_DIR, "awsim.log"), "w")
        elif task.name == "Autoware":
            out_target = open(os.path.join(OUTPUT_DIR, "autoware.log"), "w")
            
        try:
            proc = subprocess.Popen(
                ["/bin/bash", "-i", "-c", full_cmd],
                cwd=task.work_dir,
                preexec_fn=os.setsid,
                stdout=out_target,
                stderr=out_target
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
                ["/bin/bash", "-i", "-c", full_cmd],
                cwd=task.work_dir,
                preexec_fn=os.setsid,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
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

    def kill_all_processes(self, kill_resident=False):
        print("\n=== システム停止処理 ===")
        if self.client_proc:
            self._send_signal(self.client_proc, "Script Client", signal.SIGINT)
        for name, proc in reversed(self.infra_procs):
            self._send_signal(proc, name, signal.SIGINT)
        if kill_resident:
            for name, proc in reversed(self.resident_procs):
                self._send_signal(proc, name, signal.SIGINT)
        
        time.sleep(3)

        if self.client_proc:
            self._send_signal(self.client_proc, "Script Client", signal.SIGKILL)
        for name, proc in reversed(self.infra_procs):
            self._send_signal(proc, name, signal.SIGKILL)
        if kill_resident:
            for name, proc in reversed(self.resident_procs):
                self._send_signal(proc, name, signal.SIGKILL)
            self.resident_procs = []

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
        self.kill_all_processes(kill_resident=False)
        self._force_cleanup_os()

    def count_target_files(self):
        search_path = os.path.join(OUTPUT_DIR, FILE_PATTERN)
        all_files = glob.glob(search_path)
        valid_files = [f for f in all_files if "footage" not in os.path.basename(f)]
        return len(valid_files)

    def execute(self):
        print(f"=== 自動化システム [{SCENARIO_NAME.upper()} モード] ===")
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        # [分散対応] マスターがCSVを管理するため、ワーカーによる勝手な初期化処理を廃止
        print(f"[System] 分散ワーカーとして待機を開始します。")
        
        local_exec_count = 0  # 定期リフレッシュ(掃除)のタイミングを計るためのローカルカウンタ
        local_total_count = 0 # [追加] コンテナ内での累積シミュレーション実行回数
        
        while True:

            if not self.infra_procs:
                print(f"\n--- システムインフラ起動 ---")
                for task in INFRA_TASKS:
                    # Runtime Monitor が出力するファイル番号の起点を合わせる
                    if task.resident:
                        if not any(name == task.name for name, _ in self.resident_procs):
                            p = self._start_process(task, local_total_count + 1)
                            if p: self.resident_procs.append((task.name, p))
                    else:
                        p = self._start_process(task, local_total_count + 1)
                        if p: self.infra_procs.append((task.name, p))
            
            while True:
                # --- [分散対応] 司令塔から次のパラメータを取得 ---
                print(f"  [Manager] 司令塔から次のタスク(パラメータ)を待機中...")
                while True:
                    try:
                        # キューからタスクを要求（なければ None が返ってくる設計）
                        next_target = ray.get(self.task_queue.get_next_task.remote())
                        if next_target is not None:
                            break
                    except Exception as e:
                        print(f"  [警告] 司令塔との通信エラー（数秒後に再試行します）: {e}")
                    time.sleep(2)
                
                # === 追加: 堅牢な終了シグナル検知ロジック ===
                # 辞書から 'system_command' を安全に取得し、"stop" かどうか判定する
                if next_target.get("system_command") == "stop":
                    print(f"\n[Manager] 🏁 Strategistから終了シグナルを受信しました。({next_target.get('reason')})")
                    print("  -> 現存の終了プロセス（cleanup_all）に移行し、システムを安全に停止します...")
                    return  # execute関数を抜け、一番下の finally ブロック（cleanup_all）へ直行させる
                # ==========================================

                # パラメータの抽出と reason の安全な分離
                # pop() を使うことで、next_target 辞書から 'reason' を抜き取りつつ削除します。
                # これにより、シミュレータに渡す引数に文字列が混ざるのを防ぎます。
                reason_str = next_target.pop("reason", "")
                
                # --- [修正] マスターが発行したグローバルIDを取得 ---
                current_loop_num = next_target.pop("global_loop_num", local_total_count + 1)
                print(f"\n--- Global Task ID {current_loop_num} ---")
                
                expected_local_num = local_total_count + 1
                local_target_json = os.path.join(OUTPUT_DIR, f"{SCENARIO_NAME}_test_sim{expected_local_num}.json")
                if os.path.exists(local_target_json):
                    os.remove(local_target_json)
                    
                global_target_json = os.path.join(OUTPUT_DIR, f"{SCENARIO_NAME}_eval_sim{current_loop_num}.json")
                if os.path.exists(global_target_json):
                    os.remove(global_target_json)
                
                # [追加] 関連する動画とメタデータの掃除
                local_prefix = os.path.join(OUTPUT_DIR, f"{SCENARIO_NAME}_test_sim{expected_local_num}_footage")
                global_prefix = os.path.join(OUTPUT_DIR, f"{SCENARIO_NAME}_eval_sim{current_loop_num}_footage")
                for ext in [".mp4", ".meta.json"]:
                    if os.path.exists(local_prefix + ext): os.remove(local_prefix + ext)
                    if os.path.exists(global_prefix + ext): os.remove(global_prefix + ext)
                
                csv_filename = f"{SCENARIO_NAME}_parameters.csv"

                # 引数の動的生成 (この時点で next_target には数値パラメータしか残っていない)
                param_args = " ".join([f"--{k} {v:.2f}" for k, v in next_target.items()])
                dynamic_cmd = f"python3 run_scenario.py --type {SCENARIO_NAME} {param_args}"

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
                    if os.path.exists(local_target_json):
                        print(f"  [成功] {os.path.basename(local_target_json)} 生成確認")
                        time.sleep(5)  # JSON書き込み完了を待機
                        os.rename(local_target_json, global_target_json) # グローバルIDに合わせる
                        print(f"  [変換] -> {os.path.basename(global_target_json)} にリネーム完了")
                        
                        # [追加] 動画とメタデータも漏れなくグローバルIDにリネームする
                        for ext in [".mp4", ".meta.json"]:
                            if os.path.exists(local_prefix + ext):
                                os.rename(local_prefix + ext, global_prefix + ext)
                                
                        break 
                    if time.time() - start_wait > TIMEOUT_SEC:
                        print(f"  [警告] タイムアウト")
                        is_timeout = True
                        break 

                if is_timeout:
                    # タイムアウト(失敗)時もパラメータを記録し、理由にエラーを明記する
                    failed_reason = reason_str + " [ERROR: TIMEOUT]"
                    log_parameters(OUTPUT_DIR, csv_filename, current_loop_num, next_target, reason=failed_reason)
                    
                    # タイムアウト時はダミーファイルを生成
                    with open(global_target_json, 'w') as f:
                        f.write("TIMEOUT")
                        
                    local_exec_count += 1
                    local_total_count += 1
                    
                    # マスターへ完了（タイムアウト）を報告
                    try: ray.get(self.task_queue.report_completion.remote(current_loop_num, "timeout"))
                    except Exception: pass
                    
                    self.kill_infra() 
                    break 
                else:
                    self.kill_client()
                    log_parameters(OUTPUT_DIR, csv_filename, current_loop_num, next_target, reason=reason_str)
                    local_exec_count += 1
                    local_total_count += 1
                    
                    # マスターへ完了（成功）を報告
                    try: ray.get(self.task_queue.report_completion.remote(current_loop_num, "success"))
                    except Exception: pass
                    
                    if local_exec_count % REFRESH_INTERVAL == 0:
                        print(f"\n  [定期リフレッシュ] インフラを再起動します。")
                        self.kill_infra()
                        break 
                    time.sleep(INTERVAL_SEC)

    def cleanup_all(self):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        self.kill_all_processes(kill_resident=True)
        self._force_cleanup_os()
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
