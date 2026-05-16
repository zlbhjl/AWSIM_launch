#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import importlib
import json
import sys
import os
import time
import ray
import csv
import subprocess

# パスの追加
LAUNCH_DIR = os.path.dirname(os.path.abspath(__file__))
if LAUNCH_DIR not in sys.path:
    sys.path.append(LAUNCH_DIR)

from redis_cluster.cluster_manager import ClusterManager
from redis_cluster.shared_store import SharedStoreActor
from strategist import ActiveLearningStrategist

# ==============================================================================
# TaskQueueActor (Ray Actor)
# ワーカーからのアクセスをスレッドセーフに受け付けるキュー管理役
# ==============================================================================
@ray.remote
class TaskQueueActor:
    def __init__(self):
        self.queue = []
        self.completed_count = 0
        self.stop_signal = False
        self.dispatched_count = 0  # [追加] マスターが発行する絶対的なループ番号
        self.worker_statuses = {}  # [追加] 各ワーカーのリアルタイムな状態

    def update_worker_status(self, worker_id: str, status: str):
        self.worker_statuses[worker_id] = status

    def add_task(self, task: dict):
        self.queue.append(task)

    def get_next_task(self):
        if self.stop_signal:
            return {"system_command": "stop", "reason": "Target Reached or Master Stopped"}
        if len(self.queue) > 0:
            task = self.queue.pop(0)
            self.dispatched_count += 1
            task["global_loop_num"] = self.dispatched_count # [追加] タスクにIDを刻印
            return task
        return None

    def set_start_counts(self, count: int):
        if self.dispatched_count == 0:
            self.dispatched_count = count
            self.completed_count = count

    def report_completion(self, loop_num: int, status: str):
        self.completed_count += 1
        return True

    def get_status(self):
        return len(self.queue), self.completed_count, self.worker_statuses

    def set_stop_signal(self):
        self.stop_signal = True

# ==============================================================================
# 設定の動的読み込み
# ==============================================================================
def load_config():
    parser = argparse.ArgumentParser(description="Multi-Scenario Autonomous Driving Test Master Orchestrator")
    parser.add_argument("--type", type=str, default="uturn", help="Scenario type (e.g., uturn, cutin)")
    parser.add_argument("--mode", type=str, choices=["explore", "focus", "margin"], default="explore", help="Search mode")
    parser.add_argument("--focus_points", type=str, default=None, help="JSON string for focus points")
    parser.add_argument("--with_host_worker", action="store_true", help="Run a local worker on the host machine (ROS_DOMAIN_ID=21, EXEC_MODE=host)")
    args = parser.parse_args()

    try:
        config_module = importlib.import_module(f"configs.{args.type}")
        print(f"[System] シナリオ設定 'configs.{args.type}' を正常に読み込みました。")
    except ImportError:
        print(f"[Fatal] 設定ファイル configs/{args.type}.py が見つかりません。")
        sys.exit(1)

    focus_points = None
    if args.mode == "focus":
        if args.focus_points:
            focus_points = json.loads(args.focus_points)
        else:
            focus_points = getattr(config_module, 'FOCUS_POINTS', None)
            if not focus_points:
                print("[Fatal] --mode focus が指定されましたが FOCUS_POINTS が設定されていません。")
                sys.exit(1)

    return args.type, config_module, args.mode, focus_points, args.with_host_worker

# ==============================================================================
# [追加] 過去のデータセットから最大ループ番号を取得
# ==============================================================================
def get_last_processed_loop(scenario_name):
    csv_path = os.path.expanduser(f"~/simulation_traces/{scenario_name}_dataset.csv")
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
                    pass
    except Exception:
        pass
    return last_loop

