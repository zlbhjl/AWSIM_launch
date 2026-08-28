# AWSIM Adaptive Safety Testing Framework

## この README の位置づけ
この README は、主に**現行実装の使い方と運用方法**をまとめた文書です。

新しい汎用検証フレームへの分解方針、tree、contracts、移行順、責務分離ルールは
[docs/refactor_design.md](/home/passd/AWSIM_launch/docs/refactor_design.md)
を参照してください。

使い分け:

- `README.md`
  現行コードの実行方法、前提環境、運用上の入口
- `docs/refactor_design.md`
  新設計、移行方針、共通契約、責務分離
- `docs/implementation_rules.md`
  実装ルール、テストルール、回帰確認ルール

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
- **KDE重点サンプリングによるデータ再利用**: AIが探索した偏りのあるデータを捨てることなく、カーネル密度推定（KDE）で尤度比を算出し加重ECDFを構築することで、DKW不等式の数学的厳密性（i.i.d.の前提）を保ちつつシミュレーション回数を劇的に削減します。
- **データの一元管理と高効率化**: パラメータと結果は共有金庫(`shared_store.py`)によってメモリ上で結合され、単一のCSVデータセットとして出力されます。
- **堅牢な自動リカバリ**: タイムアウトや解析エラー発生時でもシステム全体がフリーズすることなく、異常データを安全に弾いてテストを継続します。
- **過去データからの自動復元と再開 (`--resume_from`)**: 退避させた過去のデータセットを現在の作業ディレクトリに復元し、シームレスに検証を再開・追記できます。
- **エッジケース自動抽出・集中検証 (`jama_edge`, `ttc_edge`)**: 過去のデータから「人間なら安全な領域での事故」や「ギリギリのニアミス」などの弱点をAIが自動抽出し、偶然か真の危険かを反復検証します。
- **最悪TTC探索 (`worst_ttc`)**: 今までの検証データから衝突しなかった「安全領域」を特定し、その中で最もTTCが小さかった（最悪の）ケースの下位N件を自動抽出し集中検証します。
- **境界ギャップ探索 (`boundary_gap`)**: 過去データを粗く区切って、危険寄りなのにサンプル数が薄いセルを自動抽出し、境界線づくりに足りない領域を重点検証します。各バッチ検証後に「まだ薄いセルが残っているか」を自動集計し、候補が残る限り次のターゲットを再抽出して繰り返します。`MAX_SAMPLES` では止まらず、ターゲットごとの進捗CSVも保存します。
- **二項信頼区間モード (`binomial_ci`)**: `c_collision` のような 0/1 指標に対して、純粋な一様ランダムサンプルだけを収集し、Wilson または Clopper-Pearson による95%信頼区間を逐次評価します。信頼区間幅が目標以下になったら自動停止します。
- **SMC (DKW) 検証モード (`--mode dkw`)**: Sequential-DKW不等式を用いて、システムの安全性を逐次的に数学的に証明します。ステージごとに収束をチェックし、信頼区間が目標精度に達したら早期終了します。
- **固定サンプリング+一括DKW評価モード (`--mode dkw_fixed`)**: 指定回数（`--max_samples`）の一様乱数サンプリングを必ず実行し、全データ収集後に1回だけDKW評価を行います。`--dkw_pure_smc` で過去データを除外した純粋評価と、全データを使った評価を選択可能です。
- **Config-Driven アーキテクチャ**: シナリオ (Uターン、割り込み等) のパラメータやAIの探索範囲、タイムアウト時間を単一の設定ファイルで柔軟に定義可能 (`configs/`)。
- **フォーカス (集中) モード**: 特定のパラメータの周辺に絞ってテストを反復するピンポイント検証機能。
- **リアルタイム進捗監視**: 司令塔の画面で、各ワーカーが「待機中」「実行中」「タイムアウト」など、何をしているかをリアルタイムで1行にまとめて表示します。
- **JAMA物理モデルに基づく理論値算出**: シミュレーションの入力パラメータから、物理限界（空走時間・ブレーキ性能）に基づく理論上の停止距離と安全マージン（Zone）を同時算出・記録し、AIの学習特徴量として活用 (`theoretical_calculator.py`)。
- **外部検証器の legacy 互換入口**: AWSIM_launch の外側にある検証器を、対象システムと切り離したまま起動できる互換CLIを保持しています。現在の `run_external_verifier.py` は互換 adapter の入口だけを残し、中では `targets/bbsl/*` と `evaluation/ft4d_service.py` の新経路を直接呼びます。
- **内部 FT4D コア**: FT4D を `verification_core/ft4d` として AWSIM_launch 内へ保持し、`estimator.py` から実行できます。BBSL 側は raw result を出力し、FT4D の計算本体と信頼度再構成は AWSIM_launch 側へ寄せています。
- **BBSL raw result 連携**: `BBSL-test/examples/run_full_experiment_all.py` / `run_full_experiment.py` は `--ft4d-backend none` で raw result のみを保存でき、AWSIM_launch 側が `U / D(n) / E(n)`、`recognition_test`、信頼度伝播を再構成します。
- **FT4D ベースの司令塔補助**: `strategist.py` は FT4D 結果を読み、信頼度不足ノードの列挙だけでなく、`node_id` 単位の集約、不足理由分類、推奨アクション生成まで行えます。

## ファイル・ディレクトリ構成

