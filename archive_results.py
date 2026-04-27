#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import os
from datetime import datetime

# このスクリプトがどこから実行されても redis_cluster を見つけられるようにパスを追加
LAUNCH_DIR = os.path.dirname(os.path.abspath(__file__))
if LAUNCH_DIR not in sys.path:
    sys.path.append(LAUNCH_DIR)

try:
    from redis_cluster import cluster_config
except ImportError:
    print("[Fatal] redis_cluster パッケージが見つかりません。AWSIM_launch ディレクトリから実行してください。")
    sys.exit(1)

def archive_on_node(node_info, archive_timestamp):
    """単一ノード上で、結果フォルダをリネーム（退避）する"""
    user = node_info.get("user")
    ip = node_info.get("ip")
    container_name = node_info.get("container", {}).get("name")

    if not container_name:
        print(f"  -> スキップ: {node_info['machine']} にはコンテナ設定がありません。")
        return

    source_dir = f"~/simulation_traces_{container_name}"
    target_dir = f"~/simulation_traces_{container_name}_{archive_timestamp}"
    
    # フォルダが存在しない場合でもエラーにならないように `|| true` を追加
    rename_cmd = f"mv {source_dir} {target_dir} > /dev/null 2>&1 || true"

    print(f"Archiving results on {node_info['machine']} ({ip})...")

    try:
        if ip == cluster_config.MASTER_IP:
            # マスターノードはローカルでコマンド実行
            subprocess.run(rename_cmd, shell=True, check=True, executable="/bin/bash")
            
            # [追加] マスター機のホスト直下に生成される「共有金庫用（AI学習用）」のディレクトリも退避する
            master_rename_cmd = f"mv ~/simulation_traces ~/simulation_traces_shared_{archive_timestamp} > /dev/null 2>&1 || true"
            subprocess.run(master_rename_cmd, shell=True, check=True, executable="/bin/bash")
        else:
            # ワーカーノードはSSH経由でコマンド実行
            ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", f"{user}@{ip}", rename_cmd]
            subprocess.run(ssh_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        
        print(f"  -> 成功: {os.path.basename(source_dir)} -> {os.path.basename(target_dir)}")

    except subprocess.CalledProcessError as e:
        print(f"  -> [エラー] {node_info['machine']} での退避に失敗しました。 Error: {e.stderr.decode().strip()}")
    except Exception as e:
        print(f"  -> [エラー] 予期せぬエラーが発生しました ({node_info['machine']}): {e}")

def main():
    """全ノードの結果フォルダを現在時刻を付与してリネームするメイン関数"""
    print("=== 過去のシミュレーション結果の退避処理を開始します ===")
    archive_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"退避用タイムスタンプ: {archive_timestamp}")

    for node_info in cluster_config.CLUSTER_NODES.values():
        archive_on_node(node_info, archive_timestamp)

    print("\n=== 退避完了 ===")
    print("新しい実験を開始する準備が整いました。")

if __name__ == "__main__":
    main()