# PRISM / Maude worker（CPU専用）

The PRISM / Maude target (CPU only). **[English version below ↓](#english)**

PRISM対象では、1つのworker container内でPRISMの確率モデル・ランダム経路を実行し、その経路をMaudeで判定します。判定後はAWSIM workerと同じRay queueへ共通形式の結果を返します。Autoware、ROS、AW-Runtime-Monitor、GPUは使用しません。

PRISMは車両シナリオの代替ではなく、**AWSIM_launchが使っている統計的検証手法(二項CI・DKW)自体が理論通り正しく機能しているかを確認できる場**という役割も持ちます。AWSIM側は物理シミュレータのため「真の衝突確率」を解析的に求める手段がなく、統計推定の正しさをそれ自体では検算できません。一方PRISMの単純なDTMCモデルは、PRISM propertiesによるモデル検査で真の到達確率を厳密に(乱数を使わず、モデル・定数・horizonが同じなら常に同一の値として)計算できるため、その厳密値とサンプリングベースの推定値(CI/DKW)を突き合わせて答え合わせができます。既存の実クラスタ検証(下記)はこの突き合わせの一例です。

実行時のcontainer名は `prism_worker_<ROS_DOMAIN_ID>` になります。既存の `sim_worker_*` を停止・置換しないため、AWSIM用container名とは衝突しません。

ただし、このcluster CLIは起動時にRay headを再作成します。進行中のAWSIM cluster実験と同時には起動せず、その実験を終了してから実行してください。

2026-09-15に21・22・23・24号機の実クラスタで、40本疎通試験および基準+4条件(各200〜500本、95% Wilson CI幅0.10で逐次停止)を実行して検証済みです。検証中に見つかったRay head/Pythonバージョン/JSON出力まわりの不具合と、条件ごとの結果は[PRISM対応計画の「16. 実クラスタ検証結果」](../prism_integration_plan.md#16-実クラスタ検証結果-2026-09-15)を参照してください。

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

`--mode explore` では固定パラメータ1件を実行します。逐次停止つきの反復サンプリングには、以下の `--mode binomial_ci` または同じ司令塔を内部利用する `python3 -m apps.cli.prism_main` を使用します。詳細は [PRISM対応計画](../prism_integration_plan.md) を参照してください。

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

**DKW(逐次/固定回数)モード**: 二項ではなく`steps_to_failure_capped`のような連続値・カウント指標の分位点を、AWSIMの`--mode dkw`/`dkw_fixed`と同じ`DKWModeRunner`(ステージ制alpha-spending、`orchestration/dkw_mode.py`)で評価します。`--mode dkw`は逐次(標本が届くたびに評価して早期停止。近似です)、`--mode dkw_fixed`は`--max-samples`本を必ず収集してから1回だけ評価します(DKWの保証が成り立ちます)。[統計手法の選び方](../statistical_method_selection.md)も参照してください。

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

PRISMは毎回新規experimentから始まりAWSIMのような既存datasetの蓄積を前提にできないため、`--mode dkw`(逐次)は最初の1標本のみで評価を開始します。標本数1件のときの分位点計算(q=0.05既定)は区間幅が数学的に必ず0になり誤って「収束」と判定される既知の挙動が実装当初あったため、`evaluation/dkw.py`にn<2を「収集継続」として扱うガードを追加済みです。また、この修正の過程で`DKWModeRunner.handle_sequential`(AWSIM/PRISM共通コード)が`max_samples`を一切チェックしていない、より深刻な既存バグも発見・修正しました(到達不可能な`--prism-dkw-target-epsilon`を指定すると標本を無限に発行し続けてしまう不具合。実際に実PRISM/Maudeバイナリで7000件超の実行を誘発したことを確認した上で修正)。現在は複数標本にわたる本来の逐次収束が正しく働き、未収束のまま`--max-samples`に達した場合は明示的に停止します。詳細は[PRISM対応計画のDKW節](../prism_integration_plan.md)を参照してください。同じ`--mode dkw`/`dkw_fixed`は`--target awsim`でも利用できます。

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
[docs/statistical_method_selection.md](../statistical_method_selection.md) を参照してください
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

---

<a id="english"></a>

# English: PRISM / Maude worker (CPU only)

For the PRISM target, one worker container runs PRISM probabilistic models and random paths, and judges the paths with Maude.
The results are returned in the common format to the same Ray queue as AWSIM workers. Autoware, ROS, AW-Runtime-Monitor, and a GPU
are not used.

PRISM is not a substitute for driving scenarios. It also serves as **a place to check that the statistical methods used in
AWSIM_launch (binomial CI, DKW) work as the theory says**. AWSIM is a physical simulator, so the "true collision probability"
cannot be computed analytically, and the correctness of the statistical estimates cannot be checked on AWSIM alone. For the simple
DTMC models in PRISM, model checking of PRISM properties computes the true reachability probability exactly (without randomness;
the same model, constants, and horizon always give the same value), so it can be compared with the sampling-based estimates (CI/DKW).
The cluster validation described below is one such comparison.

Containers are named `prism_worker_<ROS_DOMAIN_ID>`. Existing `sim_worker_*` containers are not stopped or replaced, so the names do
not clash with AWSIM containers.

However, this cluster CLI recreates the Ray head when it starts. Do not start it during a running AWSIM cluster experiment; wait for
that experiment to finish.

On 2026-09-15, this was validated on a four-node cluster with a 40-path connectivity test and a baseline plus four conditions
(200–500 paths each, sequential stopping at a 95% Wilson CI width of 0.10). For the Ray head, Python version, and JSON output issues
found during validation and the per-condition results, see
["16. Cluster validation results" in the PRISM integration plan](../prism_integration_plan.md) (Japanese only).

Each of these five conditions checked once whether the exact probability fell inside the 95% CI; this does not validate the CI
coverage itself (the fraction that should be 95% in theory). Validating coverage requires repeating the whole experiment (sequential
sampling plus CI computation) hundreds to thousands of times under the same conditions and counting how often the CI contains the
exact probability. Because PRISM computes the exact probability instantly and identically every time, such repeated experiments are
possible only with the PRISM target, not with AWSIM. This has not been done yet and is a candidate for future validation.

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

`--mode explore` runs one case with fixed parameters. For repeated sampling with sequential stopping, use `--mode binomial_ci` below,
or `python3 -m apps.cli.prism_main`, which uses the same orchestrator internally. See the
[PRISM integration plan](../prism_integration_plan.md) (Japanese only) for details.

To repeat fixed parameters and let the orchestrator stop when the Wilson confidence interval reaches the requested width, use the
command below. Sampling batches are separated by `experiment-id` and parameters, so results from different conditions are not mixed
into the same interval.

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

`records.jsonl` is the full `EvaluationRecord` returned by each worker, serialized into one file on the orchestrator side by the Ray
shared store. `dataset.csv` is the same records converted into a one-row-per-sample table that the existing statistical evaluation
reads. Workers also keep a local JSONL in each artifact directory for troubleshooting, which is separate from the user-specified
`--output`.

**DKW mode (sequential / fixed count)**: evaluates quantiles of continuous or count metrics such as `steps_to_failure_capped`
(rather than binary ones) with the same `DKWModeRunner` as AWSIM's `--mode dkw` / `dkw_fixed` (`orchestration/dkw_mode.py`).
`--mode dkw` is sequential (it evaluates as samples arrive and stops early; this is an approximation), and `--mode dkw_fixed` always
collects `--max-samples` paths and evaluates once (the DKW guarantee holds). See
[Choosing a statistical method](../statistical_method_selection.md#english).

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

PRISM always starts a new experiment and cannot rely on an accumulated dataset as AWSIM does, so `--mode dkw` (sequential) starts
evaluating from the first sample. Initially, the quantile computation for a single sample (q = 0.05 by default) always produced an
interval width of 0 and was wrongly judged as converged; `evaluation/dkw.py` now treats n < 2 as "keep collecting". While fixing this,
a more serious existing bug was also found and fixed: `DKWModeRunner.handle_sequential` (shared by AWSIM and PRISM) did not check
`max_samples` at all, so an unreachable `--prism-dkw-target-epsilon` issued samples forever (confirmed to trigger more than 7,000 runs
with real PRISM/Maude binaries before the fix). Now, if `--max-samples` is reached without converging, the run stops explicitly. See the
DKW section of the [PRISM integration plan](../prism_integration_plan.md) (Japanese only). The same `--mode dkw` / `dkw_fixed` also
works with `--target awsim`.

**SPRT (sequential probability ratio test) mode**: instead of an interval width, it decides sequentially which of two hypotheses
(`H0: p>=p0` / `H1: p<=p1`) to accept (Wald 1945; the formulation for probabilistic model checking by Younes 2006). Specify the two
hypotheses and the type II error rate with `--sprt-p0` / `--sprt-p1` / `--sprt-beta`; `--sprt-confidence` is `1-α` (α is the type I
error rate).

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

If `--max-samples` is reached without a decision, the run stops undecided as `stop_max_samples` (it never wrongly declares the system
safe or unsafe). The same `--mode sprt` also works with `--target awsim` (through `apps/cli/orchestrator_main.py`).

**EBStop (Empirical Bernstein Stopping) mode**: while `binomial_ci` / `dkw` decide with a fixed interval width that assumes the worst
case, EBStop stops based on **the observed variance** (Mnih, Szepesvári, Audibert, ICML 2008). The smaller the true variance of a
metric, the fewer samples it needs compared with DKW. It targets bounded continuous metrics such as `min_ttc` /
`steps_to_failure_capped`, not binary metrics such as `c_collision`. Unlike `dkw`, the target is a **relative error**
(`|estimate - true value| <= ε·|true value|`), not an absolute width.

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

Give the theoretical range of the metric (`R`) with `--ebstop-value-range-min/-max`. The same `--mode ebstop` also works with
`--target awsim`.

**Which statistical method to choose**: a decision tree for choosing among `binomial_ci` / `dkw` / `sprt` / `ebstop`, modeled on
Fig. 4 of PyDSMC (Gros et al., QEST 2025), is in [docs/statistical_method_selection.md](../statistical_method_selection.md#english)
(two trees, for AWSIM and for PRISM; their execution architectures differ, so the trees differ in structure, not only in their leaves).

`apps.cli.prism_main` is kept as a smoke/demo entry point that checks PRISM, Maude, and the statistical conversion in a single
container. It uses the same `FixedParameterSamplingStrategy` and orchestrator and has no loop of its own.

```bash
python3 -m apps.cli.prism_main --samples 20 --min-samples 10
```

Repeated PRISM experiments treat exact model checking and random path generation as separate kinds of execution.

```text
At the start of an experiment (1 TestCase):
  exact model checking of PRISM properties

For each sample (1 path = 1 TestCase):
  random path generation with PRISM -simpath
  path judgment with Maude
```

The exact model-checking record does not store `c_failure`, so it is not counted as a sample by the binomial CI or DKW. The
`exact_model_check` field of the final statistical report stores the exact finite-horizon reachability probability, whether it lies in
the CI, and the absolute error from the sample estimate.

Statistical metrics and region handling switch according to the target profile.

| target | Binary metric | DKW metric | Region policy |
|---|---|---|---|
| AWSIM | `c_collision` | `min_ttc` | Theory columns are generated in `targets/awsim/` |
| PRISM | `c_failure` | `steps_to_failure_capped` | `custom` (no extra processing) |

`orchestration/binomial_mode.py` and `orchestration/dkw_mode.py` do not import the AWSIM theory; they only use the injected region
policy.

The PRISM target and the Maude verifier run in the same `prism_maude` container. The directory split is a separation of Python module
responsibilities, not of containers.

```text
targets/prism/
├── backend.py              # orchestrates PRISM execution from a TestCase
├── runner.py               # runs the PRISM CLI
├── trace_parser.py         # normalizes -simpath CSV
├── result_interpreter.py   # combines into an EvaluationRecord
├── verification_input.py   # converts into FT4D input
├── dataset_adapter.py      # separates exact and sample datasets
└── profile.py              # validates and converts TestCase.input

verifiers/maude/
├── prism_checker.py         # runs the Maude binding and the spec
├── prism_trace_evaluator.py # converts the checker's fixed output into metrics
└── specs/
    └── prism_trace.maude
```

The old `targets.prism.maude_checker` remains only as a compatibility import so that external code does not break. New code should
use `verifiers.maude.prism_checker`.