```text
AWSIM_launch/
├── local_worker.py           # 【ホストワーカー】--with_host_worker 時の run_manager.py のプロセス起動・管理。
├── master_orchestrator.py    # 【司令塔】システム全体の起動、クラスター構築、AIタスクのキュー管理/停止判断。
├── run_manager.py            # 【ワーカー】各ノードのメインプロセス。司令塔からのタスク受信、process_controller.py を介したシミュレーション実行を管理。
├── run_external_verifier.py  # 【外部検証器入口】legacy 互換CLI。互換入口だけを残し、中では新しい BBSL target 経路を直接束ねる。
├── run_ft4d_smoke.py         # 【内部FT4D疎通確認】AWSIM trace fixture を新経路で流して FT4D を確認するCLI。
├── run_bbsl_local_ft4d.py    # BBSL raw result を AWSIM_launch 側の新経路で FT4D 再計算するCLI。
├── run_scenario.py           # 単一のシミュレーションを実行するスクリプト。動的パラメータを受け取りシナリオを構築。
├── strategist.py             # AIの探索戦略を司る頭脳。現在のフェーズを判断し、次に検証すべきパラメータを決定。FT4D の confidence gap 集約も担当。
├── estimator.py              # 【内部モジュール】ガウス過程回帰やDKW不等式など、統計的な評価・計算を行う数学エンジン。BBSL raw result からの FT4D 実行APIも保持。
├── awchecker.py              # シミュレーション結果(JSON)を解析し、安全性を判定。判定結果を共有金庫へ送信。
├── param_logger.py           # テスト実行時のパラメータを一時的に共有金庫のバッファへ送信。
├── theoretical_calculator.py # JAMA物理モデルに基づく理論的安全領域(Zone)とマージンを計算するモジュール。
├── point_extractors.py       # 【内部モジュール】データセットから探索候補点(JAMAエッジ等)を抽出・分類する共通アルゴリズム群。
├── extract_region_data.py    # 【互換CLI】tools/analysis/extract_region_data.py への薄いラッパー。
├── analyze_ttc_consistency.py # 【互換CLI】tools/analysis/analyze_ttc_consistency.py への薄いラッパー。
├── fix_dataset_labels.py     # 過去のデータセットを最新の抽出ロジックで全号機から並列再解析し、安全に修復(更新)するスクリプト。
├── dataset_repo.py           # データセットCSVの読み書き、--resume_from による過去データ復元
├── verification_core/
│   └── ft4d/                 # AWSIM_launch 内に保持する共通 FT4D コア
├── adapters/
│   ├── awsim/                # AWSIM 用の U/D/E 構築アダプタ骨格
│   └── bbsl/                 # BBSL raw result を AWSIM_launch 側 FT4D 入力へ変換するアダプタ
├── external_verifiers/
│   ├── base.py               # 旧互換CLI用の共通インターフェース。
│   ├── registry.py           # 旧互換CLI用アダプタの登録。
│   └── bbsl_ft4d.py          # 旧 import 互換 wrapper。実体は verifiers/compatibility/legacy_bbsl_ft4d_adapter.py。
├── verifiers/
│   ├── compatibility/
│   │   └── legacy_bbsl_ft4d_adapter.py # BBSL legacy 互換入口の実体。内部では targets/bbsl/* + evaluation/ft4d_service.py を呼ぶ。
│   └── maude/
│       ├── backend.py        # Maude 実行境界
│       └── evaluator.py      # Maude 結果の評価器
├── redis_cluster/
│   ├── cluster_config.py     # ワーカーPCのIPやコンテナ名、通信割り当て設定などを一元管理。
│   ├── cluster_manager.py    # 各PCにSSH接続し、Dockerコンテナを自動起動・同期するクラスター構築スクリプト。
│   ├── process_controller.py # AWSIM/Autoware/RuntimeMonitorの起動・終了・監視、Xvfb設定をカプセル化。
│   ├── task_queue.py         # 【司令塔キュー】TaskQueueActor。ワーカーからのタスク取得をスレッドセーフに管理するRay Actor。
│   └── shared_store.py       # 【共有金庫】非同期で送られてくるパラメータと結果を結合し、単一のCSVに記録するスレッドセーフなRay Actor。メモリリーク防止機能付き。
├── configs/                  # シナリオごとの設定ファイルを格納するディレクトリ。
│   └── uturn.py              # Uターンシナリオ用の設定 (探索範囲、ターゲット優先度、タイムアウト秒数など)。
├── visualize_traces.py       # 【legacy plot wrapper】tools/plot/visualize_traces.py を呼ぶ互換入口。
├── visualize_traces_split.py # 【legacy plot wrapper】tools/plot/visualize_traces_split.py を呼ぶ互換入口。
├── visualize_worker_stats.py # 【legacy plot wrapper】tools/plot/visualize_worker_stats.py を呼ぶ互換入口。
├── visualize_min_ttc.py      # 【legacy plot wrapper】tools/plot/visualize_min_ttc.py を呼ぶ互換入口。
├── visualize_min_ttc_3d.py   # 【legacy plot wrapper】tools/plot/visualize_min_ttc_3d.py を呼ぶ互換入口。
├── visualize_jama_zones.py   # 【legacy plot wrapper】tools/plot/visualize_jama_zones.py を呼ぶ互換入口。
├── visualize_risk_matrix.py  # 【legacy plot wrapper】tools/plot/visualize_risk_matrix.py を呼ぶ互換入口。
├── visualize_collision_regions.py  # 【legacy plot wrapper】tools/plot/visualize_collision_regions.py を呼ぶ互換入口。
├── visualize_collision_surfaces.py # 【legacy plot wrapper】tools/plot/visualize_collision_surfaces.py を呼ぶ互換入口。
├── visualize_worker_failure_clusters.py # 【legacy plot wrapper】tools/plot/visualize_worker_failure_clusters.py を呼ぶ互換入口。
├── analyze_boundary_gap.py         # 【互換CLI】tools/analysis/analyze_boundary_gap.py への薄いラッパー。
├── tools/
│   ├── analysis/
│   │   ├── extract_region_data.py           # 条件式や bounds で dataset を切り出す本体CLI。
│   │   ├── analyze_ttc_consistency.py       # 反復テストデータからTTCのばらつきを分析し、確実/偶然リスクに分類してDKW評価を出力する本体CLI。
│   │   ├── analyze_boundary_gap.py          # boundary_gap 用に、境界セルのサンプル不足状況と優先候補を集計する本体CLI。
│   │   ├── consistency_summary.py           # verify_consistency の DKW summary CSV を読む本体CLI。
│   │   └── bbsl_confidence_gap_summary.py   # BBSL confidence-gap JSON を読む閲覧用CLI。
│   └── plot/
│       ├── common.py                        # plot 系共通の dataset path / output path 解決。
│       ├── visualize_traces.py              # 実行結果CSVの3D可視化の本体CLI。
│       ├── visualize_traces_split.py        # worker ごとの分割3D可視化の本体CLI。
│       ├── visualize_worker_stats.py        # worker 別の衝突/TTC発生率比較の本体CLI。
│       ├── visualize_min_ttc.py             # min_ttc 段階表示の本体CLI。
│       ├── visualize_min_ttc_3d.py          # min_ttc 連続値ベース3D可視化の本体CLI。
│       ├── visualize_jama_zones.py          # JAMA理論安全領域可視化の本体CLI。
│       ├── visualize_risk_matrix.py         # 衝突/TTC/距離を合わせたリスク可視化の本体CLI。
│       ├── visualize_collision_regions.py   # 衝突/非衝突セルのボクセル表示の本体CLI。
│       ├── visualize_collision_surfaces.py  # 衝突外縁とAI境界面の可視化の本体CLI。
│       └── visualize_worker_failure_clusters.py # timeout / shifted success 可視化の本体CLI。
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

# 【検証】デフォルト(CVM)でAI探索を開始する場合
python3 master_orchestrator.py

# 【検証】CTRVモードを指定してAI探索を開始する場合
python3 master_orchestrator.py --ext_mode ctrv

# 【修復】デフォルト(CVM)で過去データを再解析する場合
python3 fix_dataset_labels.py

# 【修復】CTRVモードを指定して過去データを再解析する場合
python3 fix_dataset_labels.py --mode ctrv

# 【ホスト併用モード】21号機のみ画面を表示して直接検証し、他のワーカーはコンテナで実行する場合
python3 master_orchestrator.py --type uturn --mode explore --with_host_worker

# 21号機をホストOSで動かすが、画面は出さずに完全に裏で回す場合
python3 master_orchestrator.py --type uturn --mode explore --with_host_worker --headless_host

# (任意) 上記をバックグラウンドで永続稼働させる場合
nohup python3 master_orchestrator.py --type uturn --mode explore --with_host_worker --headless_host > orchestrator_out.log 2>&1 &

# 【マージンモード】過去のデータからAIの学習のみを行い、「安全領域の死角」だけを潰すことに特化する場合
python3 master_orchestrator.py --type uturn --mode margin

# Config (uturn.py) 内に定義された FOCUS_POINTS を使用する場合
python3 run_manager.py --type uturn --mode focus

# CLIから検証したいポイントを直接指定する場合
python3 run_manager.py --type uturn --mode focus --focus_points '[{"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 15.0}]'
# 【集中モード】既知の特定のポイント周辺を重点的に検証する場合
python3 master_orchestrator.py --type uturn --mode focus

# 【JAMAエッジ探索モード】過去のデータから、人間なら安全な領域でAIが事故を起こすケースを自動抽出し重点検証する場合
python3 master_orchestrator.py --type uturn --mode jama_edge

# 【TTCエッジ探索モード】過去のデータからTTCが1.5秒以下のニアミスを自動抽出し、それが処理落ち等の偶然か真の危険かを分別・探索する場合
python3 master_orchestrator.py --type uturn --mode ttc_edge

# 【最悪TTC探索モード】これまでの検証データから衝突しなかった安全領域内の「TTC最悪ケース（下位10件）」を抽出し、本当に安全か周辺を集中検証する場合
python3 master_orchestrator.py --type uturn --mode worst_ttc

# 【境界ギャップ探索モード】過去データを読み、危険寄りだがサンプル数が薄い領域を自動抽出し、
# 検証後に境界セルを再判定しながら、候補がなくなるまで自動反復する場合
python3 master_orchestrator.py --type uturn --mode boundary_gap

# いまのデータセットで、boundary_gap の未解消セルを単独集計する場合
python3 tools/analysis/analyze_boundary_gap.py ~/simulation_traces

# 【二項信頼区間モード】純粋ランダム標本だけで c_collision の95%信頼区間を Wilson で直接評価する場合
python3 master_orchestrator.py --type uturn --mode binomial_ci --binomial_target c_collision --binomial_method wilson --binomial_confidence 0.95 --binomial_target_width 0.02

# Clopper-Pearson でより保守的に評価する場合
python3 master_orchestrator.py --type uturn --mode binomial_ci --binomial_target c_collision --binomial_method clopper-pearson --binomial_confidence 0.95 --binomial_target_width 0.02

# 【DKW証明モード】統計的モデル検査(SMC)で安全性を証明する
# 1. 手動で指定した領域の安全性を証明する場合
python3 master_orchestrator.py --type uturn --mode dkw --dkw_bounds '{"dx0": [20.0, 25.0], "ego_speed": [30.0, 35.0]}'

# 2. 過去のデータから自動で領域を算出して安全性を証明する場合
#  emp_safe: 過去のデータで「衝突しなかった（経験的安全）」領域。
python3 master_orchestrator.py --type uturn --mode dkw --dkw_region emp_safe

#  jama_safe: JAMA物理モデルで「人間なら安全」とされた領域。
python3 master_orchestrator.py --type uturn --mode dkw --dkw_region jama_safe

#  intersect_safe: 上記2つの両方を満たす（AND）、確実な安全領域。
python3 master_orchestrator.py --type uturn --mode dkw --dkw_region intersect_safe

#  union_safe: 上記2つのどちらかを満たす（OR）、少し広めの安全領域。
python3 master_orchestrator.py --type uturn --mode dkw --dkw_region union_safe

# 3. 過去の探索データを無視し、純粋なSMCサンプリングのみで数学的厳密性を保って証明する場合
# (デフォルトではKDEによる重要度サンプリング補正を行い、AIの探索データも厳密な数学的証明の有効サンプルとして再利用されます)
python3 master_orchestrator.py --type uturn --mode dkw --dkw_region intersect_safe --dkw_pure_smc

# 4. TTCや距離など、複数の指標を同時に評価し、ボンフェローニ補正を用いた絶対的な同時保証を行う場合
python3 master_orchestrator.py --type uturn --mode dkw --dkw_simultaneous

# (応用) KDEデータ再利用 ＋ JAMA経験的安全領域 ＋ ボンフェローニ同時保証 をすべて組み合わせた最強の証明コマンド
python3 master_orchestrator.py --type uturn --mode dkw --dkw_region intersect_safe --dkw_simultaneous --resume_from ~/simulation_traces_shared_20260611_104737

# 【固定サンプリング+一括DKW評価モード (dkw_fixed)】指定回数サンプリング後に一括DKW評価
# 5. 純粋SMC: 過去データを使わず、新規6000回のサンプリングのみで証明する場合
python3 master_orchestrator.py --type uturn --mode dkw_fixed --dkw_region intersect_safe --dkw_simultaneous --max_samples 6000 --dkw_pure_smc

# 6. 全データ利用: resume_fromの過去データ＋新規サンプルで証明する場合
python3 master_orchestrator.py --type uturn --mode dkw_fixed --dkw_region intersect_safe --dkw_simultaneous --resume_from ~/simulation_traces_shared_20260611_104737 --max_samples 6000

# 7. 中断再開: 途中まで終わった dkw_fixed を再開する場合（_dataset.csv の続きから）
python3 master_orchestrator.py --type uturn --mode dkw_fixed --dkw_region intersect_safe --dkw_simultaneous --max_samples 6000

# 過去に退避させた特定のデータ(例: ~/simulation_traces_shared_...)を復元して、そこから探索を再開する場合
python3 master_orchestrator.py --type uturn --mode ttc_edge --resume_from ~/simulation_traces_shared_20260525_184326
```

