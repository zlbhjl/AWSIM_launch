#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import time
import ray
from . import cluster_config

class ClusterManager:
    def __init__(self):
        self.nodes = cluster_config.CLUSTER_NODES
        self.master_ip = cluster_config.MASTER_IP
        self.ray_port = cluster_config.RAY_PORT

    def start_cluster(self, scenario_type="uturn", run_mode="explore"):
        """
        Ray/Redis クラスターを起動し、ワーカーを参加させます。
        """
        print("=== Ray/Redis クラスター起動シーケンス開始 ===")
        
        # 1. マスター(自機)でヘッドノードを起動
        print(f"[Master] 21号機 ({self.master_ip}) でヘッドノードを起動中...")
        try:
            # 既存のRayプロセスがあれば停止
            subprocess.run(["ray", "stop", "--force"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # ヘッドノード起動
            ray_start_cmd = [
                "ray", "start", "--head",
                f"--port={self.ray_port}",
                f"--node-ip-address={self.master_ip}",
                "--dashboard-host=0.0.0.0",
                "--num-cpus=0",  # ホストOS上ではタスクを実行しないよう制限（コンテナ側で実行させるため）
                "--disable-usage-stats"  # [追加] 対話プロンプトによるハングアップを防止
            ]
            result = subprocess.run(
                ray_start_cmd,
                capture_output=True,
                text=True,
                timeout=30  # 30秒以内に起動しなければエラーとする
            )
            if result.returncode != 0:
                print(f"[Fatal] Rayヘッドノードの起動に失敗しました。")
                print(f"  -> STDOUT: {result.stdout.strip()}")
                print(f"  -> STDERR: {result.stderr.strip()}")
                return False
            print("[Master] ヘッドノードの起動完了")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            print(f"[Master] 起動エラー: {e}")
            return False

        # 2. ワーカーノード（22号機、23号機）をSSH経由で参加させる
        head_address = f"{self.master_ip}:{self.ray_port}"
        
        for node_id, info in self.nodes.items():
            # [追加] enabled フラグをチェックし、Falseならこのノードの処理をスキップ
            if not info.get("enabled", True):
                print(f"[Worker] {info['machine']} ({info['ip']}) は設定により無効化されています。スキップします。")
                continue

            # コンテナ設定があるノード（マスター自身を含む）は、すべてワーカーコンテナを起動する
            if "container" in info:
                print(f"[Worker] {info['machine']} ({info['ip']}) のワーカーコンテナをクラスターに参加させます...")
                
                # --- [追加] ワーカーノードへの最新コード・設定ファイルの自動同期 (rsync) ---
                if info["ip"] != self.master_ip:
                    print(f"  -> {info['machine']} へ最新のコードと設定ファイルを同期中 (rsync)...")
                    ssh_opts = "-o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=5"
                    # [修正] -az ではなく -rltvz を使い、所有者情報のコピー(-g -o)を省くことでPermission deniedを回避
                    sync_awsim_cmd = f"rsync -rltvz -e 'ssh {ssh_opts}' --exclude 'simulation_traces' --exclude '__pycache__' ~/AWSIM_launch/ {info['user']}@{info['ip']}:~/AWSIM_launch/"
                    # [修正] エラーになりやすい隠しディレクトリ(.venv)を同期から除外し、同期失敗を防ぐ
                    sync_formulas_cmd = f"rsync -rltvz -e 'ssh {ssh_opts}' --exclude '.venv' ~/aw-cheaker/Maude-3.5.1/AW-CheckerPy/ {info['user']}@{info['ip']}:~/aw-cheaker/Maude-3.5.1/AW-CheckerPy/"
                    try:
                        subprocess.run(sync_awsim_cmd, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                        subprocess.run(sync_formulas_cmd, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                        print(f"  -> 同期完了")
                    except subprocess.CalledProcessError as e:
                        print(f"  -> [警告] 同期エラー: {e.stderr.decode().strip()} (過去のコードで実行される可能性があります)")
                # ----------------------------------------------------------------

                c_info = info["container"]
                c_name = c_info.get("name", "sim_worker")
                ros_id = c_info.get("ros_domain_id", 0)
                
                # =================================================================
                # [重要] PCに存在する正しいDockerイメージ名に変更してください
                DOCKER_IMAGE = "autoware_internal:2026" 
                # =================================================================

                # 1. コンテナのクリーン起動と初期化処理の一括実行
                # 毎回古いコンテナを強制破棄し、コンテナ起動時にRayのインストール〜現場監督の起動までをすべて直列で行う
                remote_setup_cmd = (
                    f"xhost +local:docker > /dev/null 2>&1 || true; "
                    f"docker rm -f {c_name} > /dev/null 2>&1 || true; "
                    f"rm -rf ~/simulation_traces_{c_name}/* > /dev/null 2>&1 || true; "  # [追加] マウント元を完全に空にして古いデータを隠蔽
                    f"mkdir -p ~/simulation_traces_{c_name} && chmod 777 ~/simulation_traces_{c_name}; "
                    f"docker run -d -it --name {c_name} --user passd --net=host --privileged --gpus all --shm-size=16gb "
                    f"-e NVIDIA_DRIVER_CAPABILITIES=all -e __NV_PRIME_RENDER_OFFLOAD=1 -e __GLX_VENDOR_LIBRARY_NAME=nvidia "
                    f"-v /tmp/.X11-unix:/tmp/.X11-unix -v ~/AWSIM_launch:/home/passd/AWSIM_launch "
                    f"-v ~/aw-cheaker/Maude-3.5.1/AW-CheckerPy:/home/passd/aw-cheaker/Maude-3.5.1/AW-CheckerPy "
                    f"-v ~/simulation_traces_{c_name}:/home/passd/simulation_traces "
                    f"-v /run/user/$(id -u):/run/user/$(id -u) -e DISPLAY -e XDG_RUNTIME_DIR "
                    # [修正] コンテナ内の HOME を /home/passd に強制上書きし、AutowareやAWSIMのパスエラー/画面出ない問題を解決
                    f"-e ROS_DOMAIN_ID={ros_id} -e HOME=/home/passd {DOCKER_IMAGE} "
                    # [修正] ホストOSのRayバージョン(2.55.0)と合わせるためにバージョンを固定してインストール
                    f"bash -c '{{ export HOME=/home/passd && export PATH=$HOME/.local/bin:$PATH && "
                    f"python3 -m pip install --user --no-cache-dir ray==2.55.0 && ray start --address=\"{head_address}\" --node-ip-address=\"{info['ip']}\" && "
                    f"mkdir -p /home/passd/simulation_traces && cd /home/passd/AWSIM_launch && "
                    f"python3 -u run_manager.py --type {scenario_type} --mode {run_mode}; }} > /home/passd/simulation_traces/worker_log_{c_name}.txt 2>&1 || sleep infinity'"
                )
                
                full_cmd = remote_setup_cmd
                
                if info["ip"] == self.master_ip:
                    # マスター(自機)の場合はローカルのBashで実行
                    try:
                        # [改善] コンテナ起動時のエラーを握りつぶさず、ログに表示するように修正
                        result = subprocess.run(full_cmd, shell=True, executable="/bin/bash", capture_output=True, text=True)
                        if result.returncode != 0:
                            print(f"  -> [エラー] {info['machine']} (自機) のコンテナ起動失敗\n    STDOUT: {result.stdout.strip()}\n    STDERR: {result.stderr.strip()}")
                        else:
                            print(f"  -> {info['machine']} (自機) にワーカー起動コマンドを送信しました")
                    except Exception as e:
                        print(f"  -> {info['machine']} (自機) のワーカー起動失敗: {e}")
                else:
                    # 他の号機の場合はSSH経由で実行
                    ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{info['user']}@{info['ip']}", full_cmd]
                    try:
                        subprocess.Popen(ssh_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        print(f"  -> {info['machine']} にワーカー起動コマンドを送信しました")
                    except Exception as e:
                        print(f"  -> {info['machine']} へのコマンド送信失敗: {e}")

        # 3. クラスターの準備が整うまで待機
        print("クラスターの同期とワーカーの参加を待機しています（初回はRayのインストール等に時間がかかります）...")
        time.sleep(10)
        
        # Python上でRayに接続（これにより、スクリプト内で@ray.remoteが使えるようになる）
        ray.init(address=f"{self.master_ip}:{self.ray_port}", _node_ip_address=self.master_ip, namespace='awsim_cluster', ignore_reinit_error=True)
        
        print("ワーカーノードのCPUコアが認識されるのを待機中... (Rayのインストール完了まで最大3分ほど待機します)")
        for i in range(36):  # 最大約180秒待機
            resources = ray.cluster_resources()
            if resources.get('CPU', 0) > 0:
                break
            if i % 6 == 0 and i > 0:
                print(f"  ... 待機中 ({i*5}秒経過) - 各コンテナ内でRayをインストール・起動しています")
            time.sleep(5)
            
        resources = ray.cluster_resources()
        print(f"=== クラスター構築完了 ===")
        print(f"利用可能な合計CPUコア数: {resources.get('CPU', 0)}")