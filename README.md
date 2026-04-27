# AWSIM Adaptive Safety Testing Framework

## 概要
本プロジェクトは、AWSIM (自動運転シミュレータ) および Autoware を対象としたシナリオベースの安全性テスト自動化フレームワークです。
AI (ガウス過程回帰モデル) を用いた **アクティブラーニング (能動学習)** を採用しており、過去のテスト実行結果から安全性（TTCや衝突など）の境界を学習・予測することで、限られたシミュレーション回数で効率的に危険なエッジケースを探索・特定します。

## 主な特徴
- **完全自動化されたテストループ**: AWSIM、Autoware、モニタリングツールの起動・管理・クリーンアップを完全に自動化 (`run_manager.py`)。
- **AIによる適応的探索**: ガウス過程回帰を用いて、不確実性（モデルの自信のなさ）が高い領域や境界付近を狙い撃ちで検証 (`strategist.py`, `estimator.py`)。
- **3ステップ探索戦略**: 
  1. グローバル探索 (初期データ収集)
  2. 境界線探索 (安定性の評価)
  3. マージン領域のクリーンアップ (不確実性の徹底排除)
- **Config-Driven アーキテクチャ**: シナリオ (Uターン、割り込み等) のパラメータやAIの探索範囲を単一の設定ファイルで柔軟に定義可能 (`configs/`)。
- **フォーカス (集中) モード**: 特定のパラメータの周辺に絞ってテストを反復するピンポイント検証機能。

## ファイル・ディレクトリ構成

```text
AWSIM_launch/
├── run_manager.py      # システム全体のメインプロセス。インフラの起動、AIからのパラメータ受け取り、テストの反復実行を管理。
├── run_scenario.py     # 単一のシミュレーションを実行するスクリプト。動的パラメータを受け取りシナリオを構築。
├── strategist.py       # AIの探索戦略を司る頭脳。現在のフェーズを判断し、次に検証すべきパラメータを決定。
├── estimator.py        # 過去の結果を学習し、予測値と不確実性(標準偏差)を算出するガウス過程回帰モデル。
├── awchecker.py        # シミュレーション結果(JSON)をMaude等で解析し、制約式に基づき安全性(Safe/Unsafe)を判定。
├── param_logger.py     # テスト実行時のパラメータとAIの選定理由(reason)をCSVに記録。
├── configs/            # シナリオごとの設定ファイルを格納するディレクトリ。
│   └── uturn.py        # Uターンシナリオ用の設定 (探索範囲、ターゲット優先度、固定パラメータなど)。
└── README.md           # 本ドキュメント
```

## 前提環境 (Dependencies)
本システムは以下の外部ツールと連携して動作します。パスや環境構築が完了していることを確認してください。
- **AWSIM Labs**: `~/awsim_labs`
- **Autoware**: `~/autoware`
- **AW-Runtime-Monitor**: `~/AW-Runtime-Monitor`
- **AW-CheckerPy (Maude)**: `~/aw-cheaker/Maude-3.5.1/AW-CheckerPy`
- **Python パッケージ**: `numpy`, `pandas`, `scipy`, `scikit-learn`

## 使用方法

### 1. 通常探索モード (Explore Mode)
AIが探索空間全体から自動で危険領域の境界を見つけ出すモードです。デフォルトで動作します。

```bash
python3 run_manager.py --type uturn
```

### 2. フォーカス探索モード (Focus Mode)
特定のパラメータの周辺を重点的にテストし、死角や再現性を検証するモードです。

```bash
# Config (uturn.py) 内に定義された FOCUS_POINTS を使用する場合
python3 run_manager.py --type uturn --mode focus

# CLIから検証したいポイントを直接指定する場合
python3 run_manager.py --type uturn --mode focus --focus_points '[{"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 15.0}]'
```

### 3. チェッカープロセスの起動 (別ターミナル)
生成されたシミュレーションデータ (JSON) をリアルタイムで監視し、安全性を判定するために、別のターミナルでチェッカーを起動してください。

```bash
python3 awchecker.py --type uturn
```

