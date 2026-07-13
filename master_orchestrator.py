#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import time
import ray
# パスの追加
LAUNCH_DIR = os.path.dirname(os.path.abspath(__file__))
if LAUNCH_DIR not in sys.path:
    sys.path.append(LAUNCH_DIR)

from redis_cluster.cluster_manager import ClusterManager
from redis_cluster.shared_store import SharedStoreActor
from redis_cluster.task_queue import TaskQueueActor
from redis_cluster import cluster_config
from core.config_loader import load_config
from core.dataset_repo import DatasetRepository
from local_worker import HostWorkerManager
from strategist import ActiveLearningStrategist

# ==============================================================================
# メインオーケストレーター処理
# ==============================================================================
def main():
    cfg = load_config()
    repo = DatasetRepository(cfg.scenario_name)
    repo.restore_base_dataset(cfg.resume_from)
    # 1. クラスターの一斉起動 (21〜23号機のコンテナを自動で立ち上げる)
    cluster_manager = ClusterManager()
    cluster_manager.start_cluster(cfg.scenario_name, cfg.run_mode, cfg.with_host_worker, cfg.ext_mode, cfg.headless)
    
    # 2. Rayクラスターに接続 (namespaceを指定し、ワーカーから発見可能にする)
    head_address = f"{cluster_manager.master_ip}:{cluster_manager.ray_port}"
    ray.init(address=head_address, _node_ip_address=cluster_manager.master_ip, namespace='awsim_cluster', ignore_reinit_error=True)
    
    # 3. 司令塔 (TaskQueueActor) の作成
    try:
        # [修正] 司令塔を確実にマスター機(21号機)のローカルで起動させる制約を追加
        # これにより、ワーカーノード(22, 23号機)がダウンしても司令塔は生き残り、システム全体のクラッシュを防ぎます。
        task_queue = TaskQueueActor.options(
            name="TaskQueueActor",
            lifetime="detached",
            num_cpus=0,
            scheduling_strategy=ray.util.scheduling_strategies.NodeAffinitySchedulingStrategy(
                node_id=ray.get_runtime_context().get_node_id(),
                soft=False
            )).remote()
        print("[Orchestrator] 司令塔 (TaskQueueActor) を新しく作成しました。")
        
        # [追加] 過去のデータセットから再開位置を復元
        # dkw_fixed モード: resume_from の過去データ(_base)は使わず、
        # 今回の実行で途中までできた _dataset.csv のみを参照して再開する
        if cfg.run_mode == "dkw_fixed":
            # _dataset.csv のみから最終ループ番号を取得（_base.csv は除外）
            last_loop = repo.get_last_loop_num_from_current()
            if last_loop > 0:
                ray.get(task_queue.set_start_counts.remote(last_loop))
                print(f"[Orchestrator] dkw_fixed: 途中までのデータを検知。ループ番号 {last_loop + 1} から再開します。")
        else:
            last_loop = repo.get_last_loop_num()
            if last_loop > 0:
                ray.get(task_queue.set_start_counts.remote(last_loop))
                print(f"[Orchestrator] 過去のデータセットを検知しました。ループ番号 {last_loop + 1} からタスクを再開します。")
    except ValueError:
        task_queue = ray.get_actor("TaskQueueActor")
        print("[Orchestrator] 既存の司令塔 (TaskQueueActor) に再接続しました。")

    # 4. 共有金庫 (SharedStoreActor) の作成
    try:
        # 共有金庫を確実にマスター機(21号機)のローカルで起動させる制約を追加
        shared_store = SharedStoreActor.options(
            name="SharedStoreActor",
            lifetime="detached",
            num_cpus=0,
            scheduling_strategy=ray.util.scheduling_strategies.NodeAffinitySchedulingStrategy(
                node_id=ray.get_runtime_context().get_node_id(),
                soft=False
            )
        ).remote()
        print("[Orchestrator] 共有金庫 (SharedStoreActor) を新しく作成しました。")
    except ValueError:
        shared_store = ray.get_actor("SharedStoreActor")
        print("[Orchestrator] 既存の共有金庫 (SharedStoreActor) に再接続しました。")

    # 5. AI (Strategist) の初期化
    config_module = cfg.config_module
    strategist = ActiveLearningStrategist(
        cfg.scenario_name, config_module, num_candidates=2000,
        focus_points=cfg.focus_points, run_mode=cfg.run_mode,
        dkw_bounds=cfg.dkw_bounds, dkw_region=cfg.dkw_region,
        dkw_pure_smc=cfg.dkw_pure_smc, dkw_simultaneous=cfg.dkw_simultaneous,
        max_samples=cfg.max_samples
    )

    REPEAT_COUNT = getattr(config_module, 'REPEAT_COUNT', 3000)
    if cfg.max_samples is not None:
        REPEAT_COUNT = cfg.max_samples

    # 稼働中のマシン(ワーカー)数を動的にカウントし、キューのサイズを自動調整
    worker_count = sum(1 for node in cluster_config.CLUSTER_NODES.values() if node.get("enabled", True))
    MAX_QUEUE_SIZE = worker_count * 4    # ワーカー数の4倍を上限(High-Water Mark)とする
    REFILL_THRESHOLD = worker_count * 2  # ワーカー数の2倍まで減ったら補充を開始(枯渇防止の強力なバッファ)

    # 6. ホストワーカーの直接起動
    host_worker = HostWorkerManager()
    if cfg.with_host_worker:
        host_worker.start(cfg.scenario_name, cfg.run_mode, cfg.ext_mode, cfg.dkw_region,
                          focus_points=cfg.focus_points, dkw_bounds=cfg.dkw_bounds,
                          dkw_pure_smc=cfg.dkw_pure_smc, dkw_simultaneous=cfg.dkw_simultaneous,
                          headless_host=cfg.headless_host)

    if cfg.run_mode == "dkw_fixed":
        print(f"\n=== マスター司令塔 稼働開始 (固定サンプリングモード: 目標 {REPEAT_COUNT} 回) ===")
    elif cfg.run_mode in ["dkw", "verify_consistency"]:
        print(f"\n=== マスター司令塔 稼働開始 ({cfg.run_mode} モード) ===")
    else:
        print(f"\n=== マスター司令塔 稼働開始 (目標回数: {REPEAT_COUNT}) ===")
    
    try:
        while True:
            q_len, completed, worker_statuses = ray.get(task_queue.get_status.remote())
            # 各ワーカーの状態を並べて文字列化
            ws_str = " | ".join([f"[{k}] {v}" for k, v in sorted(worker_statuses.items())])
            # \033[K で行末の古い文字を消去しつつ、1行に綺麗に表示する
            if cfg.run_mode == "dkw_fixed":
                sys.stdout.write(f"\r\033[K[Orchestrator] 固定サンプリング中 {completed}/{REPEAT_COUNT} | キュー={q_len} || {ws_str}")
            elif cfg.run_mode in ["dkw", "verify_consistency"]:
                sys.stdout.write(f"\r\033[K[Orchestrator] 進行状況 (総ループ: {completed}) | キュー={q_len} || {ws_str}")
            else:
                sys.stdout.write(f"\r\033[K[Orchestrator] 完了={completed}/{REPEAT_COUNT} | キュー={q_len} || {ws_str}")
            sys.stdout.flush()

            if cfg.run_mode not in ["dkw", "verify_consistency"]:
                if completed >= REPEAT_COUNT:
                    print("\n[Orchestrator] 目標回数に到達しました。終了シグナルを送信します。")
                    ray.get(task_queue.set_stop_signal.remote())
                    break
                
            # キューが減ってきたら AI に次のパラメータを相談して補充
            if q_len <= REFILL_THRESHOLD:
                stop_requested = False
                while q_len < MAX_QUEUE_SIZE:
                    next_target = strategist.decide_next_target()
                    if next_target.get("system_command") == "stop":
                        print(f"\n[Orchestrator] AI(Strategist)から終了指示を受信しました: {next_target.get('reason')}")
                        ray.get(task_queue.set_stop_signal.remote())
                        stop_requested = True
                        break
                    ray.get(task_queue.add_task.remote(next_target))
                    q_len += 1
                if stop_requested:
                    break

            time.sleep(2)
    except KeyboardInterrupt:
        print("\n[Orchestrator] 中断シグナルを受信しました。全ワーカーに停止命令を送ります。")
        ray.get(task_queue.set_stop_signal.remote())
    finally:
        if cfg.with_host_worker:
            host_worker.stop(timeout=5)
        
if __name__ == "__main__":
    main()