### 2. 外部検証器を AWSIM_launch から起動する
この入口は、旧構成との互換を保つための legacy CLI です。
新設計では BBSL の本体は `targets/bbsl/` 側へ寄せ、ここは移行期の互換入口として残します。
現在の `bbsl_ft4d` アダプタは、BBSL 側の古い実験スクリプトを直接たたくのではなく、
legacy 入口の形を保ったまま AWSIM_launch 側の新経路を in-process で呼びます。
つまり実際の FT4D 評価は AWSIM_launch 側の
`targets/bbsl/result_interpreter.py -> targets/bbsl/verification_input.py -> evaluation/ft4d_service.py`
で行います。

```bash
# BBSL-test の FT4D 実験を AWSIM_launch 側から legacy 互換CLIで起動
python3 run_external_verifier.py \
  --verifier bbsl_ft4d \
  --target-repo /home/passd/BBSL-test \
  --reuse-existing-output \
  --tree basic \
  --sigma-pf-source dataset \
  --sigma-pb-mode delta-clean \
  --and-rule min

# 軽い疎通確認
python3 run_external_verifier.py \
  --verifier bbsl_ft4d \
  --target-repo /home/passd/BBSL-test \
  --reuse-existing-output \
  --mini \
  --max-images 3 \
  --tree basic \
  --sigma-pf-source dataset \
  --sigma-pb-mode delta-clean \
  --and-rule min
```

