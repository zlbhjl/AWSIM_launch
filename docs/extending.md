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

それ以外の種類の対象も、`targets/<name>/` だけで追加できます。plugin に任意の属性・メソッドを持たせると、共通部分が
それを呼びます（`getattr` で読むので、要らないものは書かなくてよい）。同じテストファイルが、一時的な対象を `targets/` に置き、
共通部分を変えずに動くことを種類ごとに確かめています。

| 対象の種類 | plugin に置くもの | 例 |
|---|---|---|
| 自前の探索戦略を持つ (AWSIM 型: GP による探索、replay) | `strategies`（名前 → `StrategySpec`: 組み立て、自己終了か、`--stopping-rule` での置き換え先、待つ件数）と `select_strategy` | `targets/awsim/strategies/`、テストの対象 `walk` |
| 固定パラメータのくり返し (PRISM 型) | `select_strategy` で共通の `fixed_parameter_sampling` / `fixed_parameter_dkw` を選ぶ。厳密なモデル検査は `run_model_check_once`、`exact_probability_columns`、`exact_probability_metric`、`exact_model_check_failure_reason` | `targets/prism/plugin.py`、テストの対象 `dice` |
| クラスターのコンテナで動かす | `targets/<name>/container_profiles/<profile>.py`（`CONTAINER_LAUNCH_PROFILE`）。自動で見つかり、`--container-profile` の選択肢に加わる。必須にするなら `requires_container_profile = True` | `targets/awsim/container_profiles/`、テストの profile `walk_cpu` |
| CLI の検査と既定値 | `normalize_cli_args`、`validate_orchestrator_args`、`exclusive_options`（その対象だけが受け付けるオプション）、`config_module_for`、`scenario_profiles` | `targets/awsim/cli.py` |
| 統計の実行の要約に項目を足す | `statistical_summary_extras(args)`（返した dict を要約に加える） | dynamics の `dynamics_decision_mode` |
| 保存した結果を単独で解釈し直す（`run_result_interpreter.py`） | `build_result_interpreter(case_kind=, config_module=)`（`interpret_path` を持つ解釈器を返す）。持つ対象だけが `--target` の選択肢に加わる | `targets/awsim/plugin.py`、`targets/bbsl/plugin.py` |
| 実行・保存の細部 | `direct_case_input`（`--param` の 1 件の入力）、`derived_input_columns`（保存時に足す列）、`requires_gpu_health_check`、`task_payload_meta_keys` | `targets/awsim/plugin.py` |

共通部分に残る対象ごとの部分は、**CLI オプションの定義**だけです。新しい対象が専用のオプションを持つときは、その定義を
`apps/cli/orchestrator_main.py` / `worker_main.py` に足します（検査と既定値は plugin に置きます）。

### 重点サンプリングに対応する

固定 P・固定 Q の重点サンプリングは、`targets/<name>/` だけで対応できます。plugin に属性 `importance_sampling` を持たせ、
`add_arguments(parser)`（対象専用のオプション）と `run(args)`（実験の実行）を持つオブジェクトを返します
（`contracts/importance_sampling_plugin.py`）。共通の CLI `python3 -m apps.cli.importance_main` が、プランの `target:` から
plugin を選び、オプションを足して `run` を呼びます。実行・保存・比較・完了の監査は
`orchestration/fixed_importance_sampling.py` の関数をそのまま使えます。例は `targets/dynamics/importance_sampling.py` と、
アーキテクチャのテストの一時的な対象 `coin` です（`targets/coin/` に置いたファイルだけで共通 CLI から実行できることを確かめています）。

### dynamics にモデルを足す

`targets/dynamics/models/<name>.py` に `HybridSystem` と adapter（`resolve`、`interpret` など）を置き、
`targets/dynamics/models/registry.py` の `_adapters` に 1 行加えます。共通の SDE 実行器、統計モード（`--case-kind <name>` または
`--param model_id=<name>`）、重点サンプリング（`dynamics_schema_version: 2` のプラン）がそのまま使えます。統計モードの既定は、
入力を引かずにノイズだけを変え（標本ごとの `sde_seed`）、指標はモデルの `c_event` と連続値です。変えたいときは adapter に
`statistical_defaults()` を定義します。テストでは一時的なモデルを登録して、統計モードを最後まで実行しています。

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

