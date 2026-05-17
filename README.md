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
- **データの一元管理と高効率化**: パラメータと結果は共有金庫(`shared_store.py`)によってメモリ上で結合され、単一のCSVデータセットとして出力されます。
- **堅牢な自動リカバリ**: タイムアウトや解析エラー発生時でもシステム全体がフリーズすることなく、異常データを安全に弾いてテストを継続します。
- **中断からの自動再開**: 検証を途中で中断しても、次回起動時に過去のCSVデータを読み込み、AIの状態とループ番号を引き継いで続きから自動で再開します。
- **Config-Driven アーキテクチャ**: シナリオ (Uターン、割り込み等) のパラメータやAIの探索範囲、タイムアウト時間を単一の設定ファイルで柔軟に定義可能 (`configs/`)。
- **フォーカス (集中) モード**: 特定のパラメータの周辺に絞ってテストを反復するピンポイント検証機能。
- **リアルタイム進捗監視**: 司令塔の画面で、各ワーカーが「待機中」「実行中」「タイムアウト」など、何をしているかをリアルタイムで1行にまとめて表示します。
- **JAMA物理モデルに基づく理論値算出**: シミュレーションの入力パラメータから、物理限界（空走時間・ブレーキ性能）に基づく理論上の停止距離と安全マージン（Zone）を同時算出・記録し、AIの学習特徴量として活用 (`theoretical_calculator.py`)。

## ファイル・ディレクトリ構成

```text
AWSIM_launch/
├── master_orchestrator.py # 【司令塔】システム全体の起動、クラスター構築、AIタスクのキュー管理を行うマスタープロセス。
├── run_manager.py      # 【ワーカー】各ノードのメインプロセス。インフラの起動、司令塔からのタスク受信、テスト実行を管理。
├── run_scenario.py     # 単一のシミュレーションを実行するスクリプト。動的パラメータを受け取りシナリオを構築。
├── strategist.py       # AIの探索戦略を司る頭脳。現在のフェーズを判断し、次に検証すべきパラメータを決定。
├── estimator.py        # 過去のデータセットを学習し、予測値と不確実性(標準偏差)を算出するガウス過程回帰モデル。
├── awchecker.py        # シミュレーション結果(JSON)を解析し、安全性を判定。判定結果を共有金庫へ送信。
├── param_logger.py     # テスト実行時のパラメータを一時的に共有金庫のバッファへ送信。
├── theoretical_calculator.py # JAMA物理モデルに基づく理論的安全領域(Zone)とマージンを計算するモジュール。
├── visualize_traces.py # 実行結果のCSVデータを読み込み、3Dグラフとして可視化するスクリプト。
├── visualize_traces_split.py # ホスト(21号機)とコンテナ(22・23号機)の実行結果を分割し、それぞれ独立した3Dグラフとして可視化するスクリプト。
├── visualize_worker_stats.py # ワーカー別（ホスト vs コンテナ）の衝突やTTC違反の発生確率を棒グラフで比較・可視化するスクリプト。
├── visualize_jama_zones.py # JAMA物理モデルに基づく理論的な安全領域(Zone)の分布をグラフ化して可視化・分析するスクリプト。
├── redis_cluster/      # 分散クラスター管理モジュール
│   ├── cluster_config.py  # ワーカーPCのIPやコンテナ名、通信割り当て設定などを一元管理。
│   ├── cluster_manager.py # 各PCにSSH接続し、Dockerコンテナを自動起動・同期するクラスター構築スクリプト。
│   └── shared_store.py # 【共有金庫】非同期で送られてくるパラメータと結果を結合し、単一のCSVに記録するスレッドセーフなRay Actor。メモリリーク防止機能付き。
├── configs/            # シナリオごとの設定ファイルを格納するディレクトリ。
│   └── uturn.py        # Uターンシナリオ用の設定 (探索範囲、ターゲット優先度、タイムアウト秒数など)。
└── README.md           # 本ドキュメント
```