## 出力データ (Traces)
テストの実行結果とログは `~/simulation_traces` ディレクトリに出力されます。
- `{scenario}_parameters.csv`: AIが選定した実行パラメータと選定理由の履歴。
- `checker_results.csv`: `awchecker.py` が判定した各テストケースの安全性指標 (TTC, 衝突有無など)。
- `{scenario}_test_sim{N}.json`: 各ループのRuntime Monitorの詳細トレースデータ。

## 新しいシナリオの追加方法
1. `configs/` ディレクトリに新しいシナリオの設定ファイル (例: `cutin.py`) を作成します。
2. 探索したい `PARAM_RANGES` や `FIXED_PARAMS` を定義します。
3. `run_scenario.py` 内のロジックにシナリオ生成の分岐 (例: `elif args.type == "cutin":`) を追加します。
4. `--type cutin` を指定して実行します。

## 技術的な工夫・トラブルシューティング (Dockerコンテナの外部操作に関する解決策)

ホストOS（外側）から `docker exec` を用いてコンテナ内のシミュレーションを完全自動化する際、手動でコンテナに入って実行した時と異なり「車が動かない」「レーダー（点群）が消える」「通信が詰まる」といった特有の問題に対処するため、以下の実装が組み込まれています。

1. **GUI・ROS環境変数の明示的な引き継ぎ (`master_orchestrator.py`)**
   `docker exec` にはホストの画面情報が自動で引き継がれないため、`$DISPLAY`, `$XDG_RUNTIME_DIR`, `$ROS_DOMAIN_ID` をホストから取得し、`-e` オプションとしてコンテナに注入してAWSIMのクラッシュを防いでいます。
2. **実行ユーザーと作業ディレクトリの固定**
   デフォルトの root ユーザーでの実行を防ぐため、`--user passd` と `--workdir /home/passd/AWSIM_launch` を指定し、手動実行時と権限・カレントディレクトリ環境を一致させています。
3. **パイプ詰まり（ハングアップ）の防止 (`run_manager.py`)**
   マスターからの指令完了後にターミナルが閉じられると、コンテナ内で生き残ったAWSIMやAutowareがログの出力先を失い、内部でフリーズする現象（車が途中で止まる原因）が起きます。これを防ぐため、インフラプロセスの出力を `stdout=subprocess.DEVNULL`, `stderr=subprocess.DEVNULL` に向けて完全にバックグラウンド化しています。
4. **対話型シェル (`bash -i`) による完全なROS/DDS環境ロード**
   非対話型シェル（通常の `bash -c`）での呼び出しでは、Ubuntuの仕様により `~/.bashrc` の読み込みが途中でキャンセルされます。これによりCycloneDDS等の大容量通信向けのチューニング設定がAutowareに適用されず、レーダーデータが消失する問題がありました。これを `bash -i -c` を用いて人間が直接操作している対話モードを偽装することで、手動ログイン時と全く同じROS通信環境を確立しています。

## 分散実行アーキテクチャ (改造プラン)

現在のシングルノード実行フレームワークをベースに、`改造プラン.md`で定義された複数台のマシンでシミュレーションを並列実行するための拡張構成です。

### 追加されるファイル・ディレクトリ構成

```text
AWSIM_launch/
├── redis_cluster/          # 分散クラスターのインフラ管理層
│   ├── cluster_config.py   # ワーカーPCのIPやコンテナ名などを定義
│   └── cluster_manager.py  # 各PCにSSH接続し、Dockerコンテナを起動・管理
├── master_orchestrator.py  # 【司令塔】システム全体の起動とタスク管理を行うマスタープロセス
├── shared_store.py         # 【共有金庫】各ワーカーからの結果を安全にCSVへ書き込むRay Actor
│
├── run_manager.py          # 【ワーカー】コンテナ内で実行され、司令塔からタスクを受け取りシミュレーションを実行
├── run_scenario.py         # (変更なし)
├── strategist.py           # (司令塔が使用)
├── estimator.py            # (司令塔が使用)
└── ... (その他既存ファイル)
```