Targets of the other kinds are added under `targets/<name>/` as well. The shared code calls optional plugin attributes and methods
(read with `getattr`, so only the ones a target needs have to exist). The same test file places a throwaway target of each kind
under `targets/` and checks that it runs without changing the shared code.

| Kind of target | What the plugin provides | Example |
|---|---|---|
| Its own exploration strategy (AWSIM-like: GP exploration, replay) | `strategies` (name → `StrategySpec`: builder, self-terminating, replacement under `--stopping-rule`, cases to wait for) and `select_strategy` | `targets/awsim/strategies/`, the test target `walk` |
| Repeated fixed parameters (PRISM-like) | `select_strategy` picking the shared `fixed_parameter_sampling` / `fixed_parameter_dkw`; the exact model check through `run_model_check_once`, `exact_probability_columns`, `exact_probability_metric`, `exact_model_check_failure_reason` | `targets/prism/plugin.py`, the test target `dice` |
| Running in cluster containers | `targets/<name>/container_profiles/<profile>.py` (`CONTAINER_LAUNCH_PROFILE`), found automatically and added to the choices of `--container-profile`; `requires_container_profile = True` makes one mandatory | `targets/awsim/container_profiles/`, the test profile `walk_cpu` |
| CLI checks and defaults | `normalize_cli_args`, `validate_orchestrator_args`, `exclusive_options` (options only this target accepts), `config_module_for`, `scenario_profiles` | `targets/awsim/cli.py` |
| Adding entries to the summary of a statistical run | `statistical_summary_extras(args)` (the returned dict is added to the summary) | dynamics' `dynamics_decision_mode` |
| Re-interpreting a saved result on its own (`run_result_interpreter.py`) | `build_result_interpreter(case_kind=, config_module=)` (returns an interpreter with `interpret_path`); only targets that have it are choices of `--target` | `targets/awsim/plugin.py`, `targets/bbsl/plugin.py` |
| Execution and storage details | `direct_case_input` (the input of one `--param` case), `derived_input_columns` (columns added when storing), `requires_gpu_health_check`, `task_payload_meta_keys` | `targets/awsim/plugin.py` |

The only target-specific part left in shared code is **the definition of CLI options**. A new target with its own options adds
their definitions to `apps/cli/orchestrator_main.py` / `worker_main.py` (checks and defaults go in the plugin).

### Supporting importance sampling

Fixed-P/fixed-Q importance sampling needs code only under `targets/<name>/`. Give the plugin an attribute `importance_sampling`
that returns an object with `add_arguments(parser)` (the target's options) and `run(args)` (the experiment)
(`contracts/importance_sampling_plugin.py`). The generic CLI `python3 -m apps.cli.importance_main` selects the plugin from the plan's
`target:`, adds its options and calls `run`. The functions in `orchestration/fixed_importance_sampling.py` do the running, storage,
comparison and completion audit. See `targets/dynamics/importance_sampling.py` and the throwaway target `coin` of the architecture
test (files placed only in `targets/coin/` run through the generic CLI).

### Adding a dynamics model

Put a `HybridSystem` and an adapter (`resolve`, `interpret`, ...) in `targets/dynamics/models/<name>.py` and add one line to
`_adapters` in `targets/dynamics/models/registry.py`. The shared SDE runner, the statistical modes (`--case-kind <name>` or
`--param model_id=<name>`) and importance sampling (a `dynamics_schema_version: 2` plan) then work unchanged. In the statistical
modes the defaults draw no inputs and vary only the noise (a per-sample `sde_seed`), with the model's `c_event` and continuous metric;
define `statistical_defaults()` on the adapter to change them. A test registers a throwaway model and runs a statistical mode end to
end.

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