# ==============================================================================
# メインオーケストレーター処理
# ==============================================================================
def main():
    scenario_name, cfg, run_mode, focus_points, with_host_worker = load_config()

    # 1. クラスターの一斉起動 (21〜23号機のコンテナを自動で立ち上げる)
    cluster_manager = ClusterManager()
    cluster_manager.start_cluster(scenario_name, run_mode, with_host_worker)
    
    # 2. Rayクラスターに接続 (namespaceを指定し、ワーカーから発見可能にする)
    head_address = f"{cluster_manager.master_ip}:{cluster_manager.ray_port}"
    ray.init(address=head_address, _node_ip_address=cluster_manager.master_ip, namespace='awsim_cluster', ignore_reinit_error=True)
    
    # 3. 司令塔 (TaskQueueActor) の作成
    try:
        task_queue = TaskQueueActor.options(name="TaskQueueActor", lifetime="detached", num_cpus=0).remote()
        print("[Orchestrator] 司令塔 (TaskQueueActor) を新しく作成しました。")
        
        # [追加] 過去のデータセットから再開位置を復元
        last_loop = get_last_processed_loop(scenario_name)
        if last_loop > 0:
            ray.get(task_queue.set_start_counts.remote(last_loop))
            print(f"[Orchestrator] 過去のデータセットを検知しました。ループ番号 {last_loop + 1} からタスクを再開します。")
    except ValueError:
        task_queue = ray.get_actor("TaskQueueActor")
        print("[Orchestrator] 既存の司令塔 (TaskQueueActor) に再接続しました。")

    # 4. 共有金庫 (SharedStoreActor) の作成
    try:
        shared_store = SharedStoreActor.options(name="SharedStoreActor", lifetime="detached", num_cpus=0).remote()
        print("[Orchestrator] 共有金庫 (SharedStoreActor) を新しく作成しました。")
    except ValueError:
        shared_store = ray.get_actor("SharedStoreActor")
        print("[Orchestrator] 既存の共有金庫 (SharedStoreActor) に再接続しました。")

    # 5. AI (Strategist) の初期化
    strategist = ActiveLearningStrategist(scenario_name, cfg, num_candidates=10000, focus_points=focus_points, run_mode=run_mode)
    
    REPEAT_COUNT = getattr(cfg, 'REPEAT_COUNT', 3000)
    MAX_QUEUE_SIZE = 15  # ワーカーが即座に仕事を取れるよう、常にキューにタスクを蓄えておく

    # 6. ホストワーカーの直接起動
    host_worker_proc = None
    if with_host_worker:
        print("\n[Orchestrator] ホストモードのワーカー(21号機)をバックグラウンドで起動します...")
        env = os.environ.copy()
        env["ROS_DOMAIN_ID"] = "21"
        env["EXEC_MODE"] = "host"
        
        log_dir = os.path.expanduser("~/simulation_traces_host")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "host_worker_console.log")
        host_worker_log = open(log_path, "w")
        
        cmd = ["python3", "-u", "run_manager.py", "--type", scenario_name, "--mode", run_mode]
        if focus_points:
            cmd.extend(["--focus_points", json.dumps(focus_points)])
            
        host_worker_proc = subprocess.Popen(cmd, env=env, stdout=host_worker_log, stderr=subprocess.STDOUT)
        print(f"[Orchestrator] ホストワーカーのコンソール出力は {log_path} に記録されます。")

    print(f"\n=== マスター司令塔 稼働開始 (目標回数: {REPEAT_COUNT}) ===")
    
    try:
        while True:
            q_len, completed, worker_statuses = ray.get(task_queue.get_status.remote())
            # 各ワーカーの状態を並べて文字列化
            ws_str = " | ".join([f"[{k}] {v}" for k, v in sorted(worker_statuses.items())])
            # \033[K で行末の古い文字を消去しつつ、1行に綺麗に表示する
            sys.stdout.write(f"\r\033[K[Orchestrator] 完了={completed}/{REPEAT_COUNT} | キュー={q_len} || {ws_str}")
            sys.stdout.flush()

            if completed >= REPEAT_COUNT:
                print("\n[Orchestrator] 目標回数に到達しました。終了シグナルを送信します。")
                ray.get(task_queue.set_stop_signal.remote())
                break
                
            # キューが減ってきたら AI に次のパラメータを相談して補充
            while q_len < MAX_QUEUE_SIZE:
                next_target = strategist.decide_next_target()
                ray.get(task_queue.add_task.remote(next_target))
                q_len += 1

            time.sleep(2)
    except KeyboardInterrupt:
        print("\n[Orchestrator] 中断シグナルを受信しました。全ワーカーに停止命令を送ります。")
        ray.get(task_queue.set_stop_signal.remote())
    finally:
        if host_worker_proc:
            print("\n[Orchestrator] ホストワーカープロセスを終了しています...")
            host_worker_proc.terminate()
            try:
                host_worker_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                host_worker_proc.kill()
        
if __name__ == "__main__":
    main()