### 処理フローの概要

1.  **起動**: 司令塔PCで `master_orchestrator.py` を実行します。
2.  **インフラ構築**: `cluster_manager` が各ワーカーPCにSSH接続し、`cluster_config` の設定に基づいて独立したDockerコンテナを起動します。
3.  **タスク生成**: 司令塔の `strategist` (AI) が次に検証すべきパラメータを計算します。
4.  **タスク実行**: 各コンテナ内の `run_manager` (ワーカー) が司令塔からタスクを受け取り、AWSIM/Autowareのシミュレーションを実行します。
5.  **結果集約**: シミュレーション完了後、ワーカーは結果を司令塔の `shared_store` (共有金庫) に送信します。
6.  **安全な書き込み**: `shared_store` はファイル競合を起こさないように、結果を単一のCSVファイルに追記します。
7.  **再学習**: AIは更新されたCSVを読み込んで再学習し、より賢い次のタスクを生成します。このループが繰り返されます。

使い方 / コマンド一覧
システムの運用は、すべてマスター（21号機）のターミナルから行います。

1. 過去の実験結果の退避（事前準備）
新しい実験を始める前に、全コンピュータの過去のデータ（CSVや動画など）を一斉に退避させ、環境をクリーンにします。

python3 archive_results.py
2. システムの起動（全自動シミュレーション開始）
全ワーカーへのコード同期、コンテナの再構築、AIの推論、シミュレーションの並列実行がすべて自動で開始されます。

# 【探索モード】空間全体から危険な境界線を自動探索させる場合（通常はこちら）
python3 master_orchestrator.py --type uturn --mode explore

# 【集中モード】既知の特定のポイント周辺を重点的に検証する場合
python3 master_orchestrator.py --type uturn --mode focus

# 実践例: ターミナルを閉じてもバックグラウンドで実行し、ログを保存する (探索モード)
nohup python3 master_orchestrator.py --type uturn --mode explore > orchestrator_log.txt 2>&1 &
nohup python3 master_orchestrator.py --type uturn --mode explore > orchestrator_log.txt 2>&1 & (※途中で安全に停止させたい場合は Ctrl + C を押してください。全ワーカーに安全な停止命令が送信されます)

3. ワーカーのリアルタイム監視（トラブルシューティング）
各コンテナの裏側で動いている現場監督（run_manager.py）の生ログ（出力）を見たい場合は、別ターミナルを開いて以下を実行します。

# 例: 22号機のコンテナログを見る場合
docker exec -it sim_worker_22 tail -f /home/passd/simulation_traces/worker_log_sim_worker_22.txt
今後の拡張性
新しくワーカーPC（例：24号機）を追加したい場合は、cluster_config.py に新しいIPアドレスやコンテナ名、ROS_DOMAIN_ID（例: 24）を追記するだけで、システムが全自動でコンテナを構築し、クラスターの計算力（スループット）を向上させます。

AWSIM 分散シミュレーションクラスター

最新のアップデート (ログ監視の改善)
ログの隔離: AWSIMとAutowareが毎秒吐き出す大量の通信ログを、それぞれ awsim.log と autoware.log に隔離しました。これにより、現場監督（run_manager.py）の進行状況ログが洪水に飲まれるのを防いでいます。
リアルタイム出力の強制: コンテナ起動時のPythonに -u (アンバッファード) オプションを付与し、待機中... や Loop 1 といった進捗が数分遅れることなく、リアルタイムで画面に表示されるように改善しました。
リアルタイム監視コマンド集
マスターPC（21号機）の別のターミナルから、ワーカーPC（例: 23号機）の内部で動いているシミュレーションの状況をリアルタイム監視するためのコマンドです。

1. 現場監督の進捗ログを見る（メイン）
現在何ループ目のテストをしているか、タスクの受け取り状況など、一番重要な進行状況を確認できます。

ssh tomita2@150.65.227.23 "docker exec sim_worker_23 tail -f /home/passd/simulation_traces/worker_log_sim_worker_23.txt"
2. AWSIMの裏ログを見る
シミュレータ本体（Unity）が正常に起動しているか、またはクラッシュしていないかを確認したい場合に使います。