このコマンドは AWSIM_launch 内に正規化済みの結果 JSON を保存しつつ、
内部では `run_bbsl_local_ft4d.py` を呼びます。
つまりこの経路は、

- AWSIM_launch = legacy 互換入口として検証器を起動し、新経路で FT4D を評価するフレーム
- BBSL-test = raw result を生成する実験エンジン

という役割分担です。

### 3. BBSL の出力を AWSIM_launch 側で読み直し、ローカル FT4D コアで再計算する
こちらが現在の推奨経路です。外部の `BBSL-test` でノイズ生成・推論・BBSL判定を行い、
その raw result JSON を AWSIM_launch 側で
`targets/bbsl/result_interpreter.py -> targets/bbsl/verification_input.py -> evaluation/ft4d_service.py`
へ流して FT4D を計算し直します。

```bash
# 軽い確認
python3 run_bbsl_local_ft4d.py \
  --target-repo /home/passd/BBSL-test \
  --reuse-existing-output \
  --execution-mode legacy \
  --mini \
  --max-images 3 \
  --tree basic \
  --sigma-pf-source dataset \
  --sigma-pb-mode delta-clean \
  --and-rule min

# batch-loop を fresh で最初から回す
python3 run_bbsl_local_ft4d.py \
  --target-repo /home/passd/BBSL-test \
  --execution-mode batch-loop \
  --run-mode fresh \
  --mini \
  --max-images 3 \
  --tree basic \
  --sigma-pf-source dataset \
  --sigma-pb-mode delta-clean \
  --and-rule min

# 既存 clean baseline / noisy batch を引き継いで続きから確認
python3 run_bbsl_local_ft4d.py \
  --target-repo /home/passd/BBSL-test \
  --mini \
  --execution-mode batch-loop \
  --run-mode resume \
  --tree basic \
  --sigma-pf-source dataset \
  --sigma-pb-mode delta-clean \
  --and-rule min \
  --max-batches 1

# sigma_pf を外部設定で与える例
python3 run_bbsl_local_ft4d.py \
  --target-repo /home/passd/BBSL-test \
  --execution-mode batch-loop \
  --run-mode fresh \
  --tree basic \
  --sigma-pf-value SALT_PEPPER=0.12 \
  --sigma-pf-value OCCLUSION=0.03 \
  --sigma-pf-value BLUR=0.08 \
  --sigma-pb-mode delta-clean \
  --and-rule min
```