## 前提環境 (Dependencies)
本システムは以下の外部ツールと連携して動作します。パスや環境構築が完了していることを確認してください。
- **AWSIM Labs**: `~/awsim_labs`
- **Autoware**: `~/autoware`
- **AW-Runtime-Monitor**: `~/AW-Runtime-Monitor`
- **AW-CheckerPy (Maude)**: `~/aw-cheaker/Maude-3.5.1/AW-CheckerPy`
- **Python パッケージ**: `numpy`, `pandas`, `scipy`, `scikit-learn`

## クイックスタート (使用方法)
システムの起動と管理は、すべてマスター機（21号機）の単一のターミナルから行います。
中断した場合は、同じコマンドを再度実行することで自動的に続きから再開します。

### 1. 全自動シミュレーションの開始 (モード選択)

```bash
# 【探索モード】空間全体から危険な境界線を自動探索させる場合（すべてコンテナで実行）
python3 master_orchestrator.py --type uturn --mode explore

# 【ホスト併用モード】21号機のみ画面を表示して直接検証し、他のワーカーはコンテナで実行する場合
python3 master_orchestrator.py --type uturn --mode explore --with_host_worker
```

# 【マージンモード】過去のデータからAIの学習のみを行い、「安全領域の死角」だけを潰すことに特化する場合
python3 master_orchestrator.py --type uturn --mode margin

```bash
# Config (uturn.py) 内に定義された FOCUS_POINTS を使用する場合
python3 run_manager.py --type uturn --mode focus

# CLIから検証したいポイントを直接指定する場合
python3 run_manager.py --type uturn --mode focus --focus_points '[{"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 15.0}]'
# 【集中モード】既知の特定のポイント周辺を重点的に検証する場合
python3 master_orchestrator.py --type uturn --mode focus
```

### 3. チェッカープロセスの起動 (別ターミナル)
生成されたシミュレーションデータ (JSON) をリアルタイムで監視し、安全性を判定するために、別のターミナルでチェッカーを起動してください。

```bash
python3 awchecker.py --type uturn
```

## 出力データ (Traces)
テストの実行結果とログは `~/simulation_traces` ディレクトリに出力されます。
- `{scenario}_dataset.csv`: 共有金庫によって結合された、AIの学習に直結する完全なデータセット (パラメータ + 評価結果 + 理由)。
  - ※ JAMA物理モデルに基づく理論値（`theory_margin_*`, `theory_zone_*` 等）も記録され、シミュレータの実挙動と物理限界の乖離分析に活用できます。アプローチA（壁想定）とアプローチB（NPC前進考慮）の両方が保存されます。
- `checker_errors_detail.log`: 解析ツールで異常が発生した際の詳細なエラーログ (STDOUT/STDERR)。
- `{scenario}_test_sim{N}.json`: 各ループのRuntime Monitorの詳細トレースデータ。
- `awsim.log` / `autoware.log`: インフラ側の生ログ (エラー調査用)。

## 新しいシナリオの追加方法
1. `configs/` ディレクトリに新しいシナリオの設定ファイル (例: `cutin.py`) を作成します。
2. 探索したい `PARAM_RANGES` や `FIXED_PARAMS` を定義します。
3. `run_scenario.py` 内のロジックにシナリオ生成の分岐 (例: `elif args.type == "cutin":`) を追加します。
4. `--type cutin` を指定して実行します。

## 技術的な工夫・トラブルシューティング (分散自動化に関する解決策)

本システムは、マスター機からリモート機を制御し、バックグラウンドでシミュレーションを完全自動化しています。その際、手動でターミナルから実行した時と異なり「車が動かない」「レーダー（点群）が消える」「通信が詰まる」といった特有の問題に対処するため、以下の実装が組み込まれています。

