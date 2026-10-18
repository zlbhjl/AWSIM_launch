# フレームワークの拡張 (検証対象・判定器・統計手法の追加)

Extending the framework: adding a target, a judge, or a statistical method. **[English version below ↓](#english)**

このフレームワークは、検証対象ごとに変わる部分 (対象の実行と判定) と、共通の部分 (シミュレーション管理・並列実行・統計評価) を
分けています。この文書では、真の値が分かっている最小の対象 `bernoulli` ([targets/bernoulli/](../targets/bernoulli/)) を例に、
拡張の手順を説明します。

## 1. データの流れと共通の型

```text
司令塔 (orchestration/)  ──TestCase──▶  backend.run()  ──RawRunResult──▶  result_interpreter  ──EvaluationRecord──▶  統計評価 (evaluation/)
```

| 型 (`contracts/`) | 固定する項目 | 対象ごとに自由に使う項目 |
|---|---|---|
| `TestCase` | `case_id`, `target`, `case_kind` | `input` (実行条件), `meta` |
| `RawRunResult` | `case_id`, `target`, `case_kind`, `status` | `evidence` (成果物のパス), `meta` |
| `EvaluationRecord` | `case_id`, `target`, `case_kind`, `status` | `input`, `output` (判定値・測定値), `evidence`, `meta` |
| `StatisticalRequest` / `StatisticalReport` | `method`, `metric`, `confidence` / `sample_count`, `estimate`, `interval` | `options` / `diagnostics` |

統計評価は `EvaluationRecord.output` の列 (例: `c_failure`) と `status == "success"` だけを見ます。対象の中身は知りません。

## 2. 検証対象を追加する

`bernoulli` の構成 (合計 約 250 行):

| ファイル | 役割 |
|---|---|
| `targets/bernoulli/model.py` | 対象そのもの。一様乱数 `u_failure`, `u_time`, `u_score` を逆関数法で `c_failure` (Bernoulli)、`time_to_failure` (指数分布)、`score` (Beta 分布) に変換する。真の確率・分位点・平均も式で返す |
| `targets/bernoulli/backend.py` | `run(TestCase) -> RawRunResult`。対象を実行する。外部プロセスやコンテナを使う対象 (AWSIM、PRISM) はここで起動する |
| `targets/bernoulli/result_interpreter.py` | `interpret_raw_run_result(RawRunResult) -> EvaluationRecord`。実行結果を判定し、`output` に指標を入れる |
| `targets/bernoulli/statistical.py` | `STATISTICAL_TARGET_PROFILE`。統計モードが使う指標 (`binary_metric`, `dkw_metric`, `ebstop_metric`) と、入力を独立に引く対象か (`iid_sampling`)、既定の入力分布 |
| `targets/bernoulli/plugin.py` | `PLUGIN`。共通部分への登録 (`contracts/target_plugin.py` の `TargetPlugin`)。`build_components()` で backend と result_interpreter を返す。`targets/` の下の `plugin.py` は自動で見つかり、`--target` の選択肢にも自動で加わる |
| `tests/unit/targets/test_bernoulli.py` | ユニットテスト |

入力を分布から独立に引いて評価する対象 (`iid_sampling=True`) は、`dynamics` と同じ経路
(`orchestration/random_parameter_sampling.py`) で `binomial_ci` / `sprt` / `dkw` / `dkw_fixed` / `ebstop` を使えます。

```bash
python3 run_orchestrator_v2.py --target bernoulli \
  --mode binomial_ci --binomial-target-width 0.05 --max-samples 5000 --seed 1 \
  --param p_failure=0.05 \
  --output artifacts/bernoulli/records.jsonl --dataset-csv artifacts/bernoulli/dataset.csv
```

### 共通部分の修正は必要か

**入力を分布から独立に引く対象 (`iid_sampling=True`) は、`targets/<name>/` を追加するだけで使えます。** 共通部分の修正は
要りません。`tests/unit/architecture/test_add_target_without_shared_changes.py` は、テストの中で一時的な対象 `coin` を
`targets/` に置き、共通部分を変えずに CLI から統計モードを最後まで実行できることを確かめています。

それ以外の入力の引き方を使う対象では、現在は共通部分にも修正が必要です。対象ごとの処理を `plugin.py` に移していく予定です。

| 対象の種類 | 共通部分に残っている対象ごとの処理 |
|---|---|
| AWSIM 型 (GP による探索、replay) | `orchestration/orchestrator.py` の `_build_strategy`、`orchestration/strategy.py` の 1 件実行の入力、`runtime/cluster/result_sink.py` の理論値の列 |
| PRISM 型 (固定パラメータのくり返し、厳密なモデル検査) | `orchestration/orchestrator.py` の `_build_strategy` と `_resolve_target_total` |
| 対象専用の CLI オプションと検証 | `apps/cli/orchestrator_main.py`, `apps/cli/worker_main.py` (例: `--prism-*`、`--dynamics-*`) |

## 3. 判定器を追加する

判定器は `verifiers/<name>/` に置き、対象の `result_interpreter` から呼びます。例えば `verifiers/maude/` は、AWSIM の trace JSON と
PRISM の経路を Maude (LTL) で判定します。判定器は `EvaluationRecord.output` に入れる値を返すだけで、統計評価や司令塔を参照しません。

## 4. 統計手法を追加する

統計手法は「停止規則」(`StoppingRule`、`contracts/experiment.py`) として `evaluation/stopping_rules.py` に置きます。

- `spec`: 名前、手法の種類 (`binomial_ci` / `sprt` / `dkw` / `ebstop`)、保証の種類 (`fixed_sample` / `anytime` / `approximate`)、
  保証に必要な標本の性質 (`iid_uniform` など)
- `decide(data, issued=...) -> StopDecision`: これまでの結果から、止めるか、何を報告するかを決める。評価のタイミング
  (1 回だけ、毎回、誤り率の割り振り) は規則の中で決める

追加した規則は `orchestration/composed_strategy.py` の `build_stopping_rule` と `RULE_MODES`、`STOPPING_RULE_NAMES` に名前を加えると、
`--stopping-rule <名前>` で使えます。入力の引き方 (実験計画) は今の戦略のものをそのまま使い、止め方だけが置き換わります。
IID 型の対象と PRISM の固定パラメータで使えます。

追加した手法は、真の値が分かる `bernoulli` で被覆率を確認してください ([validation/coverage.md](validation/coverage.md))。
`tools/validation/coverage_experiment.py` の `SCENARIOS` に条件を追加すると、実際の停止判定のコードをそのまま何千回もくり返して、
区間が真の値を含む割合を測れます。

## 5. チェックリスト

- [ ] `targets/<name>/` に backend、result_interpreter、statistical、plugin を置いた
- [ ] 失敗した実行は `status` を `success` 以外にして、統計に入らないようにした
- [ ] IID 型でない場合は、上の表の共通部分に対象ごとの処理を追加した
- [ ] ユニットテストを追加し、`python3 -m pytest` が通る
- [ ] 統計手法を追加した場合は、被覆率を確認した

---

<a id="english"></a>

# English: Extending the framework (adding a target, a judge, or a statistical method)

The framework separates the parts that change per target (running and judging the target) from the shared parts (simulation
management, parallel execution, statistical evaluation). This document explains how to extend it using `bernoulli`
([targets/bernoulli/](../targets/bernoulli/)), a minimal target whose true values are known exactly.

## 1. Data flow and shared types

```text
orchestrator (orchestration/)  ──TestCase──▶  backend.run()  ──RawRunResult──▶  result_interpreter  ──EvaluationRecord──▶  statistics (evaluation/)
```

| Type (`contracts/`) | Fixed fields | Fields free for each target |
|---|---|---|
| `TestCase` | `case_id`, `target`, `case_kind` | `input` (run conditions), `meta` |
| `RawRunResult` | `case_id`, `target`, `case_kind`, `status` | `evidence` (artifact paths), `meta` |
| `EvaluationRecord` | `case_id`, `target`, `case_kind`, `status` | `input`, `output` (judgments and measurements), `evidence`, `meta` |
| `StatisticalRequest` / `StatisticalReport` | `method`, `metric`, `confidence` / `sample_count`, `estimate`, `interval` | `options` / `diagnostics` |

The statistical evaluation only looks at columns of `EvaluationRecord.output` (such as `c_failure`) and at `status == "success"`. It
knows nothing about the target itself.

## 2. Adding a target

The structure of `bernoulli` (about 250 lines in total):

| File | Role |
|---|---|
| `targets/bernoulli/model.py` | The target itself. Maps uniform random numbers `u_failure`, `u_time`, `u_score` by inverse transform to `c_failure` (Bernoulli), `time_to_failure` (exponential), and `score` (Beta). Also returns the true probability, quantile, and mean |
| `targets/bernoulli/backend.py` | `run(TestCase) -> RawRunResult`. Runs the target. Targets that use external processes or containers (AWSIM, PRISM) start them here |
| `targets/bernoulli/result_interpreter.py` | `interpret_raw_run_result(RawRunResult) -> EvaluationRecord`. Judges the run and puts the metrics in `output` |
| `targets/bernoulli/statistical.py` | `STATISTICAL_TARGET_PROFILE`: the metrics used by the statistical modes (`binary_metric`, `dkw_metric`, `ebstop_metric`), whether inputs are drawn independently (`iid_sampling`), and the default input distribution |
| `targets/bernoulli/plugin.py` | `PLUGIN`: the registration with the shared code (`TargetPlugin` in `contracts/target_plugin.py`). `build_components()` returns the backend and result_interpreter. Every `plugin.py` under `targets/` is found automatically and added to the choices of `--target` |
| `tests/unit/targets/test_bernoulli.py` | Unit tests |

A target whose inputs are drawn independently from a distribution (`iid_sampling=True`) can use `binomial_ci` / `sprt` / `dkw` /
`dkw_fixed` / `ebstop` through the same path as `dynamics` (`orchestration/random_parameter_sampling.py`).

```bash
python3 run_orchestrator_v2.py --target bernoulli \
  --mode binomial_ci --binomial-target-width 0.05 --max-samples 5000 --seed 1 \
  --param p_failure=0.05 \
  --output artifacts/bernoulli/records.jsonl --dataset-csv artifacts/bernoulli/dataset.csv
```

### Do you need to change the shared code?

**A target whose inputs are drawn independently from a distribution (`iid_sampling=True`) works by adding only `targets/<name>/`.**
No change to the shared code is needed. `tests/unit/architecture/test_add_target_without_shared_changes.py` places a throwaway target
`coin` under `targets/` within the test and runs a statistical mode end to end from the CLI without changing any shared code.

Targets that draw inputs in other ways currently still need changes in the shared code. The target-specific handling is planned to
move into `plugin.py`.

| Kind of target | Target-specific handling remaining in the shared code |
|---|---|
| AWSIM-like (GP exploration, replay) | `_build_strategy` in `orchestration/orchestrator.py`, the single-case input in `orchestration/strategy.py`, the theory columns in `runtime/cluster/result_sink.py` |
| PRISM-like (repeated fixed parameters, exact model checking) | `_build_strategy` and `_resolve_target_total` in `orchestration/orchestrator.py` |
| Target-specific CLI options and validation | `apps/cli/orchestrator_main.py`, `apps/cli/worker_main.py` (for example, `--prism-*`, `--dynamics-*`) |

## 3. Adding a judge

Judges go in `verifiers/<name>/` and are called from the target's `result_interpreter`. For example, `verifiers/maude/` judges AWSIM
trace JSON and PRISM paths with Maude (LTL). A judge only returns values for `EvaluationRecord.output`; it does not refer to the
statistical evaluation or the orchestrator.

## 4. Adding a statistical method

Statistical methods are stopping rules (`StoppingRule` in `contracts/experiment.py`) in `evaluation/stopping_rules.py`.

- `spec`: the name, the method family (`binomial_ci` / `sprt` / `dkw` / `ebstop`), the guarantee (`fixed_sample` / `anytime` /
  `approximate`), and the sample kinds the guarantee needs (such as `iid_uniform`)
- `decide(data, issued=...) -> StopDecision`: decides from the results so far whether to stop and what to report. The rule itself fixes
  when it evaluates (once, every time, with a spent error budget)

Adding the rule's name to `build_stopping_rule`, `RULE_MODES`, and `STOPPING_RULE_NAMES` in `orchestration/composed_strategy.py` makes it
available as `--stopping-rule <name>`. The sampling (experimental design) of the existing strategy stays the same; only the stopping is
replaced. It works with IID-sampling targets and PRISM fixed parameters.

Check the coverage of a new method on `bernoulli`, whose true values are known ([validation/coverage.md](validation/coverage.md)).
Adding a condition to `SCENARIOS` in `tools/validation/coverage_experiment.py` repeats the production stopping logic thousands of times
and measures how often the interval contains the true value.

## 5. Checklist

- [ ] Put the backend, result_interpreter, statistical profile, and plugin in `targets/<name>/`
- [ ] Failed runs get a `status` other than `success`, so they are excluded from statistics
- [ ] If the target is not IID-like, added its handling in the shared places listed above
- [ ] Added unit tests, and `python3 -m pytest` passes
- [ ] If you added a statistical method, checked its coverage
