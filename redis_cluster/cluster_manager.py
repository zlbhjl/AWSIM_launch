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

    def start_cluster(self, scenario_type="uturn", run_mode="explore", with_host_worker=False):
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
                
            # ホストワーカー併用モードの場合、21号機(自機)のコンテナ起動をスキップする
            if with_host_worker and info["ip"] == self.master_ip:
                print(f"[Worker] ホストワーカー併用モード (--with_host_worker) が有効なため、{info['machine']} のコンテナ起動をスキップします。")
                continue

            # コンテナ設定があるノード（マスター自身を含む）は、すべてワーカーコンテナを起動する
            if "container" in info:
                print(f"[Worker] {info['machine']} ({info['ip']}) のワーカーコンテナをクラスターに参加させます...")
                
                # --- [追加] ワーカーノードへの最新コード・設定ファイルの自動同期 (rsync) ---
                if info["ip"] != self.master_ip:
                    print(f"  -> {info['machine']} へ最新のコードと設定ファイルを同期中 (rsync)...")
                    ssh_opts = "-o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=5"
                    # [修正] 同期先での権限エラー(Operation not permitted)を完全に防ぐため、--no-perms --no-owner --no-group を明示
                    sync_awsim_cmd = f"rsync -rtvz --no-perms --no-owner --no-group -e 'ssh {ssh_opts}' --exclude 'simulation_traces' --exclude '__pycache__' ~/AWSIM_launch/ {info['user']}@{info['ip']}:~/AWSIM_launch/"
                    sync_formulas_cmd = f"rsync -rtvz --no-perms --no-owner --no-group -e 'ssh {ssh_opts}' --exclude '.venv' ~/aw-cheaker/Maude-3.5.1/AW-CheckerPy/ {info['user']}@{info['ip']}:~/aw-cheaker/Maude-3.5.1/AW-CheckerPy/"
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
                # [追加] コンフィグからコンテナのパスワードを取得（未設定時は "passd" をデフォルトとする）
                c_pass = c_info.get("password", "passd")
                c_user = c_info.get("user", "passd")
                c_home = c_info.get("workspace", "/home/passd")
                c_image = c_info.get("image", "autoware_internal:2026")

                # -------------------------------------------------------------
                # [追加] 取得したパスワードを使って、sudoコマンドの自動入力動作を抽象化
                # [修正] コンテナ起動コマンド全体のシングルクォートと衝突しないよう、ダブルクォートに変更
                sudo_cmd = f"echo \"{c_pass}\" | sudo -S"
                # -------------------------------------------------------------
                # [修正] マスター機(自機)とリモート機で画面出力の設定を分ける
                # -------------------------------------------------------------

                if info["ip"] == self.master_ip:
                    # 21号機: 物理ディスプレイに画面を表示する通常設定
                    display_mount = f"-v /tmp/.X11-unix:/tmp/.X11-unix "
                    display_env = f"-e DISPLAY "
                    xhost_setup = f"xhost +local:docker > /dev/null 2>&1 || true; "
                    xvfb_setup = "" # マスターはXvfbを使用しない
                else:
                    # 22, 23号機: Xvfb (仮想ディスプレイ) を使用して画面エラーを回避しつつバックグラウンドで実行
                    display_mount = "" # Xvfbは物理Xサーバーに依存しないためマウントは不要
                    # [修正] DISPLAYを確実に固定し、Vulkan(LiDAR)にNVIDIA GPUを強制認識させて点群エラーを解決する
                    display_env = f"-e DISPLAY=:99 -e VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.json "
                    xhost_setup = ""
                    # Xvfbをインストールして起動するコマンド
                    xvfb_setup = (
                        f"{sudo_cmd} apt-get update > /dev/null 2>&1 && "
                        f"{sudo_cmd} DEBIAN_FRONTEND=noninteractive apt-get install -y xvfb > /dev/null 2>&1 && "
                        f"Xvfb :99 -screen 0 1920x1080x24 > /dev/null 2>&1 & "
                    )
                # 1. コンテナのクリーン起動と初期化処理の一括実行
                # 毎回古いコンテナを強制破棄し、コンテナ起動時にRayのインストール〜現場監督の起動までをすべて直列で行う
                remote_setup_cmd = (
                    f"{xhost_setup}"
                    f"docker rm -f {c_name} > /dev/null 2>&1 || true; "
                    f"rm -rf ~/simulation_traces_{c_name}/* > /dev/null 2>&1 || true; "  # [追加] マウント元を完全に空にして古いデータを隠蔽
                    f"mkdir -p ~/simulation_traces_{c_name} && chmod 777 ~/simulation_traces_{c_name}; "
                    f"docker run -d -it --name {c_name} --user {c_user} --net=host --privileged --gpus all --shm-size=32gb "
                    f"-e NVIDIA_DRIVER_CAPABILITIES=all -e __NV_PRIME_RENDER_OFFLOAD=1 -e __GLX_VENDOR_LIBRARY_NAME=nvidia "
                    # [追加] CycloneDDSの通信バッファを拡張し、大容量データ(点群等)のパケットドロップによる遅延を防ぐ
                    #f"-e RMW_IMPLEMENTATION=rmw_cyclonedds_cpp "
                    #f"-e CYCLONEDDS_URI=\"<CycloneDDS><Domain><Internal><MinimumSocketReceiveBufferSize>10485760</MinimumSocketReceiveBufferSize></Internal></Domain></CycloneDDS>\" "
                    f"{display_mount}-v ~/AWSIM_launch:{c_home}/AWSIM_launch "
                    f"-v ~/aw-cheaker/Maude-3.5.1/AW-CheckerPy:{c_home}/aw-cheaker/Maude-3.5.1/AW-CheckerPy "
                    f"-v ~/simulation_traces_{c_name}:{c_home}/simulation_traces "
                    f"-v /run/user/$(id -u):/run/user/$(id -u) {display_env}-e XDG_RUNTIME_DIR "
                    # [修正] コンテナ内の HOME を強制上書きし、AutowareやAWSIMのパスエラー/画面出ない問題を解決
                    f"-e ROS_DOMAIN_ID={ros_id} -e HOME={c_home} {c_image} "
                    # [修正] ホストOSのRayバージョン(2.55.0)と合わせるためにバージョンを固定してインストール
                    # [修正] PATH評価の複雑なエスケープ問題を回避するため、絶対パスでrayを直接起動する
                    f"bash -i -c '{{ export HOME={c_home} && {xvfb_setup}"
                    f"python3 -m pip install --user --no-cache-dir ray==2.55.0 && {c_home}/.local/bin/ray start --address=\"{head_address}\" --node-ip-address=\"{info['ip']}\" && "
                    f"mkdir -p {c_home}/simulation_traces && cd {c_home}/AWSIM_launch && "
                    f"python3 -u run_manager.py --type {scenario_type} --mode {run_mode}; }} > {c_home}/simulation_traces/worker_log_{c_name}.txt 2>&1 || sleep infinity'"
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