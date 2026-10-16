# AWSIM Adaptive Safety Testing Framework

検証対象を確率モデルへ抽象化せずに直接実行し、その結果を統計的に評価する、汎用の統計的検証フレームワークです。
**シミュレータ (対象の実行)・判定器・統計評価を分離**し、対象ごとに変わる部分 (`targets/`、`verifiers/`) だけを
差し替えて、シミュレーション管理・コンテナでの並列実行・統計評価 (二項信頼区間・DKW・SPRT・EBStop) を共通に使います。

- 標準構成として、マルコフ連鎖 (PRISM) とLTL判定器 (Maude) による従来型の統計的モデル検査をそのまま実行できます。
- 差し替えにより、Autoware + AWSIM の自動運転 AI や、YOLO + BBSL の画像認識 AI も同じ枠組みで評価できます。
- 実行環境 (OS、ライブラリ、Autoware のバージョンなど) の違いは対象ごとのコンテナに閉じ込めます。

| 対象 (`--target`) | 内容 | 必要な環境 |
|---|---|---|
| `awsim` | AWSIM + Autoware での走行シナリオ (U ターン、割り込み、回避、離脱、前方減速) | NVIDIA GPU、Docker |
| `dynamics` | AWSIM の U ターンと同じ入力を ODE/SDE で計算する連続ダイナミクスモデル | Python のみ |
| `prism` | PRISM の確率モデル (DTMC) の経路を Maude で判定 | Docker (CPU のみ) |
| `bbsl` | 画像認識の検証結果 ([BBSL-test](https://github.com/IOKENTOI/BBSL-test)) を FT4D で評価 | BBSL-test |

## 特徴

- **統計的な停止基準**: 二項信頼区間 (Wilson / Clopper-Pearson、anytime-valid オプション)、
  DKW 不等式による分位点評価 (段階的な alpha-spending)、SPRT、Empirical Bernstein Stopping。
  必要な精度に達した時点で実験を止めます。
- **適応的な探索**: ガウス過程回帰で衝突境界を学習し、不確実な領域や境界付近を重点的に試す探索モード
  (`explore`、`boundary_gap`、`jama_edge`、`ttc_edge`、`worst_ttc` など)。
- **分散実行**: Ray のタスクキューで複数台の worker container にケースを配り、結果を 1 つの dataset に集約します。
  worker の停止、GPU の異常、trace の遅延を検出して自動で復旧します。
- **再現性**: すべての標本に seed・入力分布・実行時の metadata を記録します。既存の dataset のケースを
  別の Autoware バージョンで再実行する `replay` モードがあります。
- **判定と指標**: Maude による形式的な判定 (衝突・TTC) と、trace から計算する連続値の指標
  (`min_ttc`、`min_distance` など)、JAMA の物理モデルによる理論値を同時に記録します。
- **重点サンプリング (固定P・固定Q)**: 評価したい分布 P を固定したまま、人間が設定した提案分布 Q で標本を生成し、
  P/Q の重みを記録します (PRISM の遷移確率、ODE/SDE の入力とノイズ経路、AWSIM の速度入力)。
  通常の P 標本と同じ標本数で、推定値・ESS・区間幅・最大重みを比較できます。
- **FT4D**: データセットに対する故障木解析と統計的検定に基づく信頼度評価 (`verification_core/ft4d`、[Aoki et al., PRDC 2020](https://doi.org/10.1109/PRDC50213.2020.00021))。

## 関連ツールとの違い

従来の統計的モデル検査ツール (UPPAAL、PRISM、Storm など) は、対象をツールが扱える形式の確率モデルとして記述する必要があります。
AI を直接扱う研究 (DSMC [Gros et al., FORTE 2020]、PyDSMC [Gros et al., QEST+FORMATS 2025]、MoGym [Gros et al., CAV 2022]、
COOL-MC [Gross et al., SETTA 2022]) も、形式 MDP や Gymnasium API などの特定の環境・性質記述を前提とします。
このフレームワークは、対象・実行環境・判定条件の記述形式に依存する部分を分離し、それらを差し替えて
多様な対象に同じ統計評価を適用することを目指しています。

## アーキテクチャ

```text
run_orchestrator_cluster_v2.py ─ 司令塔 (Ray head)
  ├─ orchestration/   探索戦略・統計モード・停止判定 (検証対象に依存しない)
  ├─ evaluation/      統計計算 (binomial_ci, dkw, sprt, ebstop, gp_boundary, ft4d_service)
  └─ runtime/cluster/ worker container の起動・監視、TaskQueue / SharedStore actor
        │ Ray Client (TCP)
        ▼
run_worker_v2.py ─ 各 worker container 内
  └─ targets/<target>/  backend (実行) → result_interpreter (判定) → EvaluationRecord
```

検証対象ごとの実装は `targets/` にあり、共通のデータ型は `contracts/` で定義しています。
詳しくは [docs/architecture.md](docs/architecture.md) を参照してください。

## インストール

Python 3.10 以上が必要です。依存パッケージは `pyproject.toml` で管理しています。

```bash
git clone https://github.com/zlbhjl/AWSIM_launch.git
cd AWSIM_launch
python3 -m pip install .                     # numpy, pandas, scipy, scikit-learn
python3 -m pip install ".[cluster,plot]"     # 分散実行 (Ray)、可視化 (matplotlib)
```

| extra | 内容 |
|---|---|
| `cluster` | Ray による分散実行 |
| `maude` | Maude の Python binding (PRISM / AWSIM の判定) |
| `plot` | 可視化 (`tools/plot`) |
| `tools` | 運用補助 (`tools/maintenance`) |
| `test` | ユニットテスト |

`awsim` 対象を動かすには、さらに次の外部ツールが必要です。いずれもこのリポジトリには含まれていません。

| ツール | 入手先 | 既定の配置先 |
|---|---|---|
| AWSIM Labs | <https://github.com/duongtd23/AWSIM-Labs> | `~/awsim_labs` |
| Autoware | <https://github.com/autowarefoundation/autoware> (image の作り方は [docker/](docker/)) | container image |
| AW-Runtime-Monitor | <https://github.com/duongtd23/AW-Runtime-Monitor> | `~/AW-Runtime-Monitor` |
| AW-CheckerPy (Maude) | <https://github.com/duongtd23/AW-CheckerPy> | `~/aw-cheaker/Maude-3.5.1/AW-CheckerPy` |
| AWSIMScriptPy (シナリオ) | <https://github.com/zlbhjl/AWSIMScriptPy> | `~/AWSIMScriptPy` |

`AW_Kinematics_Extractor/` が参照する `*.maude` と `aw_checkerpy.py` は AW-CheckerPy から、
`docker/autoware_1.9.0_awsim.Dockerfile` が使う `docker/aw_monitor/` は AW-Runtime-Monitor から取得して配置してください。

## クイックスタート

### 1. GPU なしで試す (連続ダイナミクス)

AWSIM と同じ U ターン条件を ODE で計算します。1 件 1 秒未満で、追加のツールは不要です。

```bash
# 1件だけ計算する
python3 run_orchestrator_v2.py --target dynamics --case-kind uturn \
  --param dx0=15 --param ego_speed=36 --param npc_speed=18 \
  --output artifacts/dynamics/one.jsonl --dataset-csv artifacts/dynamics/one.csv

# 入力を一様分布から引き、衝突確率の 95% 信頼区間の幅が 0.1 になるまで続ける
python3 run_orchestrator_v2.py --target dynamics --case-kind uturn \
  --mode binomial_ci --binomial-target-width 0.1 --max-samples 1000 --seed 42 \
  --output artifacts/dynamics/stat.jsonl --dataset-csv artifacts/dynamics/stat.csv
```

統計レポートと停止理由は `artifacts/dynamics/stat.jsonl.summary.json` に保存されます。
詳しくは [docs/targets/dynamics.md](docs/targets/dynamics.md) を参照してください。

### 2. PRISM / Maude (CPU のみ)

PRISM のモデル検査で厳密な確率が計算できるため、統計手法の推定値を答え合わせできます。

```bash
docker build -f docker/prism-maude.Dockerfile -t awsim-launch/prism-maude:0.1.0 docker
docker run --rm -v "$PWD":/workspace:ro -w /workspace awsim-launch/prism-maude:0.1.0 \
  python3 -m apps.cli.prism_main --samples 20 --min-samples 10 \
  --output-root /tmp/prism --report /tmp/prism/report.json
```

PRISM による厳密な確率と、標本からの推定値 (二項信頼区間・DKW) が並べて表示されます。

クラスターでの実行と各統計モードの例は [docs/targets/prism.md](docs/targets/prism.md) を参照してください。

### 3. AWSIM + Autoware (GPU クラスター)

1. Autoware の container image を作ります ([docker/README_autoware_1.9.0.md](docker/README_autoware_1.9.0.md) など)。
2. ノードの IP・ユーザー・container 名を [redis_cluster/cluster_config.py](redis_cluster/cluster_config.py) に書きます。
3. 司令塔のマシンで実行します。

```bash
python3 run_orchestrator_cluster_v2.py \
  --case-kind uturn \
  --mode binomial_ci --binomial-target c_collision \
  --binomial-method clopper-pearson --binomial-confidence 0.95 --binomial-target-width 0.02 \
  --container-profile autoware190_ekfdiagfix --scenario-profile autoware171 \
  --headless \
  --output ~/simulation_traces/uturn_records.jsonl \
  --dataset-csv ~/simulation_traces/uturn_dataset.csv
```

> **注意**: worker container は必ず bridge network の `container_profile` (`autoware171`、`autoware180*`、
> `autoware190*`) で起動してください。host network で複数台の Autoware を動かすと、DDS のマルチキャストが
> LAN へ流れ、スイッチの storm-control でポートが停止されることがあります。
> 旧フレームの `master_orchestrator.py` は host network を使うため、通常は使いません ([docs/legacy.md](docs/legacy.md))。

自動復旧のオプション、Autoware のバージョンごとの profile、timeout の扱いなどは
[docs/cluster_operations.md](docs/cluster_operations.md) を参照してください。

## 統計モード

`--mode` で選びます。どれを使うべきかは [docs/statistical_method_selection.md](docs/statistical_method_selection.md) の決定木を参照してください。

| モード | 評価するもの | 停止条件 |
|---|---|---|
| `binomial_ci` | 0/1 指標 (`c_collision` など) の確率の信頼区間 | 区間幅が目標以下 |
| `sprt` | 確率が `p0` 以上か `p1` 以下かの仮説検定 | どちらかの仮説を採択 |
| `dkw` / `dkw_fixed` | 連続値 (`min_ttc` など) の分位点 | 逐次 (段階的 alpha-spending) / 指定数の標本後に 1 回 |
| `ebstop` | 有界な連続値の平均 (相対誤差) | 実測の分散に基づく区間が目標以下 |
| `replay` | 既存 dataset のケースの再実行 | 入力のケースを使い切る |
| `explore` ほか | 衝突境界の探索 (GP) | 探索戦略ごとの条件 (`--max-samples` など) |

統計的な保証についての注意:

- `binomial_ci` は既定で、標本が増えるたびに同じ信頼水準で区間を計算し直します。何度も確認すること自体による誤り率の増加を抑えるには、`--binomial-anytime-valid` を使います (必要な標本数は増えます)。
- 安全性の判断に使う確率の区間には、`--binomial-method clopper-pearson` を使ってください。Wilson は近似のため、真の確率が 0 や 1 に近いと実際のカバレッジが名目の水準を下回ることがあります。
- `dkw` は既定で、KDE で推定した重みにより探索モードの標本も再利用します。これは経験的な補正で、DKW 不等式の保証はそのままでは成り立ちません。保証が必要な場合は `--dkw-pure-smc` を指定し、新しい一様標本だけで評価してください。

### 重点サンプリング

稀な事象の確率推定には、統計モードとは別の CLI (`apps.cli.prism_importance_main`、`apps.cli.dynamics_importance_main`、
`apps.cli.awsim_importance_main`) で、固定した Q による重み付き標本と通常の P 標本を比較できます。
Q の自動更新や逐次停止は扱いません。使い方は [docs/importance_sampling.md](docs/importance_sampling.md) を参照してください。

```bash
# ODE: 人手で設定した Q4 と通常の P を、それぞれ 1000 標本で比較する
python3 -m apps.cli.dynamics_importance_main \
  --sampling-plan configs/dynamics_importance_q4.yaml \
  --output-root artifacts/dynamics_importance_q4 \
  --max-samples 1000 --sampling-seed 20261011
```

## 適用例

性質の異なる 3 種類の対象に、同じ統計評価を適用しています。

| 対象 | 判定器 | 評価の例 |
|---|---|---|
| Autoware + AWSIM (自動運転 AI) | AW-Checker (LTL) | U ターンなどのシナリオでの衝突確率 ([Tran et al., 2025] の実験を、このフレームワーク上で再現) |
| YOLO + KITTI + ノイズ生成 (画像認識 AI) | BBSL | ノイズ (雨・遮蔽・ぼかし) による危険の見逃し確率と、FT4D による全体のリスク評価 |
| PRISM (マルコフ連鎖) | Maude (LTL) | DTMC の故障確率の推定と、モデル検査による厳密値との比較 |

## 出力

| ファイル | 内容 |
|---|---|
| `--output` (JSONL) | 標本ごとの `EvaluationRecord` (入力、判定、指標、実行時の metadata) |
| `--dataset-csv` | 同じ内容を 1 行 1 標本の表にしたもの。統計モードの再開にも使います |
| `<output>.summary.json` | 統計モードの最終レポート (推定値、区間、停止理由、seed) |
| `~/simulation_traces_sim_worker_*` (各ノード) | AWSIM の trace JSON、動画、`awsim.log`、`autoware.log` |

`status` が `success` の行だけが統計に使われます。`timeout`、`execution_error`、`analysis_error` の行は記録されますが、標本には入りません。

## テスト

```bash
python3 -m pip install ".[test]"
python3 -m pytest
```

ROS・AWSIM・GPU は不要です。AW-CheckerPy や AW-Runtime-Monitor が必要なテストは、それらが無い環境では skip されます。

## 既知の制約

- container 内のユーザー名とパス (`/home/passd`) が、container profile とコードに固定されています。
- 校正済みの ODE パラメータは Autoware 1.7.1 用だけです。
- 文書は現在日本語だけです。

## 文書

| 文書 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | ディレクトリ構成、分散実行のデータフロー、エラー処理 |
| [docs/cluster_operations.md](docs/cluster_operations.md) | AWSIM クラスターの運用 (container profile、自動復旧、timeout、監視、可視化) |
| [docs/autoware_versions.md](docs/autoware_versions.md) | Autoware 1.8.0 / 1.9.0 の既知事項 (EKF 診断による誤 MRM) |
| [docs/scenario_authoring.md](docs/scenario_authoring.md) | AWSIM シナリオの追加方法と調整 |
| [docs/targets/dynamics.md](docs/targets/dynamics.md) | ODE/SDE 対象、AWSIM との比較、校正 |
| [docs/targets/prism.md](docs/targets/prism.md) | PRISM / Maude 対象 |
| [docs/targets/bbsl_ft4d.md](docs/targets/bbsl_ft4d.md) | BBSL / FT4D 連携 |
| [docs/statistical_method_selection.md](docs/statistical_method_selection.md) | 統計手法の選び方 |
| [docs/importance_sampling.md](docs/importance_sampling.md) | 重点サンプリング (固定P・固定Q) の使い方 |
| [docs/importance_sampling_design.md](docs/importance_sampling_design.md)、[docs/awsim_importance_sampling_design.md](docs/awsim_importance_sampling_design.md) | 重点サンプリングの設計 |
| [docs/experiments/autoware_version_replay.md](docs/experiments/autoware_version_replay.md) | Autoware 1.7.1 / 1.8.0 / 1.9.0 の比較実験の記録 |
| [docs/network_storm_control_guide.md](docs/network_storm_control_guide.md) | DDS と storm-control の対策 |
| [docs/legacy.md](docs/legacy.md) | 旧フレームのコマンド (通常は使わない) |
| [docs/refactor_design.md](docs/refactor_design.md)、[docs/implementation_rules.md](docs/implementation_rules.md) | 設計方針と実装ルール |

## 引用

このソフトウェアを引用する場合は [CITATION.cff](CITATION.cff) を参照してください (GitHub の「Cite this repository」からも取得できます)。
FT4D による評価を使う場合は、次の論文も引用してください。

> Toshiaki Aoki, Daisuke Kawakami, Nobuo Chida, Takashi Tomita. Dataset Fault Tree Analysis for Systematic Evaluation of
> Machine Learning Systems. In *2020 IEEE 25th Pacific Rim International Symposium on Dependable Computing (PRDC)*,
> pp. 100–109, 2020. doi:[10.1109/PRDC50213.2020.00021](https://doi.org/10.1109/PRDC50213.2020.00021)

## 関連文献

- AW-Checker (AWSIM の trace に対する LTL 判定): D. D. Tran, T. Tomita, T. Aoki. Safety Analysis of Autonomous Driving Systems:
  A Simulation-Based Runtime Verification Approach. *IEEE Transactions on Reliability*, 74(4):4574–4588, 2025.
  doi:[10.1109/TR.2025.3561455](https://doi.org/10.1109/TR.2025.3561455)
- BBSL (画像認識の形式仕様記述言語と仕様ベーステスト): K. Tanaka, T. Aoki, T. Tomita, D. Kawakami, N. Chida.
  Specification-Based Testing of the Image-Recognition Performance of Automated Driving Systems.
  *IEEE Access*, 13:6321–6349, 2025. doi:[10.1109/ACCESS.2024.3516082](https://doi.org/10.1109/ACCESS.2024.3516082)
  (初出: K. Tanaka et al. Specification Based Testing of Object Detection for Automated Driving Systems via BBSL. *ENASE 2023*, pp. 250–261.)
- DSMC: T. P. Gros et al. Deep Statistical Model Checking. *FORTE 2020*, LNCS 12136, pp. 96–114. doi:[10.1007/978-3-030-50086-3_6](https://doi.org/10.1007/978-3-030-50086-3_6)
- PyDSMC: T. P. Gros et al. PyDSMC: Statistical Model Checking for Neural Agents Using the Gymnasium Interface.
  *QEST+FORMATS 2025*, LNCS 16143, pp. 134–156. doi:[10.1007/978-3-032-05792-1_8](https://doi.org/10.1007/978-3-032-05792-1_8)
- MoGym: T. P. Gros et al. MoGym: Using Formal Models for Training and Verifying Decision-making Agents. *CAV 2022*,
  LNCS 13372, pp. 430–443. doi:[10.1007/978-3-031-13188-2_21](https://doi.org/10.1007/978-3-031-13188-2_21)
- COOL-MC: D. Gross et al. COOL-MC: A Comprehensive Tool for Reinforcement Learning and Model Checking. *SETTA 2022*,
  LNCS 13649, pp. 41–49. doi:[10.1007/978-3-031-21213-0_3](https://doi.org/10.1007/978-3-031-21213-0_3)

## 謝辞

本研究は、JST 戦略的創造研究推進事業 CREST「次世代車載基盤システムのための形式手法と検証ツールの創出」の支援を受けて実施しました。

## ライセンス

[Apache License 2.0](LICENSE)。外部ツールについては [NOTICE](NOTICE) を参照してください。