この batch-loop 経路では:

- `clean` は最初に1回だけ実行
- `clean` で成功した画像だけに noisy 条件をかける
- `BBSL-test/output/batches/` に baseline / batch raw result を保存
- `--run-mode fresh` のときは、開始前に `output/batches/*.json` と
  `output/*.png` と `data/kitti/ft4d_batches/` の生成済みノイズ画像を削除する
- `--run-mode resume` のときは、既存の baseline / noisy batch を見て続きから再開する
- `--sigma-pf-value EVENT_ID=value` または `--sigma-pf-json path.json` を渡すと、
  AWSIM_launch 側の FT4D 再計算で `sigma_pf` 仮定値を上書きできる
- `AWSIM_launch` 側で複数 batch を束ねて `U / D(n) / E(n)` を再構成
- Chernoff-Hoeffding の必要サンプル数や信頼度が足りなければ次 batch を追加

`sigma_pf` 上書きが指定された場合は、`--sigma-pf-source dataset` を指定していても
AWSIM_launch 側では `assumption` 扱いで再計算します。

現在は

- `--execution-mode legacy`
- `--execution-mode batch-loop --condition-policy all`
- `--execution-mode batch-loop --condition-policy underconfident`

の主要経路が、最終的に AWSIM_launch 側の新評価経路へそろっています。
FT4D の計算本体、Bonferroni に基づく `recognition_test` の再計算、
confidence gap の集約は AWSIM_launch 側で実行します。

役割分担は次のとおりです。

- `BBSL-test` = 実験エンジン
  - ノイズ生成
  - 推論
  - 正解比較
  - raw result 保存
- `AWSIM_launch` = 検証コア
  - FT4D
  - `recognition_test` 再構成
  - confidence gap 集約
  - 将来の探索判断

#### BBSL 側で raw result だけを作るコマンド
AWSIM_launch へ渡す前段として、BBSL 側単独でも次のように raw result を作れます。

```bash
# clean baseline
python3 /home/passd/BBSL-test/examples/run_ft4d_batch_experiment.py \
  --mode clean-baseline \
  --mini \
  --max-images 4

# noisy batch
python3 /home/passd/BBSL-test/examples/run_ft4d_batch_experiment.py \
  --mode noisy-batch \
  --mini \
  --batch-id 1 \
  --master-seed 1000

# 同一画像で全条件比較する経路
python3 /home/passd/BBSL-test/examples/run_full_experiment_all.py \
  --mini \
  --max-images 3 \
  --tree basic \
  --sigma-pf-source dataset \
  --sigma-pb-mode delta-clean \
  --and-rule min \
  --ft4d-backend none

# 条件ごとに画像を分割する従来経路
python3 /home/passd/BBSL-test/examples/run_full_experiment.py \
  --mini \
  --max-images 6 \
  --tree basic \
  --sigma-pf-source dataset \
  --sigma-pb-mode raw \
  --and-rule min \
  --ft4d-backend none
```

生成される主なファイルは次です。

- `output/batches/clean_baseline.json`
- `output/batches/clean_success_image_ids.json`
- `output/batches/noisy_batch_0001.json`
- `output/experiment_all_raw_result.json`
- `output/experiment_all_raw_result_mini.json`
- `output/experiment_full_raw_result.json`
- `output/experiment_full_raw_result_mini.json`

`run_bbsl_local_ft4d.py --execution-mode batch-loop` は
`output/batches/` を使い、`--execution-mode legacy` では
`experiment_*_raw_result*.json` を読みます。

### 4. AWSIM_launch 内部の FT4D コアを単独で確認する
このコマンドは、まず AWSIM_launch 内だけで FT4D コアが成立しているかを確認するためのものです。

```bash
python3 run_ft4d_smoke.py
```

これは `tests/fixtures/awsim/normal_trace_maude.json` を
`targets/awsim/result_interpreter.py -> targets/awsim/verification_input.py -> evaluation/ft4d_service.py`
へ通し、`verification_core/ft4d/config/awsim_demo_tree.json` を用いて FT4D を計算します。
現在は AWSIM の `EvaluationRecord.output` に `c_collision`, `c_ttc_*` などの Maude 判定値が入り、
その値を使って smoke を確認します。

### 5. チェッカープロセスの起動 (別ターミナル)
生成されたシミュレーションデータ (JSON)を手動で安全性を判定するために、ターミナルでチェッカーを使ってくださいしてください。

```bash
python3 awchecker.py --type uturn
```

## 出力データ (Traces)
テストの実行結果とログは `~/simulation_traces` ディレクトリに出力されます。
- `{scenario}_dataset.csv`: 共有金庫によって結合された、AIの学習に直結する完全なデータセット (パラメータ + 評価結果 + 理由)。
  - ※ **最小接近距離 (`min_distance`)**: 事実として車同士が何メートルまで接近したかの最短距離。TTCの予測誤差を排除した物理的なニアミス指標として記録されます。
  - ※ JAMA物理モデルに基づく理論値（`theory_margin_*`, `theory_zone_*` 等）も記録され、シミュレータの実挙動と物理限界の乖離分析に活用できます。アプローチA（壁想定）とアプローチB（NPC前進考慮）の両方が保存されます。
- `checker_errors_detail.log`: 解析ツールで異常が発生した際の詳細なエラーログ (STDOUT/STDERR)。
- `{scenario}_test_sim{N}.json`: 各ループのRuntime Monitorの詳細トレースデータ。
- `awsim.log` / `autoware.log`: インフラ側の生ログ (エラー調査用)。

## 新しいシナリオの追加方法
いまの形では、新しい AWSIM シナリオを足すときの基本修正点は次の 4 か所です。

1. `targets/awsim/case_kinds/<new_case>.py`
2. `targets/awsim/scenario_builders/<new_case>_builder.py`
3. `targets/awsim/scenario_runner.py`
4. `configs/<new_case>.py`

