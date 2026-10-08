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
- **連続ダイナミクス対象 (`--target dynamics`)**: AWSIMと同じUターン条件（`dx0`・ego速度・NPC速度）をODE/SDEで高速に計算し、既存の統計モード（二項CI・SPRT・DKW・EBStop）で評価します。AWSIMとの比較用に、AWSIMの代わりに使う「判定モード」と、AWSIMで再検証する候補を拾う「スクリーニングモード」を用意しています（[詳細](#連続ダイナミクス-odesde-対象)）。
- **FT4D ベースの司令塔補助**: `strategist.py` は FT4D 結果を読み、信頼度不足ノードの列挙だけでなく、`node_id` 単位の集約、不足理由分類、推奨アクション生成まで行えます。

## ファイル・ディレクトリ構成

現在の正本は v2 フレーム (`run_*_v2.py` → `apps/cli/` → `orchestration/` / `runtime/` / `targets/`) です。
root 直下の旧スクリプト (`master_orchestrator.py`、`run_manager.py` など) は互換用に残しているもので、
新しい機能は v2 側にだけ追加しています。

```text
AWSIM_launch/
├── run_orchestrator_cluster_v2.py # 【司令塔・クラスター】21号機で実行。Ray head・worker container の起動から停止判断まで行う正本の入口。
├── run_orchestrator_v2.py         # 【司令塔・ローカル】クラスターを使わず1プロセスで回す入口 (dynamics の統計実行など)。
├── run_worker_v2.py               # 【ワーカー】各 container 内で動き、Ray Client で司令塔のキューからケースを取って実行する。
├── run_dynamics_awsim_compare_v2.py # 同じ入力を ODE と AWSIM の両方で実行して比べる入口。
├── run_ft4d_smoke.py / run_prism_ft4d_smoke.py # FT4D の疎通確認 (AWSIM / PRISM)。
├── run_bbsl_local_ft4d.py         # BBSL raw result を AWSIM_launch 側で FT4D 再計算する入口。
├── run_external_verifier.py       # 外部検証器の legacy 互換入口。
├── apps/cli/                      # 各 run_*.py の本体 (引数解析と組み立て)。
├── contracts/                     # target 間で共通のデータ型 (TestCase, RawRunResult, EvaluationRecord, StatisticalReport など)。
├── orchestration/                 # target に依存しない制御ロジック
│   ├── orchestrator.py            # 司令塔本体。戦略に従ってケースを発行し、停止条件を判定する。
│   ├── strategy.py                # 探索戦略 (explore / focus / boundary_gap など、GP 境界モデル)。
│   ├── binomial_mode.py / dkw_mode.py / sprt_mode.py / ebstop_mode.py # 統計モードごとの停止判定。
│   ├── replay.py                  # 既存 CSV のケースを再実行する replay モード。
│   ├── random_parameter_sampling.py / fixed_parameter_sampling.py # 入力サンプリング。
│   └── worker_loop.py             # ワーカー側のループ (取得 → 実行 → 解釈 → 保存 → 完了報告)。
├── evaluation/                    # 統計計算 (binomial_ci, dkw, sprt, ebstop, alpha_spending, gp_boundary) と FT4D サービス。
├── runtime/
│   ├── cluster/                   # Ray 接続、TaskQueue / SharedStore actor、各号機の container 起動、worker watchdog、結果 sink。
│   ├── container/                 # container 内のプロセス管理
│   │   ├── profiles/              # container_profile 定義 (autoware171 / 180* / 190* / prism_maude など)。
│   │   ├── supervised_process/    # PID 1 の process supervisor。
│   │   └── launcher.py, supervisor.py, xvfb.py, gpu_health.py など
│   └── repository/                # dataset CSV、パラメータバッファ、SharedStore、履歴・再開用データ。
├── targets/                       # 検証対象ごとの実装
│   ├── registry.py                # --target から backend / result_interpreter を組み立てる。
│   ├── awsim/                     # AWSIM: case_kinds/, scenario_builders/, scenario_runner.py, backend.py, result_interpreter.py, theory_specs/
│   ├── prism/                     # PRISM: モデル実行、-simpath 解析、Maude 判定との統合
│   ├── dynamics/                  # ODE/SDE: models/, runners/, calibrations/
│   └── bbsl/                      # BBSL raw result の解釈と FT4D 入力化
├── verifiers/                     # Maude 実行境界と判定 (verifiers/maude/)、BBSL legacy 互換 adapter。
├── verification_core/ft4d/        # AWSIM_launch 内に保持する共通 FT4D コア。
├── scenario_specs/                # dynamics 用の入力範囲定義 (uturn.py)。
├── configs/                       # 旧互換のシナリオ設定入口。実体は targets/awsim/case_kinds/ を再 export する。
├── redis_cluster/cluster_config.py # 号機の IP・ユーザー・container 名・ROS_DOMAIN_ID (v2 の cluster_manager もここを読む)。
├── docker/                        # 各 Autoware / PRISM image の Dockerfile、cyclonedds 設定、image ごとの README。
├── tools/
│   ├── analysis/                  # 集計・校正・検証用 CLI (boundary_gap, version 比較, dynamics 校正・照合, FT4D AND-rule 検証など)。
│   ├── maintenance/               # 運用補助 (clean_trash, filter_success_csv, reconcile_late_traces)。
│   └── plot/                      # 可視化の本体 CLI。root 直下の visualize_*.py はここを呼ぶ wrapper。
├── tests/                         # unit / smoke テスト。
└── (旧フレーム・互換用)
    ├── master_orchestrator.py, run_manager.py, local_worker.py, run_scenario.py
    ├── strategist.py, estimator.py, awchecker.py, param_logger.py, theoretical_calculator.py, point_extractors.py
    ├── redis_cluster/{cluster_manager,process_controller,task_queue,shared_store}.py
    ├── fix_dataset_labels.py, dataset_repo.py, archive_results.py, stop_containers.py, compare_ttc_modes.py
    └── visualize_*.py, extract_region_data.py, analyze_*.py # tools/ への薄い wrapper
```

## 前提環境 (Dependencies)
本システムは以下の外部ツールと連携して動作します。パスや環境構築が完了していることを確認してください。
- **AWSIM Labs**: `~/awsim_labs`
- **Autoware**: `~/autoware`
- **AW-Runtime-Monitor**: `~/AW-Runtime-Monitor`
- **AW-CheckerPy (Maude)**: `~/aw-cheaker/Maude-3.5.1/AW-CheckerPy`
- **Python パッケージ**: `numpy`, `pandas`, `scipy`, `scikit-learn`

## Autoware 1.8.0 の既知事項

### カメラ設定

Autoware 1.8.0 で `perception_mode:=camera_lidar_fusion` と
`enable_2d_detection:=true` を有効にする場合、AWSIM の camera topic、`camera_info`、
sensor model、物体検出 launch を 1.8.0 向けに揃える必要がある。
camera が未接続でも AWSIM と Autoware の DDS 接続や局所化の成否とは別問題として扱う。
カメラ設定の不備は知覚結果に影響するが、以下の EKF/MRM 問題の直接原因としては扱わない。

### EKF 診断と MRM

> **重要:** AWSIM 運用版の `autoware_internal:1.8.0-ekfdiagfix` は、MRM 本体を
> 変更した image ではない。公式 Autoware 1.8.0 では、AWSIM の入力周期との組み合わせで
> EKF が正常な空 queue 周期にも `delay` WARN を publish する場合があり、その診断を受けて
> MRM が誤って `EMERGENCY_STOP` へ入る問題があった。そのため、修正版では
> EKF 診断初期値だけを変更している。

公式 Autoware 1.8.0 の `autoware_ekf_localizer` は、診断情報を毎周期初期化して
定期 publish する更新を含む。AWSIM では EKF が 50 Hz で動く一方、NDT pose は約 10 Hz、
gyro twist は約 20 Hz である。そのため正常な動作中でも、EKF の一部の周期では新しい
pose または twist が queue に存在しない。

1.7.1 はこの通常の空白周期を正常として扱った。1.8.0 は空白周期を `delay` WARN として
publish する場合があり、system diagnostic graph が自律走行不可と判断すると
`mrm_handler` が `EMERGENCY_STOP` を操作する。これは AWSIM の入力周期と 1.8.0 の診断設計の
組み合わせで起きる問題であり、AWSIM/Autoware の DDS 設定漏れとは区別する。

MRM 自体や Mahalanobis 閾値を無効化して回避してはならない。正常運用向けに修正する場合は、
通常の空白周期を WARN にしない EKF 診断の最小 overlay を用いる。実際に到着した measurement の
遅延、Mahalanobis 超過、または `no_update_count` による異常検知は残す。

公式 `1.8.0` image はバージョン比較用として変更せず保持する。修正版を使う場合は
`1.8.0 + EKF diagnostic overlay` と明示した別 image を作成し、公式版との比較対象を混同しない。

## Autoware 1.9.0 の既知事項

1.9.0 の `initialize_diagnostic_info()` は 1.8.0 と同一で、`2026-10-02` の21号機での確認でも
同じ誤 `EMERGENCY_STOP` が再現した。そのため実験には 1.8.0 と同じ修正を当てた
`autoware190_ekfdiagfix` を使う。無改変の公式 image は `autoware_internal:official-1.9.0` として保持する。
確認結果と image 構成は [docker/README_autoware_1.9.0.md](docker/README_autoware_1.9.0.md) を参照。

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
```

**`wilson` と `clopper-pearson` の使い分けについて:** `wilson` は中心極限定理(CLT)による近似に基づく手法で、実際のカバレッジ(信頼区間が真の値を含む頻度)が名目上の信頼水準(例: 95%)を下回ることがあります。特に真の確率が0または1に近く、サンプル数がまだ少ない段階(探索初期など)でこのズレが大きくなります(例: n=20, 真のp=0.05 のとき、名目95%のWilson区間の実際のカバレッジは約92.5%まで低下することを検証済み)。`clopper-pearson` は二項分布の厳密な計算に基づき、どんな真の確率・サンプル数でも名目の信頼水準を下回らないことが数学的に保証されています(その代わり区間はやや保守的=広めになります)。
**衝突確率のような安全性クリティカルな判断には `clopper-pearson` を使ってください。** `wilson` はデフォルトのままですが、それは既存ワークフローとの互換性のためであり、安全性の最終判断への使用を推奨する意味ではありません。

```bash

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

同じ確認を PRISM 側でも行えます。

```bash
python3 run_prism_ft4d_smoke.py
```

これは `tests/fixtures/prism/prism_stage2_baseline_sample41.jsonl`
(`2026-09-15` の実クラスタ検証 baseline 条件から抜粋した実データ、exact 1件 + sample 40件)を
`targets/prism/verification_input.py::build_verification_input_from_records -> evaluation/ft4d_service.py`
へ通し、`verification_core/ft4d/config/tree_prism_demo.json` を用いて FT4D を計算します。
複数の PRISM サンプル経路をまとめて1つの母集団として集約し、
`FAILURE` / `EARLY_FAILURE` / `REPEATED_DEGRADATION` それぞれの違反率と、
それらを OR で束ねた `top_sigma_pe` を表示します。
`--records-jsonl` で任意の PRISM 実験 JSONL(`run_orchestrator_cluster_v2.py --target prism` の
`--output`成果物など)を指定すれば、その実験データに対して同じ集約を実行できます。

### 5. チェッカープロセスの起動 (別ターミナル)
生成されたシミュレーションデータ (JSON)を手動で安全性を判定するために、ターミナルでチェッカーを使ってください。

```bash
python3 awchecker.py --type uturn
```

## 出力データ (Traces)
テストの実行結果とログは `~/simulation_traces` ディレクトリに出力されます。
- `{scenario}_dataset.csv`: 共有金庫によって結合された、AIの学習に直結する完全なデータセット (パラメータ + 評価結果 + 理由)。
  - ※ **最小接近距離 (`min_distance`)**: 事実として車同士が何メートルまで接近したかの最短距離。TTCの予測誤差を排除した物理的なニアミス指標として記録されます。
  - ※ JAMA物理モデルに基づく理論値（`theory_margin_*`, `theory_zone_*` 等）も記録され、シミュレータの実挙動と物理限界の乖離分析に活用できます。アプローチA（壁想定）とアプローチB（NPC前進考慮）の両方が保存されます。
- `checker_errors_detail.log`: 解析ツールで異常が発生した際の詳細なエラーログ (STDOUT/STDERR)。
- `{scenario}_eval_sim{N}.json`: global loop番号で確定したRuntime Monitorの詳細トレースデータ。
- `{scenario}_test_sim{N}.json`: Runtime Monitorがworker内連番で一時保存する変換前データ。通常は`eval`名へ移動されます。
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

### `scenario_profile` の運用方針 (`2026-08-31` 時点)

- scenario 実装の正本はすべて新フレームです。
- `uturn` / `cutin` / `swerve` / `cutout` / `deceleration` は、現在の `targets/awsim/case_kinds/*` と `targets/awsim/scenario_builders/*` を正本として運用します。
- `legacy` という名前の入口はまだ残っていますが、これは互換のための alias です。実体として旧フレームの別挙動を維持しているわけではありません。
- したがって、`scenario_profile=legacy` を指定しても、現行コードでは新フレームの scenario 定義と builder に流れます。
- 今後 profile を増やす場合は、「互換名だけ残す」のか「本当に別挙動を持つ」のかを先に決め、README と test を同時に更新してください。

### `container_profile` を切り替えるときの扱い (`2026-08-31` 時点)

- 現在サポートしている `container_profile` は `legacy`、`autoware171`、`autoware180`、`autoware180_ekfdiagfix`、`autoware190`、`autoware190_ekfdiagfix` です。
- `legacy` は旧来寄りのコンテナ起動条件で、`image=autoware_internal:2026`、`network_mode=host`、`privileged=true` を使います。
- `autoware171` は `1.7.1` 系の現在の運用条件で、`image=autoware_internal:2026-1.7.1-x11-verified-20260808`、`network_mode=bridge`、`privileged=false` を使います。
- `autoware180` は公式 `1.8.0` 比較用、`autoware180_ekfdiagfix` は AWSIM 運用向けの EKF 診断 overlay 版です。どちらも `autoware180_runtime/maps` と `autoware180_runtime/ml_models` を mount します。
- `autoware190` は公式 `1.9.0` image に AWSIM 互換層だけを重ねた比較用、`autoware190_ekfdiagfix` は 1.8.0 と同じ EKF 診断 overlay を当てた実験用です。network / privileged / user / mount は `autoware180_ekfdiagfix` と同一で、`autoware190_runtime/maps` と `autoware190_runtime/ml_models` を mount します。公式 1.9.0 image の既定 `CYCLONEDDS_URI` は multicast を許可する `/home/aw/cyclonedds.xml` なので、profile env で `/home/passd/cyclonedds.xml` を明示しています。詳細は [docker/README_autoware_1.9.0.md](docker/README_autoware_1.9.0.md) を参照してください。
- `autoware180` / `autoware190` 系 image の `/docker-entrypoint.sh` は root 権限で初期化した後、`gosu passd` で実プロセスを起動します。そのため、この2 profileだけ Docker の開始userを`root`に固定します。`docker run --user passd`を指定するとentrypoint内のuser切替が`operation not permitted`で失敗します。非`privileged`運用で表示されるloopback multicastやsysctlのWARNは非致命であり、このuser切替エラーとは別です。
- `autoware180` 系workerのRay Client、checker、統計処理は、全号機で固定した`awsim_python_deps/py310`を`/opt/awsim_python_deps/py310`へread-only mountして使用します。image内のuser-local packageだけに依存するとentrypointの`gosu passd`後に`grpc`が見えず、workerが`ray_control_plane_lost`で終了するためです。
- `autoware171` / `autoware180*` では `cyclonedds.xml`、`AWSIMScriptPy`、`autoware_map`、`AW-Runtime-Monitor` などの mount を profile 側でまとめて管理しています。`docker run` を都度手で増減させるのではなく、まず profile 定義を直してください。
- CLI では `--container-profile` だけを指定した場合、`--scenario-profile` を省略すると同じ名前が自動で入ります。
- ただし現行の scenario 実装は新フレーム 1 系統なので、`scenario_profile=legacy` になっても scenario の中身は旧実装へ戻りません。変わるのは主にコンテナ起動条件です。
- つまり「コンテナだけ切り替えたい」場合でも、まず `container_profile` を基準に考えてください。scenario の違いより、`docker run` の network / privileged / env / mounts の違いのほうが実運用では重要です。
- `container_profile` は image / network / privileged / mount / Docker env を決める入口です。画面あり・headless の最終切替は `--headless` で行います。
- `--headless` を付けた場合、Xvfb の仮想画面を使い、Autoware launch には `rviz:=false` を自動で追加します。`DISPLAY=:99` だけでは RViz は止まりません。
- 画面ありで目視確認したい場合は `--headless` を付けず、X11 表示用の手動コマンドまたは画面ありコンテナ手順を使ってください。
- `bridge` profile で cluster worker を起動する場合、コンテナ内では Ray worker node を起動しません。`run_worker_v2.py` は Ray の外部 driver として、21号機の `TaskQueueActor` / `SharedStoreActor` に接続します。Docker bridge 内部IPやホスト側物理IPを、コンテナ内 Ray node として登録しないでください。
- `bridge` profile では Ray Client 経由で detached actor を呼びます。この経路では actor method の位置引数 signature が崩れることがあるため、`TaskQueueActor` / `SharedStoreActor` への複数引数呼び出しは keyword 引数で行います。`too many positional arguments` が出た場合はこの層を確認してください。
- `bridge` profile の cluster worker は、Ray Client 接続をデフォルトで 6 回、15 秒間隔で retry します。simulation 中は 60 秒ごとに `running` heartbeat を更新し、長い case 実行中に stale と誤判定されるのを避けます。
- Ray/GCS 接続が途中で切れた場合、worker は `ray_control_plane_lost` として停止し、その case の結果を無理に保存し続けません。司令塔側も Python 例外として検知できる範囲では `run_failed_ray_gcs_lost` を summary に残して停止します。
- 登録前に `Exited(1)` した worker container を自動再起動したい場合だけ、`--auto-restart-missing-workers` を明示してください。再起動は node ごと最大 2 回、cooldown 600 秒、再起動前に `docker logs --tail 100` を summary の `maintenance_events` に残す方針です。
- 長期実験で稼働中 container から GPU / NVML が見えなくなる問題へ自動対応する場合は、`--auto-restart-gpu-workers` を明示してください。worker は各ケースを queue から取る前に `nvidia-smi` を確認し、異常時は `gpu_unavailable` を通知して終了するため、新しいケースを消費しません。
- GPU watchdog は数ループごとに各稼働中 container の `nvidia-smi` も確認します。実行中ケースを強制終了するとqueue上の未完了taskを失うため、`running`中は隔離待ちにし、ケース終了後の取得前checkで停止させます。その後、ログ末尾を `maintenance_events` に保存し、worker container を `docker rm -f` して同じ profile から再作成します。simulation trace はホストの `simulation_traces_sim_worker_*` を bind mount しているため、container 再作成では削除されません。
- 再作成後はホストと新しい container の両方で GPU を確認し、成功時だけ一時隔離を解除します。失敗時は既定で600秒待って再試行し、連続3回失敗または24時間内の再作成5回到達でその node を終端隔離します。他nodeの実験は継続します。
- GPU自動復旧を有効にする標準指定は `--auto-restart-gpu-workers --gpu-worker-max-restarts-per-24h 5 --gpu-worker-max-consecutive-failures 3 --stale-worker-restart-cooldown-sec 600` です。上限値を増やす前に、host側driverやNVIDIA Container Toolkitの恒常障害でないことを確認してください。
- コンテナを切り替えるときは、次の順でそろえるのを基本にしてください。
  - 既存コンテナを停止して消す
  - 使いたい `container_profile` を 1 つ決める
  - その profile で worker / orchestrator を起動する
  - mount 済みの `cyclonedds.xml`、`VK_ICD_FILENAMES`、`DISPLAY`、map path がその profile で成立しているかを確認する
- 画面表示や学校ネットワーク対策 (下記の storm-control 対策) を含む `1.7.1` 系運用は、原則として `autoware171` profile を正本にしてください。`legacy` は互換用に残っている profile です。

### 学内スイッチの storm-control 停止と bridge 化の理由 (`2026-09-28` 時点)

- `2026-09-26` に、学内スイッチ `hw57-is12` が研究室の LAN ポート 21 本を error-down にしました。スイッチログの理由は `Cause=storm-control` で、各ポートが link up してから約 8〜9 秒後にストームとみられる通信を検知して止めています。
- JAIST 情報基盤センター (CII) の対応記録は `Case#70493` です。`2026-09-28 08:45` ごろに CII 側で解除されるまで、該当ポートの号機はネットワークを使えませんでした。error-down は自動では戻らず、CII に解除を依頼する必要があります。
- 主因と推定しているのは、`legacy` profile (`network_mode=host`、`privileged=true`) で動かしていた ROS 2 / Cyclone DDS の通信です。host モードではコンテナが物理 NIC (`enp10s0`) を直接使うため、Autoware の数百ノード分の discovery マルチキャスト (`239.255.0.1`) やトピック通信が、複数号機から学内 LAN へ流れていたと考えています。
- `2026-08` に 24 号機で起きた `dhcp4 ... no lease` / `ip-config-unavailable` のネットワーク断 ([docs/manual_run_autoware_1_7_1_with_awsim_20260807.md](docs/manual_run_autoware_1_7_1_with_awsim_20260807.md)) も、同じ error-down だった可能性があります。
- 対策として、`autoware171` / `autoware180*` / `autoware190*` profile では、DDS の通信を物理 NIC へ出さないために次の 3 つを重ねています (`prism_maude` も bridge・非 privileged ですが、ROS を使わないため `cyclonedds.xml` は mount しません)。
  - `network_mode=bridge`、`privileged=false`: コンテナからは docker0 と `lo` しか見えず、docker0 上のマルチキャストは物理 NIC へ転送されません。
  - `cyclonedds.xml` の mount: `NetworkInterface=lo`、`AllowMulticast=false`、`Peer=127.0.0.1` で、DDS を loopback だけに閉じ込めます。
  - 手動実行時の `iptables -I DOCKER-USER 1 -i docker0 -o enp10s0 -j REJECT`: コンテナから学内 LAN への通信を止めます ([docs/node21_autoware_1_7_1_awsim_bridge_commands_20260830.md](docs/node21_autoware_1_7_1_awsim_bridge_commands_20260830.md))。
- 号機間の通信は、Ray Client (`ray://<master-ip>:10001`) の TCP ユニキャストだけにしています。そのため、コンテナを Ray node としてクラスタに参加させないでください。
- cluster worker を `network_mode=host` に戻したり、`cyclonedds.xml` を外したり、DDS のマルチキャストを有効にしたりしないでください。新しい profile を追加するときも、DDS の通信が物理 NIC へ出ないことを必ず確認してください。
- CII からは、スイッチやハブのループ接続がないかも確認するよう依頼されています。物理的なループが原因の場合は、コンテナ側の設定では防げません。研究室内のハブの配線も確認してください。

### Autoware 1.7.1 クラスター標準実行コマンド

次のコマンドを司令塔の21号機で実行します。これは `bridge`、headless、
`AWSIMScriptPy` 同期、stale/missing worker復旧、GPU異常時の隔離・再作成を含む
1.7.1長期実験用の正本です。

```bash
cd /home/passd/AWSIM_launch

python3 run_orchestrator_cluster_v2.py \
  --output /home/passd/simulation_traces/uturn_records.jsonl \
  --dataset-csv /home/passd/simulation_traces/uturn_dataset.csv \
  --case-kind uturn \
  --mode binomial_ci \
  --binomial-target c_collision \
  --binomial-method wilson \
  --binomial-confidence 0.95 \
  --binomial-target-width 0.02 \
  --container-profile autoware171 \
  --scenario-profile autoware171 \
  --headless \
  --sync-awsim-script-py \
  --auto-restart-stale-workers \
  --auto-restart-missing-workers \
  --auto-restart-gpu-workers \
  --worker-queue-connect-retries 6 \
  --worker-queue-connect-retry-interval-sec 15 \
  --stale-worker-restart-cooldown-sec 600 \
  --gpu-worker-max-restarts-per-24h 5 \
  --gpu-worker-max-consecutive-failures 3 \
  --gpu-worker-stable-reset-sec 1800 \
  --gpu-worker-probe-timeout-sec 5
```

`--auto-restart-gpu-workers` は起動済みorchestratorへ後から反映できません。
このオプションを付けずに開始した実験では、現在の実験を安全に停止してから上記コマンドで
再開してください。既存CSVと各ホストの `simulation_traces_sim_worker_*` は削除しません。

上記コマンドの `--binomial-method wilson` は、CLT近似に基づく標準的な手法であり、
真の衝突確率が0や1に近い・サンプル数がまだ少ない段階では、名目の信頼水準(95%)を
実際のカバレッジが下回ることがあります(検証例: n=20, 真のp=0.05 で実カバレッジ約92.5%)。
`c_collision` の最終的な安全性判断に使う結果は `--binomial-method clopper-pearson` で
再評価することを推奨します(区間はやや保守的になりますが、どんな真の値でも名目の信頼水準を
下回らないことが数学的に保証されています)。

**`binomial_ci`の"repeated peeking"問題と`--binomial-anytime-valid`**: `--mode binomial_ci`は
1標本増えるたびに同じ`--binomial-confidence`で区間を計算し直す設計のため、理論上は「何度も
覗き見ること」自体が実効的な誤り率を悪化させうるという弱点があります(DKWのステージ制
alpha-spendingや、SPRT・EBStopの本質的にvalidな構成とは異なり、binomial_ciにはこれに対する
保護が組み込まれていません)。`--binomial-anytime-valid`を付けると、固定confidenceの代わりに
EBStopと同じ`d_t = c/t^1.1`型のunion bound(`evaluation/alpha_spending.py`、Mnih, Szepesvári,
Audibert 2008スタイルのpeeling schedule)で毎回の信頼水準を計算し直し、「何度チェックしても
全体の誤り率が`1-confidence`を超えない」ことを数学的に保証します。

ただし**このコストは大きく無視できません**: 同じtarget-widthに到達するのに必要なサンプル数は、
実測でおよそ**5〜8倍**に増えます(例: p=0.1、target-width=0.02、confidence=95%の場合、通常は
約3,600サンプルで収束するところ、`--binomial-anytime-valid`では約28,000サンプル必要)。
spending exponentの調整やチェック頻度を落とす等の工夫を試しても、この倍率は大きくは改善しません
(anytime-valid confidence sequence全般に共通する本質的なコストです)。そのため既定では無効
(オプトイン)にしており、AWSIM/PRISMのシミュレーション1回のコストを度外視できる、安全性の
理論的保証を最優先したい実験でのみ使うことを想定しています。

### 1.7.1 bridge worker の Python 依存 bundle

`autoware171` の verified image は Autoware / AWSIM の運用状態を固定するための image ですが、現在の `AWSIM_launch` worker は起動時に `ray` と `scikit-learn` を import します。そのため、`binomial_ci` や `dkw` では `sklearn` が無いと worker がキュー取得前に落ちます。

この依存はホストの `~/.local/lib/python3.10/site-packages` を丸ごと mount して解決しないでください。実測では host 側の `numpy==1.26.4` と container 側の `scipy==1.8.0` が混ざり、`SciPy requires NumPy <1.25.0` の警告が出ます。短期的に import は通っても、検証運用の再現性が落ちます。

`1.7.1` bridge worker では、各ホストに専用の Python deps bundle を置き、それだけを read-only mount する方針にします。

```text
host:      ${HOME}/awsim_python_deps/py310
container: /opt/awsim_python_deps/py310
env:       PYTHONPATH=/opt/awsim_python_deps/py310
```

bundle に入れる依存は、少なくとも次を固定します。

```text
ray[client]==2.55.0
scikit-learn==1.7.2
numpy==1.24.4
joblib
threadpoolctl
```

`ray` / `scikit-learn` を毎回 `pip install` する方式は、起動時間が長くなり、ネットワーク状態にも依存します。最終的にはこの bundle を image に焼き込んだ snapshot を作るのが本命ですが、まずは `autoware171` profile の mount/env でこの bundle を読む形を正本にします。

`2026-09-02` 時点で、21/22/23/24号機にこの bundle を配置済みです。

```text
21号機: /home/passd/awsim_python_deps/py310
22号機: /home/tomita1/awsim_python_deps/py310
23号機: /home/tomita2/awsim_python_deps/py310
24号機: /home/tomita4/awsim_python_deps/py310
```

22/23/24号機では、`autoware_internal:2026-1.7.1-x11-verified-20260808` に mount した状態で `ray[client]==2.55.0`、`grpcio`、`sklearn==1.7.2`、`numpy==1.24.4`、container 標準の `scipy==1.8.0`、`StandardScaler` の import が通ることを確認済みです。bridge worker は `ray://<master-ip>:10001` で Ray Client 接続します。

### Maude checker の Python 環境

`AWSIM_launch` worker の Python 依存 bundle と、AW-CheckerPy の `maude` Python module は別物です。
`maude` は次の venv に入っているため、検証器は通常の `python3` ではなくこの Python を優先して使います。

```text
/home/passd/aw-cheaker/Maude-3.5.1/AW-CheckerPy/.venv/bin/python
```

`ModuleNotFoundError: No module named 'maude'` が出た場合は、`aw-cheaker` mount の中に
`AW-CheckerPy/.venv/lib/python3.10/site-packages/maude` が存在するかを確認してください。
`PYTHONPATH=/opt/awsim_python_deps/py310` だけを増やしても Maude は見えません。

### クラスター各ノードの Trash 掃除

Ray の `/tmp/ray ... is over 95% full` 警告が出た場合でも、実際には `/tmp/ray` ではなく
各ホストの root filesystem が詰まっていることがあります。まず `~/.local/share/Trash` を確認してください。

Trash 掃除は実験データ、Docker image、map、model には触れず、次の tool に固定します。
実行場所は司令塔の 21号機です。21号機から `redis_cluster/cluster_config.py` の
`enabled=True` ノードへ SSH し、各ノードの Trash だけを処理します。

```bash
cd /home/passd/AWSIM_launch

# 確認だけ。削除はしない。
python3 tools/maintenance/clean_trash.py

# 21/22/23/24 など enabled=True のノードで Trash を空にする。
python3 tools/maintenance/clean_trash.py --apply
```

デフォルトは `redis_cluster/cluster_config.py` で `enabled=True` のノードだけです。特定ノードだけ見る場合は次のように指定します。

```bash
# 22/23 だけ確認する。
python3 tools/maintenance/clean_trash.py --nodes 22,23

# cluster_config に登録されている全ノードで Trash を空にする。
python3 tools/maintenance/clean_trash.py --nodes all --apply
```

削除対象は各ユーザーの `~/.local/share/Trash/files/*` と `~/.local/share/Trash/info/*` のみです。
`simulation_traces*`、Docker image/container、`autoware_map`、`autoware180_runtime`、
`AWSIMScriptPy`、`AWSIM_launch` は削除対象にしません。

実験中に `/tmp/ray ... over 95% full` が出た場合は、まず削除なしで確認します。

```bash
python3 tools/maintenance/clean_trash.py
```

Trash が大きく、root filesystem の空きが危険な場合だけ `--apply` を付けます。
この tool は Ray や worker container を停止しません。

### AWSIM trace timeout と late JSON の扱い

クラスタ実行では、司令塔が割り当てるglobal loop番号と、各workerが実行する順番は一致しません。
Runtime Monitorにはworker内のローカル連番 (`1, 2, 3, ...`) を渡し、backendは
`{scenario}_test_sim{local}.json`を待ちます。取得後にJSON、動画、動画メタデータを
`{scenario}_eval_sim{global}.json`へ移動します。Runtime Monitorを再利用してもglobal番号を
直接渡してはいけません。

AWSIM は scenario 終了処理の最後に trace JSON を書くため、監視時刻ちょうどに
`uturn_test_sim<loop>.json` が出ていない場合でも、数秒後に JSON が届くことがあります。
`uturn` は旧 worker と同じく outer watcher で `300秒` 待ち、scenario 内部には別の
goal timeout を入れません。このため AWSIM backend は通常の scenario timeout の後、`10秒` の artifact grace を取ります。
grace 内に JSON を見つけた場合は `eval` 側へ昇格して解析し、`TIMEOUT` marker は書きません。

JSON がある試行は、scenario process の終了コードではなく checker の検証結果で最終判定します。
raw process の状態は `meta.raw_run_status`、timeout の内訳は `meta.raw_timeout_reason` に保存し、
検証結果を上書きしません。

| trace / checker の状態 | 最終 `status` | 衝突率への採用 |
| --- | --- | --- |
| JSON あり、checker が全式を評価し、EGO/NPC ともに移動 | `success` | 採用 (`c_collision` が `0` / `1`) |
| JSON あり、EGO または NPC が動いていない | `analysis_error` | 除外 (`c_collision=-1`) |
| JSON あり、formula 欠落・Maude 失敗 | `analysis_error` | 除外 |
| JSON なし、10秒 grace 後も未到着 | `timeout` | 除外 (`meta.timeout_reason=artifact_timeout`) |

連続値の `min_ttc`、`min_distance`、`min_ttb`、`z_margin` は、旧 `awchecker.py` と同じ
`AWKinematicsPipeline` を `targets/awsim/kinematics_bridge.py` 経由で呼んで trace JSON から算出します
(NPCは case kind の `TARGET_NPCS`、方式は `--ext_mode`)。抽出に失敗しても Maude の判定は残し、
値を空欄にして `meta.kinematics_error` に理由を残します。Maude 解析エラー時は4値とも `-1` です。
`2026-10-05` より前の v2 実行 (1.7.1 / 1.8.0 / 1.9.0 比較実験を含む) ではこの抽出が呼ばれておらず、
dataset CSV のこれらの列は空です。`--mode dkw` / `dkw_fixed` / `ebstop` / `worst_ttc` / `ttc_edge` は
これらの列を使うため、過去データで使う場合は trace JSON から再抽出してください。

trace JSON の必須項目 (`planning_trajectory`、`control_cmds` など) が空の場合は、判定は続けたうえで
`meta.empty_trace_keys` (dataset CSV では `meta_empty_trace_keys`) に項目名を残します。Runtime Monitor は
存在しない topic を購読してもエラーにならず空配列を書くため、Autoware の更新で topic 名が変わったときは
この列で検出してください。実例として、Autoware 1.9.0 では `/planning/scenario_planning/trajectory` の中継が
削除され、`2026-10-05` 以前の 1.9.0 trace の `planning_trajectory` は空です。Runtime Monitor は現在
`/planning/trajectory` を記録しており、1.7.1 / 1.8.0 でも同じ内容が記録されることを実機で確認しています。

たとえば scenario 側が exit code `124` を返しても、その後に回収した JSON が checker を通り、
EGO/NPC の移動と衝突判定が確認できれば `success` として採用します。逆に JSON があっても
車両が動いていない試行は衝突なしには数えません。

`binomial_ci` の有効標本は `status=success` かつ対象値が `0` / `1` の行だけです。
ここで `status` はchecker解析後の最終判定を表し、scenario実行時の状態は
`meta.raw_run_status`（dataset CSVでは `meta_raw_run_status`）へ分離して保存します。
したがって `meta.raw_run_status=timeout` であっても、trace JSONを回収してcheckerの解析に
成功した試行は最終的に `status=success` となり、統計検証に使用します。最終 `status=timeout` は、
有効なtraceまたは判定結果を取得できなかった試行として統計検証から除外します。

既存CSVを変更せず、timeout marker と同じ worker の local trace を再照合するには次を使います。
実行場所は21号機です。

```bash
cd /home/passd/AWSIM_launch

# 読み取りだけ。CSV、trace、container、Ray 状態は変更しない。
python3 tools/maintenance/reconcile_late_traces.py \
  --dataset-csv /home/passd/simulation_traces/uturn_dataset.csv \
  --case-kind uturn

# JSON report を別ファイルへ残す場合だけ明示する。
python3 tools/maintenance/reconcile_late_traces.py \
  --dataset-csv /home/passd/simulation_traces/uturn_dataset.csv \
  --case-kind uturn \
  --output /home/passd/simulation_traces/uturn_late_trace_reconciliation.json
```

このtimeout処理の変更はworker process起動時に読み込まれます。すでに実行中のworkerへは
遡及しないため、次のcluster runから有効です。

### success結果だけのCSVを作成する

元のdataset CSVを変更せず、`status`列が`success`の行だけを同じ列構成・行順で
別CSVへ保存する場合は次を実行します。出力先を省略すると、入力ファイルと同じ場所に
`<入力名>_success.csv`を作成します。

```bash
cd /home/passd/AWSIM_launch

python3 tools/maintenance/filter_success_csv.py \
  --dataset-csv /home/passd/simulation_traces/uturn_dataset.csv
```

既存のsuccess CSVを現在のdatasetから作り直す場合だけ`--overwrite`を付けます。

```bash
python3 tools/maintenance/filter_success_csv.py \
  --dataset-csv /home/passd/simulation_traces/uturn_dataset.csv \
  --output /home/passd/simulation_traces/uturn_dataset_success.csv \
  --overwrite
```

このツールは`timeout`、`execution_error`、`analysis_error`を除外します。
ただし、focusやerror recovery由来の成功行も保持します。`binomial_ci`が使用する
一様ランダム標本だけへ限定したCSVではありません。

### 1.7.1の二項標本を1.8.0で再実行する

`replay`モードは、入力CSVから次の条件をすべて満たす行だけを元の行順で再実行します。

- `status=success`
- `reason`に`BINOMIAL_CI:`を含む
- `c_collision`が`0`または`1`

`uturn_dataset_success.csv`の全`10,309件`ではなく、実際に1.7.1の二項信頼区間へ
採用された`9,066件`だけが比較対象です。件数が変わっていた場合は実験を開始しないよう、
`--replay-expected-count 9066`を指定します。

```bash
cd /home/passd/AWSIM_launch

python3 run_orchestrator_cluster_v2.py \
  --output /home/passd/simulation_traces_autoware180_replay_20260911/uturn_records.jsonl \
  --dataset-csv /home/passd/simulation_traces_autoware180_replay_20260911/uturn_dataset.csv \
  --case-kind uturn \
  --mode replay \
  --replay-csv /home/passd/simulation_traces_shared_20260911_125415/uturn_dataset_success.csv \
  --replay-expected-count 9066 \
  --run-id autoware180_replay_20260911 \
  --container-profile autoware180_ekfdiagfix \
  --scenario-profile autoware171 \
  --headless \
  --sync-autoware180-map \
  --auto-restart-stale-workers \
  --auto-restart-missing-workers \
  --auto-restart-gpu-workers
```

`--run-id`は各号機のtrace mount先を別ディレクトリに分離し、1.7.1の既存traceを
上書きしないために必須です。1.8.0の結果には`meta_replay_source_loop_num`、
`meta_replay_source_case_id`、`meta_replay_source_collision`、`meta_replay_source_reason`が入り、
元試行と一対一で比較できます。
再実行先で発生した`timeout`や`execution_error`もバージョン差なので削除しません。同じ出力CSVで
再開した場合は、すでに記録済みの`meta_replay_source_loop_num`を自動的に除外します。

### 1.7.1 / 1.8.0 の両方で有効だった標本を 1.9.0 で再実行する

1.9.0 では、1.7.1 の二項標本 `9,066件` のうち 1.8.0 replay でも `status=success` かつ
`c_collision` が `0` / `1` だった `8,914件` だけを再実行します。1.7.1 の無効試行
(`execution_error` / `timeout` / `analysis_error` の `8,110件`) と、1.8.0 側で無効になった `152件` は含めません。
入力CSVは元の`loop_num`を保持しているため、3バージョンを同じケース番号で対応付けられます。
抽出条件とSHA-256は同じディレクトリの `*_manifest.json` に残しています。

```bash
cd /home/passd/AWSIM_launch

python3 run_orchestrator_cluster_v2.py \
  --output /home/passd/simulation_traces_autoware190_replay_20261002/uturn_records.jsonl \
  --dataset-csv /home/passd/simulation_traces_autoware190_replay_20261002/uturn_dataset.csv \
  --case-kind uturn \
  --mode replay \
  --replay-csv /home/passd/simulation_traces_autoware190_replay_input/uturn_replay_input_valid_171_180.csv \
  --replay-expected-count 8914 \
  --run-id autoware190_replay_20261002 \
  --container-profile autoware190_ekfdiagfix \
  --scenario-profile autoware171 \
  --headless \
  --sync-autoware190-map \
  --auto-restart-stale-workers \
  --auto-restart-missing-workers \
  --auto-restart-gpu-workers
```

### バージョン差と確率的変動を反復検証する準備

反転したケースと固定seedの無作為対照群を選び、5反復ずつ実行するためのCSV、manifest、
手動コマンド一覧を一括生成します。生成コマンドは実験を開始しません。

```bash
python3 -m tools.analysis.prepare_version_repeat \
  --source-csv /home/passd/simulation_traces_shared_20260911_125415/uturn_dataset_success.csv \
  --replay-csv /home/passd/simulation_traces_autoware180_replay_20260911/uturn_dataset.csv \
  --output-dir /home/passd/simulation_traces_version_repeat_20260913 \
  --random-count 500 \
  --seed 171180
```

実験は出力された`manual_commands.md`の順番で1ブロックずつ手動実行します。各ブロックの
`run-id`と出力先は別になっているため、反復結果は上書きされません。中断したブロックだけは、
同じコマンドを再入力すると完了済みケースを除外して再開します。

### クラスター同期で追加更新するもの

クラスター起動時、リモートノードにはデフォルトで `~/AWSIM_launch` と
`~/AW-Runtime-Monitor` を同期します。前者は通常の worker / scenario / supervisor コード、
後者は trace JSON の保存処理に必要なため、両方を実験コードの正本として毎回揃えます。

`1.7.1` 系で `AWSIMScriptPy` 本体を更新したい場合だけ、次を追加します。

```bash
python3 run_orchestrator_cluster_v2.py ... \
  --container-profile autoware171 \
  --scenario-profile autoware171 \
  --headless \
  --sync-awsim-script-py \
  --auto-restart-gpu-workers
```

`AW-Runtime-Monitor` はデフォルトでディレクトリ全体を同期します。`main.py` だけを個別に
配布すると recorder 側と版がずれて起動時に失敗するため、部分同期は行いません。
`--sync-aw-runtime-monitor` は明示指定との互換のため残しますが、通常は入力不要です。

```bash
python3 run_orchestrator_cluster_v2.py ... \
  --sync-aw-runtime-monitor
```

この同期では `main.py`、`recorder/Recorder.py`、
`recorder/AWSIMClientOpStateTracker.py` の SHA-256 と構文を worker 起動前に検証します。
さらに container 内で lifecycle tracker を import できなければ worker は起動しません。

`1.8.0` 系で map を更新したい場合だけ、次を追加します。`1.9.0` 系では同じ用途で `--sync-autoware190-map` を使います。

```bash
python3 run_orchestrator_cluster_v2.py ... \
  --container-profile autoware180_ekfdiagfix \
  --scenario-profile autoware171 \
  --headless \
  --sync-autoware180-map
```

複数の追加更新が必要な作業では `--sync-awsim-script-py` と
`--sync-autoware180-map` を同時に指定できます。
デフォルト同期を止める特殊検証だけは `--no-sync-awsim-launch` または
`--no-sync-aw-runtime-monitor` を使います。通常の実験では指定しません。

cluster 起動前には各 image を使った `nvidia-smi` preflight も実行します。GPU を認識できない
ノードはタスクを取得させず、`cluster_preflight_failures` に理由を残して他ノードだけを起動します。
AWSIM、Autoware、Runtime Monitor が待機時間内に終了した場合も、trace timeout を待たず
`execution_error` とし、起動ログ末尾をエラー情報へ含めます。

### cluster worker の process supervisor

cluster container では `run_worker_v2.py` を PID 1 として直接起動しません。
PID 1 は `runtime.container.supervised_process.server` で、Ray Client / gRPC を import する前に
Unix socket `/tmp/awsim-process-supervisor.sock` を作り、その後 worker を子 process として起動します。

worker は socket 経由で supervisor に依頼し、次の process を起動・停止します。

- Xvfb
- AWSIM
- Autoware
- AW-Runtime-Monitor
- scenario client
- readiness probe
- AW Checker / Maude / BBSL の外部 command

これにより、gRPC thread が動作中の worker 自身から `fork()` して
`cygrpc` や `librclcpp` が不定期に segfault する経路を避けます。AWSIM、Autoware、scenario の
command、待機時間、parameter は変更しません。手動実行など
`AWSIM_PROCESS_SUPERVISOR_SOCKET` がない環境では、従来のローカル subprocess 経路を使います。

supervisor が終了した場合は管理下の process group を停止します。worker が終了した場合も
AWSIM/Autoware を残したまま container を存続させず、同じ container を再利用しません。

### 22/23/24号機への 1.8.0 運用入力配置 (`2026-09-01` 確認)

- `AWSIMScriptPy`、`autoware180_runtime/maps`、`autoware180_runtime/ml_models`、`cyclonedds.xml` は 22/23/24号機へ配置済みです。
- `cyclonedds.xml` は 3台とも `026239b408bfde21dfbc136b4ab84c12588c8571314e0f0c34d7826c5fd360be` で一致しています。
- `autoware180_runtime/maps` は 3台とも `223M`、`69` files で一致しています。
- `autoware180_runtime/ml_models` は 3台とも `3.7G`、`198` files で一致しています。
- `autoware_internal:1.8.0-ekfdiagfix` は `numpy==1.24.4`、`scikit-learn==1.7.2`、`ray==2.55.0`、`typing_extensions==4.15.0` を含む image として rebuild し、22/23/24号機へ再配布済みです。
- 依存確認では 22/23/24号機で `numpy=1.24.4 scipy=1.8.0 sklearn=1.7.2 ray=2.55.0` の import が通っています。

### 設計ルール

- `case_kind` にシミュレータ組み立て処理は入れないでください。
- `runner` に個別ロジックを溜め込まないでください。
- 速度帯で挙動が変わるなら `SCENARIO_PROFILES` を使ってください。
- 手動実行でも `bash -i -c` と `source /home/passd/autoware/install/setup.bash` を崩さないでください。
- 旧 `AWSIMScriptPy` 側の scenario 本体も最新化されていることを確認してください。
- 原則として修正は `AWSIM_launch` 側へ寄せますが、新フレームは最終的に旧 `AWSIMScriptPy/scenarios/...` の `make_*_scenario(...)` を呼ぶため、受け口の引数追加や実挙動修正が必要な場合は旧 scenario 本体も合わせて直してください。
- 特に `NPC が動かない`、`spawn はするが lane change しない`、`新フレームで計算した ratio が効かない` といった問題は、`case_kind` / `builder` ではなく旧 `AWSIMScriptPy` 側の条件式が原因のことがあります。この場合は新フレームだけ直しても不十分です。

### 新しいシナリオ設計時の注意と調整アルゴリズム

- `FIXED_PARAMS` に初期位置や加速度を一度置いて終わりにしないでください。
- 新しいシナリオでは、`ego_speed` / `npc_speed` の帯ごとに
  - `ego_init_offset`
  - `npc_init_offset`
  - `goal_offset`
  - `acceleration`
  が本当に十分かを確認してください。
- `uturn` / `cutin` / `swerve` は、現行の新フレーム側で `SCENARIO_PROFILES` を使い、速度帯ごとに開始位置や加速度、始動条件を切り替える設計です。
- `cutout` / `deceleration` は現状では新方式 1 系統で運用しています。こちらも必要なら profile 化できますが、`2026-08-31` 時点では別系統を持っていません。
- したがって、新しいシナリオを実験基盤へ正式に入れるときは、「profile を分ける必要が本当にあるか」を先に判断し、不要なら新フレーム 1 系統として整理したまま入れて構いません。
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
- 問題になりやすいのは「曲がる条件」ではなく、「NPC1 が前進を開始する条件」です。
- 現行の新フレーム側では `npc_start_speed_ratio` を `SCENARIO_PROFILES` に持たせ、`uturn` / `cutin` の `AWSIMScriptPy` 側へ渡します。現在は `av_speed >= npc_start_speed_ratio * _ego_speed` を使います。
- `2026-08-28` 時点の実運用値はおおむね `30/35/40 km/h -> 0.898 / 0.9083 / 0.916` です。つまり、ego が完全に目標速度へ届く前でも、NPC1 が少し早めに走り始められるようにしています。
- `legacy` profile を指定しても、この始動条件を旧式へ戻すことはありません。現行コードでは互換入口も同じ新方式へ流れます。
- 新しいシナリオでも、`NPC がときどき動かない` ときは `dx0` や lane change 条件だけを見るのではなく、まず `NPC1 の前進開始条件` が実速度に対して厳しすぎないかを確認してください。

### `swerve` で入れた補正の考え方

- `swerve` でも `2026-08-28` 時点で `npc_start_speed_ratio` を導入し、`av_speed >= npc_start_speed_ratio * _ego_speed` で `npc1` が前進を開始するようにしています。
- さらに `npc_init_offset` と `ego_goal_offset` は、式で必要距離を見積もった上で、旧アンカーケース `30/10`, `30/15`, `40/10`, `40/15` の安定値を下回らないように補正しています。
- この補正も現行の新フレーム実装に集約されています。`legacy` profile 名は残っていますが、別の `swerve` 挙動を維持しているわけではありません。
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
- これは「新フレームのためだけに旧コードを触った」のではなく、新フレームが最終的に旧 `dynamic_spawn.py` を実行するためです。`spawn_trigger_speed_ratio` や `decel_trigger_speed_ratio` を新フレーム側で計算しても、旧 scenario 関数がその引数を受け取らなければ runtime では反映されません。

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

現在のフレームワークは、21号機の司令塔と各号機の worker container を Ray で結んだ分散構成で動いています。
以下は v2 (`run_orchestrator_cluster_v2.py`) の流れです。

### 処理フローの概要

1.  **インフラ構築**: 司令塔 (`apps/cli/orchestrator_cluster_main.py`) が Ray head を起動し、
    `TaskQueueActor` (`runtime/cluster/ray_queue.py`) と `SharedStoreActor` (`runtime/repository/shared_store.py`) を detached actor として作ります。
    続いて `runtime/cluster/cluster_manager.py` が `redis_cluster/cluster_config.py` の各号機へ SSH し、
    コード同期・GPU preflight の後、`container_profile` に従って worker container を起動します。
2.  **タスク生成**: `orchestration/orchestrator.py` が、モードに応じた戦略 (`orchestration/strategy.py` の探索戦略、
    `binomial_mode.py` / `dkw_mode.py` / `sprt_mode.py` / `ebstop_mode.py`、`replay.py` など) から次のケースを決め、キューに積みます。
3.  **タスク実行**: 各 container では process supervisor が PID 1 で動き、その子 process の `run_worker_v2.py` が
    Ray Client (`ray://<master-ip>:10001`) でキューからケースを取ります。`orchestration/worker_loop.py` が
    `targets/registry.py` で選んだ target の backend (AWSIM なら `targets/awsim/backend.py`) を呼び、
    AWSIM・Autoware・Runtime Monitor・scenario を supervisor 経由で起動します。
4.  **結果解析**: trace JSON を target の `result_interpreter.py` が Maude checker と kinematics 抽出にかけ、
    共通形式の `EvaluationRecord` (`contracts/evaluation.py`) にします。
5.  **データ結合**: worker の `RaySharedStoreResultSink` (`runtime/cluster/result_sink.py`) が、実行前に入力パラメータを、
    実行後に結果を `SharedStoreActor` へ送ります。`SharedStore` は `loop_num` で両者を結合し、
    `--dataset-csv` (1行1標本) と `--output` (`EvaluationRecord` の JSONL) に追記します。
6.  **完了報告と次の判断**: worker が `TaskQueueActor` に完了を報告すると、司令塔は更新された dataset を読み直し、
    統計の停止条件の判定や次のケース生成を行います。停止条件を満たすと stop signal を出します。
    その後もキューに残ったケースが一定時間取られない場合は、放棄されたものとして取り消し、司令塔を終了します。

### 堅牢なエラー＆タイムアウト処理
分散システム特有の「フリーズ」や「通信エラー」から自己復旧し、統計や探索への悪影響を遮断する仕組みが備わっています。
- **タイムアウト**: trace JSON が scenario timeout と10秒の grace を過ぎても届かない場合は `status=timeout` とし、
  `SharedStore.flush_timeout` で入力パラメータと合わせて記録します (詳細は「AWSIM trace timeout と late JSON の扱い」)。
- **解析エラー**: JSON の破損・formula 欠落・車両が動いていない試行は `status=analysis_error` とし、判定値を `-1` にします。
- **ガベージコレクション**: container のクラッシュなどで結果が届かないパラメータは、
  `ParameterBuffer` から一定時間 (600秒) で自動破棄されます。
- **統計・探索への影響遮断**: 統計モードと探索戦略は `status=success` かつ判定値が有効な行だけを使うため、
  `timeout` / `execution_error` / `analysis_error` の行は標本に入りません。
- **worker の異常**: Ray 接続断は `ray_control_plane_lost`、GPU 異常は `gpu_unavailable` として worker を止め、
  司令塔側の watchdog が設定に応じて container を再作成します (「`container_profile` を切り替えるときの扱い」を参照)。

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

# 1.7.1の元ケースと1.8.0 replayをケース番号で対応付けて差分を可視化
python3 -m tools.plot.visualize_version_comparison \
  ~/simulation_traces_shared_20260911_125415/uturn_dataset_success.csv \
  ~/simulation_traces_autoware180_replay_20260911/uturn_dataset.csv \
  --output ~/simulation_traces_autoware180_replay_20260911/autoware171_vs_180_comparison.png
```

### TTC 計算モデル (CVM / CTRV など) の比較

```bash
# 最新のシミュレーションデータで各モデルのTTCを計算し、差分をCSVに出力する場合
python3 compare_ttc_modes.py

# 対象のフォルダ（ディレクトリ）を指定する場合
python3 compare_ttc_modes.py --dir ~/simulation_traces_shared_20260512_144346

# 出力されるCSVファイルの名前を指定する場合
python3 compare_ttc_modes.py --output custom_comparison_result.csv

# 両方を指定する場合
python3 compare_ttc_modes.py --dir ~/my_test_data --output my_test_diff.csv
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

`bridge` コンテナを使う 1.7.1 の手動起動は、
[docs/node21_autoware_1_7_1_awsim_bridge_commands_20260830.md](/home/passd/AWSIM_launch/docs/node21_autoware_1_7_1_awsim_bridge_commands_20260830.md:1)
を正本にしてください。

```bash
docker exec -it autoware171-x11-map bash -i -c 'cd /home/passd/awsim_labs && ./awsim_labs.x86_64 -noise false'
docker exec -it autoware171-x11-map bash -i -c 'source /home/passd/autoware/install/setup.bash && cd /home/passd/autoware && ros2 launch autoware_launch e2e_simulator.launch.xml vehicle_model:=awsim_labs_vehicle sensor_model:=awsim_labs_sensor_kit map_path:=/home/passd/autoware_map/nishishinjuku_autoware_map launch_vehicle_interface:=true rviz:=false'
```

`bash -c` や `bash -lc` で十分そうに見えても、コンテナ運用では `bash -i -c` を正本にしてください。

## PRISM / Maude worker（CPU専用）

PRISM対象では、1つのworker container内でPRISMの確率モデル・ランダム経路を実行し、その経路をMaudeで判定します。判定後はAWSIM workerと同じRay queueへ共通形式の結果を返します。Autoware、ROS、AW-Runtime-Monitor、GPUは使用しません。

PRISMは車両シナリオの代替ではなく、**AWSIM_launchが使っている統計的検証手法(二項CI・DKW)自体が理論通り正しく機能しているかを確認できる場**という役割も持ちます。AWSIM側は物理シミュレータのため「真の衝突確率」を解析的に求める手段がなく、統計推定の正しさをそれ自体では検算できません。一方PRISMの単純なDTMCモデルは、PRISM propertiesによるモデル検査で真の到達確率を厳密に(乱数を使わず、モデル・定数・horizonが同じなら常に同一の値として)計算できるため、その厳密値とサンプリングベースの推定値(CI/DKW)を突き合わせて答え合わせができます。既存の実クラスタ検証(下記)はこの突き合わせの一例です。

実行時のcontainer名は `prism_worker_<ROS_DOMAIN_ID>` になります。既存の `sim_worker_*` を停止・置換しないため、AWSIM用container名とは衝突しません。

ただし、このcluster CLIは起動時にRay headを再作成します。進行中のAWSIM cluster実験と同時には起動せず、その実験を終了してから実行してください。

2026-09-15に21・22・23・24号機の実クラスタで、40本疎通試験および基準+4条件(各200〜500本、95% Wilson CI幅0.10で逐次停止)を実行して検証済みです。検証中に見つかったRay head/Pythonバージョン/JSON出力まわりの不具合と、条件ごとの結果は[PRISM対応計画の「16. 実クラスタ検証結果」](docs/prism_integration_plan.md#16-実クラスタ検証結果-2026-09-15)を参照してください。

上記5条件はいずれも「厳密確率が95%CIに含まれたか」を1回ずつ確認したものであり、CIの被覆率(理論上95%であるべき割合)そのものを検証したものではありません。被覆率を実際に検証するには、同一条件・同一パラメータで実験全体(逐次サンプリング+CI計算)を数百〜数千回繰り返し、厳密確率がCIに含まれた割合が95%に近いかを集計する実験が必要です。PRISMは厳密確率を毎回同じ値で即座に計算できるため、この規模の繰り返し実験もAWSIM側では不可能な検証としてPRISM対象でのみ実行可能です。現時点では未実施で、今後の検証候補です。

```bash
docker build \
  -f docker/prism-maude.Dockerfile \
  -t awsim-launch/prism-maude:0.1.0 \
  docker

python3 run_orchestrator_cluster_v2.py \
  --target prism \
  --container-profile prism_maude \
  --case-kind simple_reliability_dtmc \
  --param model=simple_reliability_dtmc \
  --param steps=20 \
  --output ./verification_results/prism_cluster.jsonl
```

`--mode explore` では固定パラメータ1件を実行します。逐次停止つきの反復サンプリングには、以下の `--mode binomial_ci` または同じ司令塔を内部利用する `python3 -m apps.cli.prism_main` を使用します。詳細は [PRISM対応計画](docs/prism_integration_plan.md) を参照してください。

固定パラメータを繰り返し、Wilson信頼区間が指定幅に達した時点で司令塔から停止する場合は次を使います。`experiment-id` とパラメータからsampling batchを分離するため、異なる条件の結果は同じ信頼区間に混ざりません。

```bash
python3 run_orchestrator_cluster_v2.py \
  --target prism \
  --container-profile prism_maude \
  --case-kind simple_reliability_dtmc \
  --mode binomial_ci \
  --binomial-target c_failure \
  --binomial-confidence 0.95 \
  --binomial-target-width 0.3 \
  --max-samples 60 \
  --param steps=20 \
  --param p_normal_degrade=0.1 \
  --param p_normal_failure=0.01 \
  --param p_degraded_normal=0.3 \
  --param p_degraded_failure=0.1 \
  --output /home/passd/prism_results/records.jsonl \
  --dataset-csv /home/passd/prism_results/dataset.csv
```

`records.jsonl` は、各workerが返した完全な `EvaluationRecord` をRayの共有ストアが司令塔側で1ファイルへ直列化したものです。`dataset.csv` は、同じrecordを既存の統計評価が読める1行1標本の表へ変換したものです。workerは障害調査用のローカルJSONLも各成果物ディレクトリに保持しますが、利用者が指定した `--output` とは別物です。

**DKW(逐次/固定回数)モード**: 二項ではなく`steps_to_failure_capped`のような連続値・カウント指標の分位点を、AWSIMの`--mode dkw`/`dkw_fixed`と同じ`DKWModeRunner`(ステージ制alpha-spending、`orchestration/dkw_mode.py`)で評価します。`--mode dkw`は逐次(ステージごとに評価して早期停止)、`--mode dkw_fixed`は`--max-samples`本を必ず収集してから1回だけ評価します。

```bash
python3 run_orchestrator_cluster_v2.py \
  --target prism \
  --container-profile prism_maude \
  --case-kind simple_reliability_dtmc \
  --mode dkw \
  --prism-dkw-target-metric steps_to_failure_capped \
  --prism-dkw-confidence 0.95 \
  --prism-dkw-target-epsilon 0.15 \
  --max-samples 200 \
  --param steps=20 \
  --param p_normal_degrade=0.1 \
  --param p_normal_failure=0.01 \
  --param p_degraded_normal=0.3 \
  --param p_degraded_failure=0.1 \
  --output /home/passd/prism_results/dkw_records.jsonl \
  --dataset-csv /home/passd/prism_results/dkw_dataset.csv
```

PRISMは毎回新規experimentから始まりAWSIMのような既存datasetの蓄積を前提にできないため、`--mode dkw`(逐次)は最初の1標本のみで評価を開始します。標本数1件のときの分位点計算(q=0.05既定)は区間幅が数学的に必ず0になり誤って「収束」と判定される既知の挙動が実装当初あったため、`evaluation/dkw.py`にn<2を「収集継続」として扱うガードを追加済みです。また、この修正の過程で`DKWModeRunner.handle_sequential`(AWSIM/PRISM共通コード)が`max_samples`を一切チェックしていない、より深刻な既存バグも発見・修正しました(到達不可能な`--prism-dkw-target-epsilon`を指定すると標本を無限に発行し続けてしまう不具合。実際に実PRISM/Maudeバイナリで7000件超の実行を誘発したことを確認した上で修正)。現在は複数標本にわたる本来の逐次収束が正しく働き、未収束のまま`--max-samples`に達した場合は明示的に停止します。詳細は[PRISM対応計画のDKW節](docs/prism_integration_plan.md)を参照してください。同じ`--mode dkw`/`dkw_fixed`は`--target awsim`でも利用できます。

**SPRT(逐次確率比検定)モード**: 信頼区間の幅ではなく、2つの仮説(`H0: p>=p0` / `H1: p<=p1`)のどちらを採択するかを逐次判定します(Wald 1945; Younes 2006の確率モデル検査向け定式化)。`--sprt-p0`/`--sprt-p1`/`--sprt-beta` で2仮説と第二種の誤り率を指定し、`--sprt-confidence` は `1-α`(第一種の誤り率)を表します。

```bash
python3 run_orchestrator_cluster_v2.py \
  --target prism \
  --container-profile prism_maude \
  --case-kind simple_reliability_dtmc \
  --mode sprt \
  --sprt-target c_failure \
  --sprt-p0 0.5 \
  --sprt-p1 0.3 \
  --sprt-beta 0.05 \
  --sprt-confidence 0.95 \
  --max-samples 500 \
  --param steps=20 \
  --param p_normal_degrade=0.1 \
  --param p_normal_failure=0.01 \
  --param p_degraded_normal=0.3 \
  --param p_degraded_failure=0.1 \
  --output /home/passd/prism_results/sprt_records.jsonl \
  --dataset-csv /home/passd/prism_results/sprt_dataset.csv
```

`--max-samples` に達しても判定が出ない場合は `stop_max_samples` として未決着のまま停止します(誤って安全/危険と断定しません)。同じ `--mode sprt` は `--target awsim` でも利用できます(`apps/cli/orchestrator_main.py` 経由)。

**EBStop(Empirical Bernstein Stopping)モード**: `binomial_ci`/`dkw`が最悪ケースを想定した固定の区間幅で判定するのに対し、EBStopは**実測した分散**を使って停止判定します(Mnih, Szepesvári, Audibert, ICML 2008)。真の分散が小さい指標ほど、DKWより少ないサンプル数で収束できます。二項指標(`c_collision`等)ではなく、`min_ttc`/`steps_to_failure_capped`のような連続値・有界な指標が対象です。目標は絶対幅ではなく**相対誤差**(`|推定値-真の値| <= ε・|真の値|`)である点が`dkw`と異なります。

```bash
python3 run_orchestrator_cluster_v2.py \
  --target prism \
  --container-profile prism_maude \
  --case-kind simple_reliability_dtmc \
  --mode ebstop \
  --ebstop-target steps_to_failure_capped \
  --ebstop-epsilon 0.1 \
  --ebstop-value-range-min 0 \
  --ebstop-value-range-max 20 \
  --ebstop-confidence 0.95 \
  --ebstop-max-samples 2000 \
  --param steps=20 \
  --param p_normal_degrade=0.1 \
  --param p_normal_failure=0.01 \
  --param p_degraded_normal=0.3 \
  --param p_degraded_failure=0.1 \
  --output /home/passd/prism_results/ebstop_records.jsonl \
  --dataset-csv /home/passd/prism_results/ebstop_dataset.csv
```

`--ebstop-value-range-min/-max` には指標が理論上取りうる値の範囲(`R`)を指定します。同じ`--mode ebstop`は`--target awsim`でも利用できます。

**どの統計手法を選べばいいか迷ったら**: `binomial_ci`/`dkw`/`sprt`/`ebstop`のどれを使うべきかを、
PyDSMC(Gros et al., QEST 2025)のFig.4を参考にした決定木としてまとめています。
[docs/statistical_method_selection.md](docs/statistical_method_selection.md) を参照してください
(AWSIM用・PRISM用の2本。両者は実行アーキテクチャが異なるため、単純な葉違いではなく木の構造自体が異なります)。

`apps.cli.prism_main` は削除せず、単一containerでPRISM、Maude、統計変換まで確認するsmoke/demo入口として残しています。同じ `FixedParameterSamplingStrategy` と司令塔を使用し、独自の反復loopは持ちません。

```bash
python3 -m apps.cli.prism_main --samples 20 --min-samples 10
```

PRISMの反復実験では、厳密モデル検査とランダム経路生成を別の実行種別として扱います。

```text
実験開始時（1 TestCase）:
  PRISM propertiesによる厳密モデル検査

各標本（1 path = 1 TestCase）:
  PRISM -simpathによるランダム経路生成
  Maudeによる経路判定
```

厳密モデル検査のrecordには`c_failure`を保存しないため、二項信頼区間やDKWの標本数には入りません。最終統計reportの`exact_model_check`には、有限horizonの厳密到達確率、CIに含まれるか、標本推定値との絶対誤差が保存されます。

統計metricとregion処理はtarget profileで切り替わります。

| target | 二項metric | DKW metric | region policy |
|---|---|---|---|
| AWSIM | `c_collision` | `min_ttc` | `targets/awsim/`でtheory列を生成 |
| PRISM | `c_failure` | `steps_to_failure_capped` | `custom`（追加処理なし） |

`orchestration/binomial_mode.py`と`orchestration/dkw_mode.py`はAWSIM theoryをimportせず、注入されたregion policyだけを使用します。

PRISM targetとMaude verifierは、同じ`prism_maude`コンテナ内で動作します。ディレクトリの分離はコンテナ分割ではなく、Pythonモジュールの責務分離です。

```text
targets/prism/
├── backend.py              # TestCaseからPRISM実行を調停
├── runner.py               # PRISM CLI実行
├── trace_parser.py         # -simpath CSVの正規化
├── result_interpreter.py   # EvaluationRecordへの統合
├── verification_input.py  # FT4D入力への変換
├── dataset_adapter.py      # exact/sample datasetの分離
└── profile.py              # TestCase.inputの検証・型変換

verifiers/maude/
├── prism_checker.py         # Maude bindingとspecの実行
├── prism_trace_evaluator.py # checker固定出力からmetricへ変換
└── specs/
    └── prism_trace.maude
```

旧`targets.prism.maude_checker`は外部利用コードを壊さないための互換importだけを残しています。新規コードは`verifiers.maude.prism_checker`を使用します。

## 連続ダイナミクス (ODE/SDE) 対象

`--target dynamics --case-kind uturn` は、AWSIMのUターンと同じ入力（`dx0`・`ego_speed`・`npc_speed`）を
連続モデルで計算する対象です。AWSIM・Autoware・GPUは使わず、1件あたり1秒未満で終わります。
設計は [docs/dynamics_target_design.md](docs/dynamics_target_design.md) を参照してください。

使い方は2系統あります。

- **単独モード**: ODE/SDEそのものを統計的に評価します。AWSIMの結果は混ぜません。
  既定の制動はJAMA ai_aeb profileです。Autoware校正済みの制動を使う場合は
  `--param controller_kind=autoware171_uturn_calibrated` を付けます。
- **AWSIM比較モード**: 同じ入力をODEとAWSIMの両方に渡して結果を比べます。
  ODEはAutoware 1.7.1のtraceから校正した開始状態・NPC旋回・制動を使います。

### 汎用モデルとUターンへの具体化

モデルはすべて、離散モード $q$ を持つハイブリッド確率微分方程式として書きます。

```text
dx = f_q(t, x) dt + G_q(t, x) dW_t        （G_q = 0 なら ODE）
ガード g(t, x) が 0 を横切ったら、リセット x+ = r(x-) を適用してモード q -> q' へ切り替える
```

- `targets/dynamics/models/base.py`: モデルが実装するインターフェース `HybridSystem`
  （状態名、初期モード・状態、ドリフト $f_q$、拡散 $G_q$、ガードとリセット、射影）
- `targets/dynamics/runners/ode.py`: モードごとの `solve_ivp`。ガードは終端イベントとして根を求めます。
- `targets/dynamics/runners/sde.py`: 固定刻みEuler--Maruyama。ノイズがなければODE runnerに委譲します。

新しいモデル（車線変更など）は `HybridSystem` を実装すれば、runner・統計モード・保存経路をそのまま使えます。
Uターンはその一実装 `UTurnSystem`（[models/uturn.py](targets/dynamics/models/uturn.py)）です。
モードは `lane` → `turn` → `exit` で、式は [設計書 6.2節](docs/dynamics_target_design.md) にあります。

### Uターンモデルの中身

時刻0は、AWSIMでNPCがUターンを始めた瞬間（`dx0` 到達）に揃えています。

- 開始時の相対位置・速度・方位: `dx0`・ego速度・NPC速度からの線形校正
- NPCの旋回: 姿勢原点が半径約3.35 m・約176.6°の円弧をたどる。幾何中心はその1.12 m前方
- egoの制動: 遅れ0.616 s、立上り0.095 s、減速度3.013 m/s²（速度系列への曲線当てはめ）
- 衝突判定: 車体寸法を使った向き付き矩形（OBB）の重なり
- TTC: 5秒先まで0.1秒刻みの等速予測でOBBが重なる最初の時刻

校正値は `targets/dynamics/calibrations/` に置いています。各実行結果の `execution` metadata には、
解決後の初期状態・半径・旋回角・制動profile・校正元が保存されます。

### 2つの判定モード

同じ軌跡から、2つの判定を常に出力します。

| モード | 出力列 | 判定 | 用途 |
|---|---|---|---|
| 判定（judgment） | `c_collision` | 車体が重なったら衝突 | AWSIMの代わりの判定 |
| スクリーニング（screening） | `c_screening_candidate` | 車体間の隙間 `min_clearance` が1.1 m未満 | AWSIMで再検証する候補の抽出 |

Autoware 1.7.1で、結果を一度も見ていない100件（衝突50・非衝突50）を1回だけ評価した結果は次のとおりです。

| モード | 衝突の検出 | 見逃し | 非衝突の正判定率 | 正解率 |
|---|---|---|---|---|
| 判定 | 49/50 | 1 | 92% | 95% |
| スクリーニング | 50/50 | 0 | 72% | 86% |

連続TTCの平均誤差は0.039秒でした。見逃し0/50は見逃し率0を意味しません（95%上側信頼限界は約6%）。
検証は `ego lane=514, offset=38` の配置だけで行っています。余裕幅1.1 mはAutoware校正済みの制動に対して
決めた値なので、JAMA制動で使った場合は `screening_provenance.calibrated_for_current_controller=false` が記録されます。
詳細は [docs/dynamics_awsim_decision_modes_20261005.md](docs/dynamics_awsim_decision_modes_20261005.md) と
[docs/dynamics_awsim_final_validation_v2_20261005.md](docs/dynamics_awsim_final_validation_v2_20261005.md) を参照してください。

### 実行例

固定パラメータで1件だけ計算する場合:

```bash
python3 run_orchestrator_v2.py \
  --target dynamics --case-kind uturn \
  --param dx0=15 --param ego_speed=36 --param npc_speed=18 \
  --output artifacts/dynamics/records.jsonl \
  --dataset-csv artifacts/dynamics/dataset.csv \
  --dynamics-output-root artifacts/dynamics/traces
```

入力を一様分布から引き、衝突確率を二項信頼区間で評価する場合（スクリーニング指標を使う例）:

```bash
python3 run_orchestrator_v2.py \
  --target dynamics --case-kind uturn \
  --mode binomial_ci --max-samples 1000 --seed 42 \
  --dynamics-decision-mode screening \
  --output artifacts/dynamics/stat.jsonl \
  --dataset-csv artifacts/dynamics/stat.csv
```

- `--dynamics-decision-mode` は `binomial_ci` / `sprt` の2値指標を選びます（既定 `judgment` = `c_collision`）。
  `dkw` / `dkw_fixed` / `ebstop` は常に `min_ttc` を使います。
- 分布を指定しない場合、固定した `--param` 以外を `scenario_specs/uturn.py` の範囲から独立一様に引きます。
  `--dynamics-input-distribution '{"dx0":{"distribution":"uniform","min":10,"max":25}}'` で指定できます。
- 各行には seed・分布・実際に引いた値が残り、`<output>.summary.json` に統計レポート・停止理由・
  `dynamics_decision_mode` が保存されます。

1件をODEとAWSIMの両方で実行して比べる場合（AWSIMが動く環境が必要です）:

```bash
python3 run_dynamics_awsim_compare_v2.py \
  --dx0 15 --ego-speed 36 --npc-speed 18 \
  --decision-mode screening \
  --output artifacts/dynamics_compare/compare.jsonl
```

比較レコードには `dynamics_decision`、`decision_agree`、`decision_missed_awsim_collision` が入ります。
AWSIM側が失敗した場合は「不一致」ではなく `revalidation_unavailable` として保存されます。

保存済みのAWSIM traceとまとめて照合する場合:

```bash
TD=/home/passd/simulation_traces_sim_worker_21_20260911_125415
python3 tools/analysis/validate_dynamics_against_awsim.py \
  --trace-dir $TD --records-jsonl $TD/uturn_records.jsonl \
  --calibration-report artifacts/autoware171_uturn_screening_holdout_split_20261005.json \
  --sources-key final_validation_sources \
  --output-dir artifacts/my_validation
```

この照合ツールは、AWSIM側のTTCもODEと同じコードで計算し直します（`awsim_min_ttc`）。
`AW_Kinematics_Extractor` CVMの値は `awsim_min_ttc_extractor` 列に参考として残ります。
このextractorはワールド座標系の `twist` をyawでもう一度回転させるため、比較の基準には使いません。

### 校正のやり直し

2つの校正ツール（`tools/analysis/`）は、ODEをAWSIMの実際の挙動に合わせ直すためのものです。
普段の実験で毎回動かすものではなく、AWSIM側の挙動が変わったときに動かします。

- `calibrate_autoware_uturn_braking.py`: ego制動の遅れ・立上り・減速度。トリガー後2.5秒の速度系列に
  制動曲線を最小二乗で当てはめ、校正用traceの中央値を取ります。
- `calibrate_uturn_start_geometry.py`: Uターン開始時の相対位置・速度・方位と、NPCの旋回半径・旋回角。
  入力（`dx0`・ego速度・NPC速度）の線形式として最小二乗で当てはめます。最終評価用の集合の選定も行います。

#### どういう状況で使うか

| 状況 | 例 | 動かすツール |
|---|---|---|
| ego車の制動の仕方が変わった | Autowareのバージョン変更、制御パラメータの変更 | 制動の校正（その後、開始状態の校正も） |
| NPCの動き・配置が変わった | シナリオのlane/offset変更、waypoint生成の変更、AWSIM更新 | 開始状態とNPC旋回の校正 |
| 検証でずれが見つかった | 誤検出の増加、TTCの系統的なずれ | 原因に応じてどちらか |
| データを増やして推定を安定させたい | 新しいAWSIM実験のtraceが溜まった | 両方 |

**使ってはいけない使い方**: 最終評価の数字を良くするために、評価集合を見ながら何度も校正し直すこと。
評価の意味がなくなります。校正し直したら、未使用の集合で評価し直してください。

#### 使い方（手順と順番）

AWSIMのtraceディレクトリ（`uturn_eval_sim*.json` と `uturn_records.jsonl`）が必要です。

**手順1: 制動を校正する**（校正用・確認用のデータ分割もここで決まります）

```bash
TD=/home/passd/simulation_traces_sim_worker_21_20260911_125415
python3 tools/analysis/calibrate_autoware_uturn_braking.py $TD \
  --output artifacts/autoware171_uturn_braking_calibration_v2.json \
  --max-files 200 --calibration-fraction 0.7 --seed 20261005
```

推定結果（遅れ・立上り・減速度）と確認用traceでの誤差が表示されます。同じseedなら同じ分割になります。

**手順2: 開始状態とNPC旋回を校正し、最終評価用の集合を選ぶ**

```bash
python3 tools/analysis/calibrate_uturn_start_geometry.py \
  --trace-dir $TD --records-jsonl $TD/uturn_records.jsonl \
  --split-report artifacts/autoware171_uturn_braking_calibration_v2.json \
  --exclude-report artifacts/autoware171_uturn_start_geometry_calibration.json \
  --exclude-report artifacts/autoware171_uturn_start_geometry_calibration_v2.json \
  --seed 20261008 \
  --output artifacts/<新しい分割>.json
```

- `--split-report`: 手順1と同じ校正用・確認用の分割を使います。
- `--exclude-report`: 結果を見たことのある評価集合を除外します。複数指定できます。
- `--seed`: 最終評価の100件（衝突50・非衝突50）を選ぶ乱数です。毎回新しい値にします。

**手順3: 結果をODEに反映する**（現状は手作業）

ツールは推定結果をファイルに出すだけです。ODEが読む同梱ファイルへは手でコピーします。

- 制動: `profile` などを `targets/dynamics/calibrations/autoware171_uturn_braking.json` へ
- 開始状態: `coefficients` などを `targets/dynamics/calibrations/autoware171_uturn_start_geometry.json` へ

**手順4: 開発用データで確認する**

```bash
python3 tools/analysis/validate_dynamics_against_awsim.py \
  --trace-dir $TD --records-jsonl $TD/uturn_records.jsonl \
  --calibration-report artifacts/<新しい分割>.json \
  --sources-key development_evaluation_sources \
  --output-dir artifacts/<開発確認の出力先>
```

ここでは何度試してもかまいません。モデルや校正方法を変えるのはこの段階です。

**手順5: 余裕幅を決め直す**（現状は手作業）

開発用データで「AWSIMで衝突したのにODEの隙間が最大だった値 + 0.1 m」を求め、
`targets/dynamics/calibrations/autoware171_uturn_screening.json` を更新します。

**手順6: 最終評価を1回だけ行う**

手順4のコマンドの `--sources-key` を `final_validation_sources` に変えて実行します。

#### 現状の制約

- 同梱ファイルへの反映（手順3）と余裕幅の計算（手順5）は自動化していません。
- 同梱ファイル名と設定名はAutoware 1.7.1用です。1.8.0や1.9.0を校正すると1.7.1の値を上書きします。
  複数バージョンを並べて使うには、バージョンごとに校正ファイルを持てるようにコードを変える必要があります。

### SDE

`--param` で `solver_kind=sde`・`sde_seed`・`sde_dt_sec`・`sde_noise` を渡すと、制動・NPC加速度・NPC方位にノイズを加えられます。

```bash
python3 run_worker_v2.py \
  --target dynamics --case-kind uturn \
  --param dx0=12 --param ego_speed=36 --param npc_speed=18 \
  --param solver_kind=sde --param sde_seed=3 \
  --param 'sde_noise={"ego_brake_acceleration_std":0.25}' \
  --output artifacts/dynamics/sde.jsonl --dataset-csv artifacts/dynamics/sde.csv
```

ノイズがすべて0の場合はODEと同一の結果になります。ノイズの大きさはまだ実データから校正していないため、
SDEの衝突率は感度分析として扱い、現実の確率とは解釈しないでください。

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
