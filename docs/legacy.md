# 旧フレーム (legacy) のコマンド

> **警告: 通常はこの文書のコマンドを使わないでください。**
> 旧フレーム (`master_orchestrator.py`、`run_manager.py` など) は、worker container を
> `docker run --net=host --privileged` で起動します ([redis_cluster/cluster_manager.py](../redis_cluster/cluster_manager.py))。
> この構成では ROS 2 / Cyclone DDS のマルチキャストが物理 NIC から LAN へ流れ、
> スイッチの storm-control でポートが停止されることがあります
> ([cluster_operations.md](cluster_operations.md#storm-control-と-bridge-化の理由))。
> 新しい実験は v2 の `run_orchestrator_cluster_v2.py` (bridge network) で行ってください。

v2 は同じモード (`explore`、`focus`、`margin`、`jama_edge`、`ttc_edge`、`worst_ttc`、`boundary_gap`、
`binomial_ci`、`dkw`、`dkw_fixed`、`verify_consistency`) に対応しています。主なオプションの対応は次のとおりです。

| 旧フレーム | v2 (`run_orchestrator_cluster_v2.py`) |
|---|---|
| `--type uturn` | `--case-kind uturn` |
| `--binomial_target` など `--binomial_*` | `--binomial-target` など `--binomial-*` |
| `--dkw_region`、`--dkw_bounds`、`--dkw_pure_smc`、`--dkw_simultaneous` | `--dkw-region`、`--dkw-bounds`、`--dkw-pure-smc`、`--dkw-simultaneous` |
| `--max_samples`、`--resume_from` | `--max-samples`、`--resume-from` |
| `--with_host_worker`、`--headless_host` | `--with-host-worker`、`--headless-host` |
| `--focus_points`、`--ext_mode` | 同じ名前 (`--focus_points`、`--ext_mode`) |

v2 では、出力先 (`--output`、`--dataset-csv`) と `--container-profile` も指定します。
すべてのオプションは `python3 run_orchestrator_cluster_v2.py --help` で確認できます。

## 実験の開始 (モード選択)

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

# 【DKWモード】統計的モデル検査(SMC)で指標の分位点を評価する
# 1. 手動で指定した領域を評価する場合
python3 master_orchestrator.py --type uturn --mode dkw --dkw_bounds '{"dx0": [20.0, 25.0], "ego_speed": [30.0, 35.0]}'

# 2. 過去のデータから自動で領域を算出して評価する場合
#  emp_safe: 過去のデータで「衝突しなかった（経験的安全）」領域。
python3 master_orchestrator.py --type uturn --mode dkw --dkw_region emp_safe

#  jama_safe: JAMA物理モデルで「人間なら安全」とされた領域。
python3 master_orchestrator.py --type uturn --mode dkw --dkw_region jama_safe

#  intersect_safe: 上記2つの両方を満たす（AND）、確実な安全領域。
python3 master_orchestrator.py --type uturn --mode dkw --dkw_region intersect_safe

#  union_safe: 上記2つのどちらかを満たす（OR）、少し広めの安全領域。
python3 master_orchestrator.py --type uturn --mode dkw --dkw_region union_safe

# 3. 過去の探索データを使わず、新しい一様標本だけで評価する場合 (逐次評価のため近似。保証が必要なら 5. の dkw_fixed)
# (既定では、KDE で推定した重みで AI の探索データも再利用します。重みが推定値で標本も適応的に選ばれているため、
#  これは経験的な補正であり、DKW 不等式の保証はそのままでは成り立ちません。逐次評価もステージ内の繰り返しの確認を含むため近似です)
python3 master_orchestrator.py --type uturn --mode dkw --dkw_region intersect_safe --dkw_pure_smc

# 4. TTCや距離など、複数の指標を同時に評価し、ボンフェローニ補正を用いた絶対的な同時保証を行う場合
python3 master_orchestrator.py --type uturn --mode dkw --dkw_simultaneous

# (応用) KDEデータ再利用 ＋ JAMA経験的安全領域 ＋ ボンフェローニ同時保証 をすべて組み合わせた例 (KDE 再利用を含むため経験的な評価)
python3 master_orchestrator.py --type uturn --mode dkw --dkw_region intersect_safe --dkw_simultaneous --resume_from ~/simulation_traces_shared_20260611_104737

# 【固定サンプリング+一括DKW評価モード (dkw_fixed)】指定回数サンプリング後に一括DKW評価
# 5. 純粋SMC: 過去データを使わず、新規6000回のサンプリングのみで評価する場合 (DKW 不等式の保証が成り立つ構成)
python3 master_orchestrator.py --type uturn --mode dkw_fixed --dkw_region intersect_safe --dkw_simultaneous --max_samples 6000 --dkw_pure_smc

# 6. 全データ利用: resume_fromの過去データ＋新規サンプルで評価する場合 (探索データを重みなしで使うため保証なし)
python3 master_orchestrator.py --type uturn --mode dkw_fixed --dkw_region intersect_safe --dkw_simultaneous --resume_from ~/simulation_traces_shared_20260611_104737 --max_samples 6000

# 7. 中断再開: 途中まで終わった dkw_fixed を再開する場合（_dataset.csv の続きから）
python3 master_orchestrator.py --type uturn --mode dkw_fixed --dkw_region intersect_safe --dkw_simultaneous --max_samples 6000

# 過去に退避させた特定のデータ(例: ~/simulation_traces_shared_...)を復元して、そこから探索を再開する場合
python3 master_orchestrator.py --type uturn --mode ttc_edge --resume_from ~/simulation_traces_shared_20260525_184326
```

## チェッカーの手動実行
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

## 実験の完全リセットとデータ退避
**完全に新しい実験**を始める前に、全コンピュータの過去のデータ（CSVや動画など）を一斉にバックアップ（退避）させ、環境をクリーンにします。
*(※ 単に検証を再開したいだけの場合は実行しないでください)*
```bash
python3 archive_results.py
```

## TTC 計算モデル (CVM / CTRV など) の比較

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

## システムの停止・強制終了
```bash
# 安全な停止（各ワーカーに終了シグナルを送り、キリの良いところで終わる）
pkill -2 -f master_orchestrator.py

# 強制終了（今すぐすべてのプロセスと全号機のコンテナを強制停止する）
pkill -9 -f master_orchestrator.py
python3 stop_containers.py
```