1. **対話型シェル (`bash -i`) による完全なROS/DDS環境ロード (`cluster_manager.py` / `run_manager.py`)**
   コンテナを起動してバックグラウンドでコマンドを実行する際、通常の `bash -c` ではUbuntuの仕様により `~/.bashrc` の読み込みが途中でキャンセルされます。これによりCycloneDDS等の大容量通信向けのチューニング設定がAutowareに適用されず、通信詰まりやレーダーデータが消失する問題がありました。これを `bash -i -c` を用いて対話モードを偽装することで、手動ログイン時と全く同じROS通信環境を確立しています。
2. **マスター・リモート間のROS通信の分離 (`cluster_manager.py`)**
   複数台のコンピュータで同時にシミュレーションを実行する際、ROS 2の通信がネットワーク上で混線しないよう、コンテナ起動時に `ROS_DOMAIN_ID` を号機ごとに割り当て、完全に独立した通信環境を構築しています。
3. **リモート環境 (ヘッドレス) での仮想ディスプレイ(Xvfb)とGPU連携 (`cluster_manager.py`)**
   物理ディスプレイが接続されていないリモートPC (22, 23号機) では、画面を描画できないためにRVizが無限クラッシュしたり、AWSIMのLiDAR点群が生成されなくなる問題が発生します。これを以下の3つの連携で解決しています。
   - **Xvfbの自動起動**: コンテナ内で `Xvfb :99` を立ち上げ、`DISPLAY=:99` を指定することで、すべてのGUIアプリケーションの描画先を仮想モニターに向け、画面エラーによるクラッシュを防ぎます。
   - **NVIDIA GPUの強制認識**: 通常、Xvfb環境ではGPUが使われませんが、AWSIMのLiDAR計算はGPU(Vulkan)に依存しています。そこで環境変数 `VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.json` を注入し、仮想画面下でも強制的にGPUを認識させて点群データを正常に生成させています。
   - **AWSIMの通常起動**: AWSIMの `-batchmode` (画面なしモード) はセンサーデータ欠損を引き起こすため使用せず、Xvfbに向かって「通常起動」させることで正常なシミュレーションを実現しています。
4. **ログの隔離とハングアップの防止 (`run_manager.py`)**
   AWSIMとAutowareの膨大な標準出力がパイプを詰まらせてプロセスをフリーズさせないよう、各出力は `awsim.log` および `autoware.log` の専用ファイルに隔離しています。現場監督の進行状況は、Pythonのアンバッファード出力（`-u` オプション）を利用してリアルタイムに監視できるようにしています。

## 分散実行アーキテクチャとデータフロー

現在のフレームワークは、複数台のマシンでシミュレーションを並列実行・管理するための分散構成（Ray/Redis）で稼働しています。

### 処理フローの概要

1.  **インフラ構築**: `cluster_manager` が各ワーカーPCにSSH接続し、独立したDockerコンテナを起動します。
2.  **タスク生成**: 司令塔の `strategist` (AI) が次に検証すべきパラメータを計算し、キューに積みます。
3.  **タスク実行**: 各コンテナ内の `run_manager` (ワーカー) がタスクを受け取ります。実行直前に `param_logger` を経由してパラメータを `shared_store` (共有金庫) のメモリ上（バッファ）に一時保存し、シミュレーションを実行します。
4.  **結果解析**: シミュレーション完了後、出力されたJSONを `awchecker` が解析します。
5.  **データ結合**: `awchecker` は解析結果を `shared_store` に送信します。`shared_store` はバッファに保存されていた該当パラメータと結果を紐付け（結合）し、単一の `{scenario}_dataset.csv` に追記します。
6.  **再学習**: AIは更新された単一のデータセットCSVを読み込んで再学習し、より賢い次のタスクを生成します。

