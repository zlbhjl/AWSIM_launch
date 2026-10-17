# BBSL / FT4D 連携

BBSL / FT4D integration. **[English version below ↓](#english)**

[BBSL-test](https://github.com/IOKENTOI/BBSL-test) (画像認識へのノイズ付与・推論・判定を行う実験エンジン) の
raw result を、AWSIM_launch 内の FT4D コア (`verification_core/ft4d`) で評価します。
FT4D の手法は [Aoki et al., "Dataset Fault Tree Analysis for Systematic Evaluation of Machine Learning Systems", PRDC 2020](https://doi.org/10.1109/PRDC50213.2020.00021) に基づきます。

### 論文のうち実装している範囲

実装しているもの:

- 故障木の構造 (基本事象・中間事象・TOP 事象、AND / OR ゲート) と、universal dataset `U`・failure dataset `D(n)`・error dataset `E(n)`
- 故障率 σpf = |D(n)|/|U|、誤り率 σpe = |E(n)|/|U|、基本事象での σpe = σpf · σpb
- 伝播: OR ゲートは和、AND ゲートは `min` (論文の上限評価)。比較用に `--and-rule product` も選べます (論文の上限ではありません)
- Chernoff-Hoeffding の不等式による標本数 n = ⌈ln(2/δ) / (2ε²)⌉ と合否判定、Bonferroni 補正による信頼度の伝播

実装していないもの:

- 基本事象が不合格のときに、安全機構で故障率を下げて TOP 事象まで再計算する手順 (論文の実験で行っている改善のループ)
- 標本数が Chernoff-Hoeffding の必要数に足りないときの、自動での追加サンプリング
- BBSL-test 以外の形式のデータからの `U`・`D(n)`・`E(n)` の自動抽出

> **注意**: AND ゲートの `min` が上限になるのは、論文の定義どおり、AND 事象の error dataset が子事象の error dataset の
> 積集合になっている場合です。複合条件で新しく生じる誤り (単独のノイズでは起きないが、組み合わさると起きる見逃し) があると
> この前提が成り立たず、過小評価になります。例えば子事象の一方の誤り率が 0 だと、AND 事象も 0 と推定されます。
> 重要な判断に使う場合は、複合条件の一部を直接実測して確認してください。

以下の例の `--target-repo` は、BBSL-test を clone したディレクトリに読み替えてください。

## legacy 互換入口から起動する
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

## BBSL の raw result を FT4D で再計算する (推奨)
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

### BBSL 側で raw result だけを作るコマンド
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

## FT4D コアを単独で確認する
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

---

<a id="english"></a>

# English: BBSL / FT4D integration

Raw results from [BBSL-test](https://github.com/IOKENTOI/BBSL-test) (an experiment engine that adds noise to images, runs inference, and
judges the results) are evaluated with the FT4D core in AWSIM_launch (`verification_core/ft4d`).
The FT4D method follows [Aoki et al., "Dataset Fault Tree Analysis for Systematic Evaluation of Machine Learning Systems", PRDC 2020](https://doi.org/10.1109/PRDC50213.2020.00021).

### Which parts of the paper are implemented

Implemented:

- The fault tree structure (basic events, intermediate events, the top event, AND / OR gates) and the universal dataset `U`, failure
  datasets `D(n)`, and error datasets `E(n)`
- Failure rate σpf = |D(n)|/|U|, error rate σpe = |E(n)|/|U|, and σpe = σpf · σpb for basic events
- Propagation: sum for OR gates and `min` for AND gates (the paper's upper bound). `--and-rule product` is also available for
  comparison (it is not the paper's upper bound)
- The sample size n = ⌈ln(2/δ) / (2ε²)⌉ and pass/fail decision from the Chernoff-Hoeffding inequality, and confidence propagation
  with the Bonferroni correction

Not implemented:

- The procedure of reducing failure rates with safety mechanisms and recomputing up to the top event when a basic event fails (the
  improvement loop in the paper's experiment)
- Automatic additional sampling when the number of samples is below the Chernoff-Hoeffding requirement
- Automatic extraction of `U`, `D(n)`, and `E(n)` from data in formats other than BBSL-test's

> **Note**: the `min` of an AND gate is an upper bound only when, as in the paper's definition, the error dataset of the AND event is
> the intersection of the error datasets of its children. If combined conditions produce new errors (misses that do not occur with a
> single noise but do when noises are combined), this assumption does not hold and the result is underestimated. For example, if the
> error rate of one child is 0, the AND event is also estimated as 0. For important decisions, check some combined conditions by
> measuring them directly.

In the examples below, replace `--target-repo` with the directory where you cloned BBSL-test.

## Starting from the legacy-compatible entry point

This entry point is a legacy CLI kept for compatibility with the old structure. In the new design, the BBSL logic lives in
`targets/bbsl/`, and this is kept as a compatible entry point during the transition. The current `bbsl_ft4d` adapter does not call
the old experiment scripts on the BBSL side directly; it keeps the shape of the legacy entry point and calls the new path in
AWSIM_launch in-process. The actual FT4D evaluation runs in AWSIM_launch through
`targets/bbsl/result_interpreter.py -> targets/bbsl/verification_input.py -> evaluation/ft4d_service.py`.

```bash
# Start the BBSL-test FT4D experiment from AWSIM_launch with the legacy-compatible CLI
python3 run_external_verifier.py \
  --verifier bbsl_ft4d \
  --target-repo /home/passd/BBSL-test \
  --reuse-existing-output \
  --tree basic \
  --sigma-pf-source dataset \
  --sigma-pb-mode delta-clean \
  --and-rule min

# Quick connectivity check
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

This command saves a normalized result JSON in AWSIM_launch and internally calls `run_bbsl_local_ft4d.py`. The roles on this path are:

- AWSIM_launch = the framework that starts the verifier through the legacy-compatible entry point and evaluates FT4D on the new path
- BBSL-test = the experiment engine that produces raw results

## Recomputing FT4D from BBSL raw results (recommended)

This is the currently recommended path. The external `BBSL-test` generates noise, runs inference, and makes BBSL judgments, and
AWSIM_launch recomputes FT4D by passing the raw result JSON through
`targets/bbsl/result_interpreter.py -> targets/bbsl/verification_input.py -> evaluation/ft4d_service.py`.

```bash
# Quick check
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

# Run the batch loop from scratch
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

# Continue from an existing clean baseline / noisy batches
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

# Give sigma_pf from an external setting
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

On this batch-loop path:

- `clean` runs only once at the beginning
- Noisy conditions are applied only to images that succeeded under `clean`
- Baseline / batch raw results are saved in `BBSL-test/output/batches/`
- With `--run-mode fresh`, `output/batches/*.json`, `output/*.png`, and the generated noisy images in `data/kitti/ft4d_batches/` are
  deleted before starting
- With `--run-mode resume`, the run continues from the existing baseline / noisy batches
- Passing `--sigma-pf-value EVENT_ID=value` or `--sigma-pf-json path.json` overrides the assumed `sigma_pf` values in the FT4D
  recomputation in AWSIM_launch
- AWSIM_launch combines several batches to reconstruct `U / D(n) / E(n)`
- If the Chernoff-Hoeffding sample size or the confidence is not sufficient, the next batch is added

When `sigma_pf` is overridden, AWSIM_launch recomputes it as `assumption` even if `--sigma-pf-source dataset` is specified.

Currently, the main paths

- `--execution-mode legacy`
- `--execution-mode batch-loop --condition-policy all`
- `--execution-mode batch-loop --condition-policy underconfident`

all end up on the new evaluation path in AWSIM_launch. The FT4D computation itself, the Bonferroni-based recomputation of
`recognition_test`, and the aggregation of confidence gaps run in AWSIM_launch.

The roles are:

- `BBSL-test` = experiment engine
  - noise generation
  - inference
  - comparison with the ground truth
  - saving raw results
- `AWSIM_launch` = verification core
  - FT4D
  - reconstruction of `recognition_test`
  - aggregation of confidence gaps
  - future exploration decisions

### Commands that only produce raw results on the BBSL side

As the step before passing results to AWSIM_launch, the BBSL side alone can produce raw results as follows.

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

# Path that compares all conditions on the same images
python3 /home/passd/BBSL-test/examples/run_full_experiment_all.py \
  --mini \
  --max-images 3 \
  --tree basic \
  --sigma-pf-source dataset \
  --sigma-pb-mode delta-clean \
  --and-rule min \
  --ft4d-backend none

# Older path that splits images by condition
python3 /home/passd/BBSL-test/examples/run_full_experiment.py \
  --mini \
  --max-images 6 \
  --tree basic \
  --sigma-pf-source dataset \
  --sigma-pb-mode raw \
  --and-rule min \
  --ft4d-backend none
```

The main output files are:

- `output/batches/clean_baseline.json`
- `output/batches/clean_success_image_ids.json`
- `output/batches/noisy_batch_0001.json`
- `output/experiment_all_raw_result.json`
- `output/experiment_all_raw_result_mini.json`
- `output/experiment_full_raw_result.json`
- `output/experiment_full_raw_result_mini.json`

`run_bbsl_local_ft4d.py --execution-mode batch-loop` uses `output/batches/`, and `--execution-mode legacy` reads
`experiment_*_raw_result*.json`.

## Checking the FT4D core on its own

This command first checks that the FT4D core works within AWSIM_launch alone.

```bash
python3 run_ft4d_smoke.py
```

It passes `tests/fixtures/awsim/normal_trace_maude.json` through
`targets/awsim/result_interpreter.py -> targets/awsim/verification_input.py -> evaluation/ft4d_service.py` and computes FT4D with
`verification_core/ft4d/config/awsim_demo_tree.json`. The AWSIM `EvaluationRecord.output` contains Maude judgments such as
`c_collision` and `c_ttc_*`, and the smoke test uses those values.

The same check can be run for PRISM.

```bash
python3 run_prism_ft4d_smoke.py
```

It passes `tests/fixtures/prism/prism_stage2_baseline_sample41.jsonl` (real data taken from the baseline condition of the cluster
validation on 2026-09-15: 1 exact record and 40 samples) through
`targets/prism/verification_input.py::build_verification_input_from_records -> evaluation/ft4d_service.py` and computes FT4D with
`verification_core/ft4d/config/tree_prism_demo.json`. It aggregates several PRISM sample paths as one population and shows the
violation rates of `FAILURE` / `EARLY_FAILURE` / `REPEATED_DEGRADATION` and the `top_sigma_pe` that combines them with OR.
With `--records-jsonl`, the same aggregation runs on any PRISM experiment JSONL (such as the `--output` of
`run_orchestrator_cluster_v2.py --target prism`).