一言でいうと、役割分離は次のルールです。

- `case_kind = 何を探索するか`
- `builder = どう組み立てるか`
- `runner = どれを呼ぶか`

### 4 か所の役割

- `targets/awsim/case_kinds/<new_case>.py`
  - 「実験定義」を置きます。
  - 基本は `PARAM_RANGES`, `FIXED_PARAMS`, `RESULT_LABELS`, `FORMULAS`, `TIMEOUT_SEC` です。
  - 必要なら `SCENARIO_PROFILES`, `FOCUS_POINTS` もここへ置きます。
- `targets/awsim/scenario_builders/<new_case>_builder.py`
  - lane / offset / speed などを組み立てます。
  - 動的パラメータから必要値を解決し、最後に旧 `AWSIMScriptPy` のシナリオ関数へ接続します。
- `targets/awsim/scenario_runner.py`
  - `scenario_type == "<new_case>"` の分岐を 1 本追加して、新しい builder を呼ぶだけにします。
- `configs/<new_case>.py`
  - 旧互換の入口です。実体は `case_kind` を再 export するだけで構いません。

追加で必要になるのは次の場合だけです。

- 専用 theory が必要な場合
  - `targets/awsim/theory_specs/<new_case>.py` を追加します。
- 既存の 3 軸前提を外れる可視化が必要な場合
  - `tools/plot/*` 側を調整します。

いまは下流の `dkw` / `binomial_ci` / `jama_edge` / `result_sink` は
`case_kind + build_theory_metrics(...)` を見る形になっているため、
新しいシナリオを足すたびにそこを毎回修正する必要はありません。

### 設計ルール

- `case_kind` にシミュレータ組み立て処理は入れないでください。
- `runner` に個別ロジックを溜め込まないでください。
- 速度帯で挙動が変わるなら `SCENARIO_PROFILES` を使ってください。
- 手動実行でも `bash -i -c` と `source /home/passd/autoware/install/setup.bash` を崩さないでください。
- 旧 `AWSIMScriptPy` 側の scenario 本体も最新化されていることを確認してください。

### 新しいシナリオ設計時の注意と調整アルゴリズム

- `FIXED_PARAMS` に初期位置や加速度を一度置いて終わりにしないでください。
- 新しいシナリオでは、`ego_speed` / `npc_speed` の帯ごとに
  - `ego_init_offset`
  - `npc_init_offset`
  - `goal_offset`
  - `acceleration`
  が本当に十分かを確認してください。
- `uturn` は `SCENARIO_PROFILES` で速度帯ごとに開始位置や加速度を切り替える設計にしてあり、単純な固定値より安定して動かせるようにしています。
- `cutin` のように全ケースで同じ `FIXED_PARAMS` を使う形は、最初の移植としてはよいですが、「全速度帯で十分距離がある」「車が確実に動ける」「route / operation mode が安定する」ことをまだ保証しません。
- したがって、新しいシナリオを実験基盤へ正式に入れるときは、必要に応じて `uturn` と同様に速度帯プロファイルを導入し、シナリオごとに十分距離・十分加速度を設計してください。
- `ego_goal_offset` は「目標速度に達する最低距離」だけで決めないでください。基本は `加速距離 + イベント区間 + 余裕` で見積もり、さらに少なくとも旧来の安定値を下回らないようにしてください。
- `npc_init_offset` も単なる固定値ではなく、必要なら `ego_speed` / `npc_speed` / `lateral velocity` / `event time` から必要 gap を式で見積もり、そのうえで旧来の安定値を下回らないようにしてください。
- `NPC が動かない` 問題に備えて、`npc_start_speed_ratio` や `spawn_trigger_speed_ratio` のような始動条件を `SCENARIO_PROFILES` で持てるようにしておくと安全です。
- 実運用上の調整アルゴリズムは次の形を基本にしてください。
  - `ego_goal_offset`
    - `warmup_distance = v^2 / (2a)`
    - `event_distance = v * event_time`
    - `goal_offset = init_offset + warmup_distance + event_distance + static_margin`
    - 最後に `max(calculated_value, legacy_minimum)` を取る
  - `npc_init_offset`
    - `ego` と `npc` の速度、必要なら `lateral velocity` から trigger までの必要 gap を見積もる
    - 旧アンカーケースとの差分だけを offset に足す
    - 最後に `max(calculated_value, legacy_minimum)` を取る
  - `npc_start_speed_ratio` / `spawn_trigger_speed_ratio`
    - `ego` が目標速度ぴったりに届く前でも NPC が前進開始できるようにする
    - `av_speed >= ratio * target_speed` の形で使う
    - ただし緩めすぎず、実機で `route` と `operation_mode` が安定する値にする

### `cutout` 移植時の注意

- `cutout` は旧 `AWSIMScriptPy` 側に `dynamic_spawn.py` のロジックがあり、新フレーム側の `case_kind` / `builder` だけ直しても、コンテナ内の `AWSIMScriptPy/scenarios/cutout/dynamic_spawn.py` が古いままだと期待どおりに動きません。手動検証時は、`targets/awsim/case_kinds/cutout.py` だけでなく、`AWSIMScriptPy/scenarios/cutout/dynamic_spawn.py` も最新化されていることを確認してください。
- `cutout` の `ego_goal_offset` は「目標速度に達する最低距離」だけで決めると短すぎます。`加速距離 + イベント区間 + 余裕` を見た上で、少なくとも旧来の安定値を下回らないようにしてください。`2026-08-28` 時点の実運用値は `30/35/40 km/h -> 210/240/280 m` です。
- `cutout` の動的 spawn では、`npc1` / `npc2` の出現条件は `av_speed >= spawn_trigger_speed_ratio * _speed` です。さらに `npc1` の lane change 条件を厳しくしすぎると、「spawn はするが動かない」状態になります。
- `2026-08-28` の実機調整では、`npc1` の `FollowLane` は無条件、`ChangeLane` は `actor_speed >= spawn_trigger_speed` まで緩めることで安定しました。以前の `actor_speed >= _speed` は厳しすぎて、`npc1` が十分に加速できず cut-out に入らないことがありました。