### 堅牢なエラー＆タイムアウト処理
分散システム特有の「フリーズ」や「通信エラー」から自己復旧し、AIへの悪影響を完全に遮断する仕組みが備わっています。
- **タイムアウト**: シミュレーションが完了しない場合、`run_manager` が検知してインフラ（AWSIM等）を強制再起動し、クリーンアップします。同時に `shared_store` に直接通知して結果を `-1`（エラー）として記録させ、後続が無限ループしないようダミーJSONを発行します。
- **解析エラー**: JSONの破損等で解析できない場合、`awchecker` がエラーを検知して結果を `-1` として記録します。
- **ガベージコレクション**: コンテナクラッシュ等で一生結果が届かない（孤児となった）パラメータが共有金庫のバッファに残り続けるのを防ぐため、一定時間（10分）で自動破棄するクリーンアップ機能が働きます。
- **AIへの影響遮断**: 記録された異常データ（`-1`）は、AIがデータセットをロードする際に自動でフィルタリング（除外）されるため、AIの学習モデルが汚染されることはありません。

## 運用コマンドリファレンス
システムの運用は、すべてマスター（21号機）のターミナルから行います。

### 実験の完全リセットとデータ退避
**完全に新しい実験**を始める前に、全コンピュータの過去のデータ（CSVや動画など）を一斉にバックアップ（退避）させ、環境をクリーンにします。
*(※ 単に検証を再開したいだけの場合は実行しないでください)*
```bash
python3 archive_results.py
```

### 実行結果の3D可視化
実験完了後、生成されたデータセットを3Dグラフで可視化し、安全境界を直感的に確認します。
```bash
# 最新の実験結果を可視化する場合
python3 visualize_traces.py ~/simulation_traces

# 過去に退避させた特定のデータを可視化する場合
python3 visualize_traces.py ~/simulation_traces_shared_20260512_144346
```

### システムの停止・強制終了
```bash
# 安全な停止（各ワーカーに終了シグナルを送り、キリの良いところで終わる）
pkill -2 -f master_orchestrator.py

# 強制終了（今すぐすべてのプロセスと全号機のコンテナを強制停止する）
pkill -9 -f master_orchestrator.py
python3 stop_containers.py
```

## リアルタイム監視・トラブルシューティング
マスターPC（21号機）の別のターミナルから、ワーカーPC（例: 23号機）の内部で動いているシミュレーションの状況をリアルタイム監視するためのコマンドです。

### 1. 現場監督の進捗ログ (ワーカーメインログ)
現在何ループ目のテストをしているか、エラーで再起動していないかを確認できます。
```bash
# 例: 23号機の場合
ssh tomita2@150.65.227.23 "docker exec sim_worker_23 tail -f /home/passd/simulation_traces/worker_log_sim_worker_23.txt"
```

### 2. インフラの内部ログ (詳細エラー調査)
シミュレータ本体やAutowareが正常に起動しているか確認したい場合に使います。
```bash
ssh tomita2@150.65.227.23 "docker exec sim_worker_23 tail -f /home/passd/simulation_traces/awsim.log"
ssh tomita2@150.65.227.23 "docker exec sim_worker_23 tail -f /home/passd/simulation_traces/autoware.log"
```

### コンテナ内での手動デバッグ
もし特定の号機でシミュレーションがうまく動かない場合、以下の手順でシステムと全く同じ環境設定のコンテナに手動で入り、どこでエラーが起きているか検証することができます。

```bash
# コンテナの中に入る
docker exec -it sim_worker_21 /bin/bash

# コンテナ内で手動起動テスト
cd /home/passd/awsim_labs && ./awsim_labs.x86_64 -noise false &
cd /home/passd/autoware && source install/setup.bash && ros2 launch autoware_launch e2e_simulator.launch.xml vehicle_model:=awsim_labs_vehicle sensor_model:=awsim_labs_sensor_kit map_path:=/home/passd/autoware_map/nishishinjuku_autoware_map launch_vehicle_interface:=true
```

## 今後の拡張性
新しくワーカーPC（例：24号機）を追加したい場合は、`redis_cluster/cluster_config.py` に新しいIPアドレスやコンテナ名、`ROS_DOMAIN_ID` を追記するだけで、システムが全自動でコンテナを構築し、クラスターの計算力（スループット）を向上させます。