ssh tomita2@150.65.227.23 "docker exec sim_worker_23 tail -f /home/passd/simulation_traces/awsim.log"
3. Autowareの裏ログを見る
自動運転AI（ROS 2 / Fast DDS）のノードが正常に立ち上がっているか、通信エラーが出ていないかを確認したい場合に使います。

ssh tomita2@150.65.227.23 "docker exec sim_worker_23 tail -f /home/passd/simulation_traces/autoware.log"
💡 Hint
監視を終了したい時は、どのコマンドも Ctrl + C を押してください。監視をやめても裏側のシミュレーション自体は止まりません。

よく使うコマンド集（ホストOS用）
# 実行ログのリアルタイム監視
tail -f orchestrator_log.txt

# システムの安全な停止（各ワーカーに終了シグナルを送る）
pkill -2 -f master_orchestrator.py

# システムの強制終了
pkill -9 -f master_orchestrator.py
python3 stop_containers.py
トラブルシューティング / 手動デバッグ
システムが全自動でコンテナを起動する際、内部ではクラッシュや通信干渉を防ぐために以下のような強力な設定が行われています。

--shm-size=16gb: ROS 2の大容量通信（Lidar点群など）による共有メモリ不足でのクラッシュを防止。
-e ROS_DOMAIN_ID={id}: 他の号機やホストとのROS 2通信の混線を完全に遮断。
-e __NV_PRIME_RENDER_OFFLOAD=1 等: GUIの描画を強制的にNVIDIA GPUへオフロード。
もし特定の号機でシミュレーションがうまく動かない場合、以下の手順でシステムと全く同じ環境設定のコンテナに手動で入り、どこでエラーが起きているか検証することができます。

1. 手動デバッグ用コンテナの起動
以下のコマンドをターミナルに貼り付けて実行します（例：21号機の場合。他号機の場合は sim_worker_21 や ROS_DOMAIN_ID の数値を変更してください）。

# 1. 古いコンテナとログディレクトリの初期化
xhost +local:docker
docker rm -f sim_worker_21
rm -rf ~/simulation_traces_sim_worker_21/*
mkdir -p ~/simulation_traces_sim_worker_21
chmod 777 ~/simulation_traces_sim_worker_21

# 2. 手動デバッグ用コンテナの起動（対話モードで中に入る）
docker run -it \
  --name sim_worker_21 \
  --user passd \
  --net=host \
  --privileged \
  --gpus all \
  --shm-size=16gb \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  -e __NV_PRIME_RENDER_OFFLOAD=1 \
  -e __GLX_VENDOR_LIBRARY_NAME=nvidia \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v ~/AWSIM_launch:/home/passd/AWSIM_launch \
  -v ~/aw-cheaker/Maude-3.5.1/AW-CheckerPy:/home/passd/aw-cheaker/Maude-3.5.1/AW-CheckerPy \
  -v ~/simulation_traces_sim_worker_21:/home/passd/simulation_traces \
  -v /run/user/$(id -u):/run/user/$(id -u) \
  -e DISPLAY=$DISPLAY \
  -e XDG_RUNTIME_DIR=/run/user/$(id -u) \
  -e ROS_DOMAIN_ID=21 \
  -e HOME=/home/passd \
  autoware_internal:2026 \
  /bin/bash
2. コンテナ内での動作検証
コンテナ内に入ったら、以下の順に手動で実行してエラーの原因を特定します。

# [検証1] 昔のように手動で AWSIM と Autoware を動かしてみる
cd /home/passd/awsim_labs && ./awsim_labs.x86_64 -noise false &
cd /home/passd/autoware && source install/setup.bash && ros2 launch autoware_launch e2e_simulator.launch.xml vehicle_model:=awsim_labs_vehicle sensor_model:=awsim_labs_sensor_kit map_path:=/home/passd/autoware_map/nishishinjuku_autoware_map launch_vehicle_interface:=true