### `uturn` / `cutin` の NPC1 始動条件

- `uturn` と `cutin` では、カーブ自体の開始条件は昔から変えていません。`uturn` は `longitudinal_distance_to_ego <= dx0` で `FollowWaypoints` に入り、`cutin` は `longitudinal_distance_to_ego <= dx0` で `ChangeLane` に入ります。
- `2026-08-28` の実機確認では、問題になっていたのは「曲がる条件」ではなく、「NPC1 が前進を開始する条件」でした。旧実装ではどちらも `av_speed >= _ego_speed - 0.2` で、ego が目標速度ぴったり近くまで出ないと NPC1 が動き始めないことがありました。
- このため新フレーム側では `npc_start_speed_ratio` を `SCENARIO_PROFILES` に持たせ、`uturn` / `cutin` の `AWSIMScriptPy` 側へ渡すようにしました。現在は `av_speed >= npc_start_speed_ratio * _ego_speed` を使います。
- `2026-08-28` 時点の実運用値はおおむね `30/35/40 km/h -> 0.898 / 0.9083 / 0.916` です。つまり、ego が完全に目標速度へ届く前でも、NPC1 が少し早めに走り始められるようにしています。
- 新しいシナリオでも、`NPC がときどき動かない` ときは `dx0` や lane change 条件だけを見るのではなく、まず `NPC1 の前進開始条件` が実速度に対して厳しすぎないかを確認してください。

### `swerve` で入れた補正の考え方

- `swerve` でも `2026-08-28` 時点で `npc_start_speed_ratio` を導入し、`av_speed >= npc_start_speed_ratio * _ego_speed` で `npc1` が前進を開始するようにしています。
- さらに `npc_init_offset` と `ego_goal_offset` は、式で必要距離を見積もった上で、旧アンカーケース `30/10`, `30/15`, `40/10`, `40/15` の安定値を下回らないように補正しています。
- つまり `swerve` の調整は「全部を完全に式へ置き換える」のではなく、
  - 旧安定値を基準にする
  - そこから必要なら増やす
  - でも短くしすぎて旧来より不安定にはしない
  という方針です。

### `deceleration` の移植と調整

- `deceleration` は旧 `AWSIMScriptPy` 側の `base.py` ではなく、`dynamic_spawn.py` を正本として新フレームへ接続しています。Autoware が前方車両を早めに警戒して速度を落としてしまうため、静的 spawn より動的 spawn の方が JAMA の意図に近いからです。
- 新フレーム側は `targets/awsim/case_kinds/deceleration.py`, `targets/awsim/scenario_builders/deceleration_builder.py`, `targets/awsim/theory_specs/deceleration.py`, `configs/deceleration.py` を追加し、`scenario_runner.py` から実行できるようにしています。
- `2026-08-28` 時点の `deceleration` は、まず JAMA 寄りの `ego_speed` 単軸で入れています。つまり、他シナリオのような `dx0 / ego_speed / npc_speed` の 3 軸ではなく、`spawn_headway_sec=2.0` と `npc_deceleration=9.8` を固定にした最初の移植です。
- `ego_goal_offset` は `cutout` と同じ考え方で、`加速距離 + イベント区間 + 余裕` から見積もり、少なくとも旧来の安定値 `30/35/40 km/h -> 210/240/280 m` を下回らないようにしています。
- `spawn_trigger_speed_ratio` と `decel_trigger_speed_ratio` も式ベースへ切り替えています。つまり
  - `spawn_trigger_speed_ratio`
    - ego が完全に目標速度へ届く前でも NPC を spawn できるようにする
  - `decel_trigger_speed_ratio`
    - NPC が `_speed` ぴったりまで安定しなくても減速へ入れるようにする
  という役割です。
- `2026-08-28` の代表ケース `ego_speed=30 km/h` では、21号コンテナで `bash -i -c` と `source /home/passd/autoware/install/setup.bash` 付きの手動実行を行い、`npc1` の spawn、`FollowLane`、`SetTargetSpeed` 送信まで確認済みです。
- ただし `deceleration` でも、repo 内の `case_kind` / `builder` だけでは不十分です。手動検証時は、コンテナ内の `AWSIMScriptPy/scenarios/deceleration/dynamic_spawn.py` も最新化されていることを確認してください。

## 技術的な工夫・トラブルシューティング (分散自動化に関する解決策)

本システムは、マスター機からリモート機を制御し、バックグラウンドでシミュレーションを完全自動化しています。その際、手動でターミナルから実行した時と異なり「車が動かない」「レーダー（点群）が消える」「通信が詰まる」といった特有の問題に対処するため、以下の実装が組み込まれています。

1. **対話型シェル (`bash -i`) による完全なROS/DDS環境ロード (`cluster_manager.py` / `run_manager.py`)**
   コンテナを起動してバックグラウンドでコマンドを実行する際、通常の `bash -c` ではUbuntuの仕様により `~/.bashrc` の読み込みが途中でキャンセルされます。これによりCycloneDDS等の大容量通信向けのチューニング設定がAutowareに適用されず、通信詰まりやレーダーデータが消失する問題がありました。これを `bash -i -c` を用いて対話モードを偽装することで、手動ログイン時と全く同じROS通信環境を確立しています。
   - **重要**: コンテナ内で `docker exec` を使って手動デバッグするときも、この原則を崩さないでください。`docker exec ... bash -c ...` や `docker exec ... bash -lc ...` で直接起動すると、`run_manager.py` / `cluster_manager.py` と同じ環境にならず、`README` 通りの手順でも「車が動かない」「route が入らない」「点群やDDS通信が不安定になる」といった切り分けしづらい差分が入ります。手動実行時も `bash -i -c` を使い、必要ならその中で `source /home/passd/autoware/install/setup.bash` まで明示してください。
