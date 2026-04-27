#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import os

# パスの追加
LAUNCH_DIR = os.path.dirname(os.path.abspath(__file__))
if LAUNCH_DIR not in sys.path:
    sys.path.append(LAUNCH_DIR)

try:
    from redis_cluster import cluster_config
except ImportError:
    print("[Fatal] redis_cluster パッケージが見つかりません。AWSIM_launch ディレクトリから実行してください。")
    sys.exit(1)

def stop_container_on_node(node_info):
    """単一ノード上でコンテナを強制終了（削除）する"""
    user = node_info.get("user")
    ip = node_info.get("ip")
    container_name = node_info.get("container", {}).get("name")

    if not container_name:
        print(f"  -> スキップ: {node_info['machine']} にはコンテナ設定がありません。")
        return

    # コンテナを強制終了して削除するコマンド
    stop_cmd = f"docker rm -f {container_name} > /dev/null 2>&1 || true"

    print(f"Stopping container '{container_name}' on {node_info['machine']} ({ip})...")

    try:
        if ip == cluster_config.MASTER_IP:
            # マスターノードはローカルでコマンド実行
            subprocess.run(stop_cmd, shell=True, check=True, executable="/bin/bash")
        else:
            # ワーカーノードはSSH経由でコマンド実行
            ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", f"{user}@{ip}", stop_cmd]
            subprocess.run(ssh_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        
        print(f"  -> 成功: コンテナ '{container_name}' を終了しました。")

    except subprocess.CalledProcessError as e:
        print(f"  -> [エラー] {node_info['machine']} でのコンテナ終了に失敗しました。 Error: {e.stderr.decode().strip() if e.stderr else e}")
    except Exception as e:
        print(f"  -> [エラー] 予期せぬエラーが発生しました ({node_info['machine']}): {e}")

def main():
    print("=== クラスター全コンテナの強制終了処理を開始します ===")
    
    # マスターのRayプロセスやOrchestratorも念のためクリーンアップする
    print("\n[Local] Orchestrator と Ray の残存プロセスを停止しています...")
    subprocess.run("pkill -9 -f master_orchestrator.py > /dev/null 2>&1 || true", shell=True, executable="/bin/bash")
    subprocess.run("ray stop --force > /dev/null 2>&1 || true", shell=True, executable="/bin/bash")

    print("\n[Cluster] 各ノードのコンテナを停止・削除しています...")
    for node_info in cluster_config.CLUSTER_NODES.values():
        # enableフラグに関わらず、念のため全コンテナの停止を試みるのが安全
        stop_container_on_node(node_info)

    print("\n=== 全コンテナの終了処理が完了しました ===")

if __name__ == "__main__":
    main()