2. **マスター・リモート間のROS通信の分離 (`cluster_manager.py`)**
   複数台のコンピュータで同時にシミュレーションを実行する際、ROS 2の通信がネットワーク上で混線しないよう、コンテナ起動時に `ROS_DOMAIN_ID` を号機ごとに割り当て、完全に独立した通信環境を構築しています。
3. **リモート環境 (ヘッドレス) での仮想ディスプレイ(Xvfb)とGPU連携 (`cluster_manager.py`)**
   物理ディスプレイが接続されていないリモートPC (22, 23号機) では、画面を描画できないためにRVizが無限クラッシュしたり、AWSIMのLiDAR点群が生成されなくなる問題が発生します。これを以下の3つの連携で解決しています。
   - **Xvfbの自動起動**: まずコンテナ内で `Xvfb :99` を立ち上げ、その上で `DISPLAY=:99` を指定してGUIアプリケーションの描画先を仮想モニターへ向けます。`DISPLAY=:99` は描画先の指定だけであり、Xvfb 本体を起動するわけではありません。
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

可視化系の正本は現在 `tools/plot/*` です。
root 直下の `visualize_*.py` は legacy wrapper として残してあり、内部では `tools/plot/*` を呼びます。

```bash
# 最新の実験結果を可視化する場合
python3 tools/plot/visualize_traces.py ~/simulation_traces

# 過去に退避させた特定のデータを可視化する場合
python3 tools/plot/visualize_traces.py ~/simulation_traces_shared_20260512_144346

# MIN_TTCの連続値に基づく深刻度の3D可視化 (Maudeのフラグではなく抽出器の数値を優先)
python3 tools/plot/visualize_min_ttc_3d.py ~/simulation_traces

# リスク評価マトリックスの3D可視化
python3 tools/plot/visualize_risk_matrix.py ~/simulation_traces

# 衝突セル/非衝突セルをボクセル領域として可視化
python3 tools/plot/visualize_collision_regions.py ~/simulation_traces

# 実データの衝突外縁とAI学習境界を滑らかな面として可視化
python3 tools/plot/visualize_collision_surfaces.py ~/simulation_traces
```

# 対象のフォルダ（ディレクトリ）を指定する場合
python3 compare_ttc_modes.py --dir ~/simulation_traces_shared_20260512_144346

# 出力されるCSVファイルの名前を指定する場合
python3 compare_ttc_modes.py --output custom_comparison_result.csv

# 両方を指定する場合
python3 compare_ttc_modes.py --dir ~/my_test_data --output my_test_diff.csv

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
# `docker exec` の中でも `bash -i -c` を徹底する
docker exec -it sim_worker_21 bash -i -c 'cd /home/passd/awsim_labs && ./awsim_labs.x86_64 -noise false'

# Autoware も同様に対話型シェルで起動する
docker exec -it sim_worker_21 bash -i -c 'source /home/passd/autoware/install/setup.bash && cd /home/passd/autoware && ros2 launch autoware_launch e2e_simulator.launch.xml vehicle_model:=awsim_labs_vehicle sensor_model:=awsim_labs_sensor_kit map_path:=/home/passd/autoware_map/nishishinjuku_autoware_map launch_vehicle_interface:=true'
```

`bash -c` や `bash -lc` で十分そうに見えても、コンテナ運用では `bash -i -c` を正本にしてください。

## 今後の拡張性
新しくワーカーPC（例：24号機）を追加したい場合は、`redis_cluster/cluster_config.py` に新しいIPアドレスやコンテナ名、`ROS_DOMAIN_ID` を追記するだけで、システムが全自動でコンテナを構築し、クラスターの計算力（スループット）を向上させます。

<!-- 最新のシミュレーションデータで各モデルのTTCを計算し、差分をCSVに出力する場合
python3 compare_ttc_modes.py

# 対象のフォルダ（ディレクトリ）を指定する場合
python3 compare_ttc_modes.py --dir ~/simulation_traces_shared_20260512_144346

# 出力されるCSVファイルの名前を指定する場合
python3 compare_ttc_modes.py --output custom_comparison_result.csv

# 両方を指定する場合
python3 compare_ttc_modes.py --dir ~/my_test_data --output my_test_diff.csv
-->



1. ソフトウェア拡張のための3大設計思想
① OCP（Open-Closed Principle：開放閉鎖の原則）
「いろんな用途に合わせる」ための最も重要な原則です。ソフトウェアの構成要素は「拡張に対して開いており（Open）、修正に対して閉じている（Closed）べきである」という思想です。
新しい用途（機能）を追加する際、既存のコアコードを「書き換える（修正する）」のではなく、新しいコードを「追加する（拡張する）」だけで済むように設計します。外部評価ツールを安全に相乗りさせるためのアダプター層の導入は、まさにこのOCPの完璧な実践例です。

② Microkernel Architecture（プラグイン・アーキテクチャ）
OS（オペレーティングシステム）の設計から派生した思想で、システムを「最小限の機能を持つコア（Microkernel）」と「特定の用途向けの拡張モジュール（Plugin）」に明確に分離します。
VS CodeやEclipseなどのエディタが、あらゆるプログラミング言語（用途）に対応できるのはこの思想で作られているためです。タスクのキュー管理やインフラの起動といった「コア」だけを強固に作り、検証シナリオや評価ツールを「プラグイン」として外付けする設計がこれに当たります。

③ DIP（Dependency Inversion Principle：依存性逆転の原則）
「上位のロジック（抽象的なワークフロー）は、下位の詳細（具体的なツールやOSのコマンド）に依存してはならない。両者は『抽象（インターフェース）』に依存すべきである」という思想です。
現場監督（メインスクリプト）が直接Linuxコマンドを叩くのではなく、「プロセスを管理する専門家」というインターフェースを介して操作するようにしたことで、この原則が満たされています。
