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

### GP 探索を使う

AWSIM の GP 探索（`explore` / `focus` / `margin` など）は共通の探索エンジン（`orchestration/exploration/`）です。
対象は次を置くだけで使えます（例: アーキテクチャのテストの一時的な対象 `slope`、AWSIM の `targets/awsim/exploration.py`）。

- 設定モジュール（plugin の `default_config_module` が返す名前）: `PARAM_RANGES`（探索する連続の入力と範囲）、`FIXED_PARAMS`、
  `TARGET_PRIORITIES`（境界を学ぶ 0/1 の指標の候補）、段階の上限など。
- plugin の `strategies` に、`orchestration/exploration/builder.py` の `build_gp_exploration_strategy` を
  `ExplorationSpec` とともに登録し、`select_strategy` でその名前を返す。
- `ExplorationSpec`（`orchestration/exploration/spec.py`）: GP の学習データの規則（`TrainingRules`）、失敗した回の判定
  （近くの点で探し直す。統計モードでは使わない）、抽出によるモードの規則。どれも無くてよい。
- 名前つき領域（`--dkw-region` に書ける名前）は、`statistical.py` の `StatisticalTargetProfile.region_vocabulary`
  （`evaluation/region_filter.py` の `RegionVocabulary`: 名前 → 条件式、必要な列、文字列として比べる列）で宣言します
  （例: AWSIM の `emp_safe`・`jama_safe`、`targets/awsim/statistical.py`）。共通の統計は対象の名前を知りません。
  宣言にない名前は実行前にエラーになります。条件式をそのまま書くこともできます。
- 統計モード（`binomial_ci` / `sprt` / `dkw` / `dkw_fixed` / `ebstop`）は探索エンジンの中では動かず、共通の統計の経路の
  「範囲から引く標本」（`orchestration/region_sampling.py`）で動きます（`build_gp_exploration_strategy` が渡します）。
  設定モジュールの範囲から一様に引き、名前つき領域なら探索データのうち領域に入る行から範囲を決めて、領域の外の点は捨てます。
  標本に番号（`sample_index`）と実行の ID（`experiment_id`）が付き、番号順に欠けずにそろった部分だけで判断します。
  探索の戦略の `StrategySpec` に `composed_as="composed_region"` を書くと、`--stopping-rule` も使えます（AWSIM はそうしています）。

### 指標の性質を宣言する

`statistical.py` の `StatisticalTargetProfile` の `metric_specs` に、指標ごとの性質を宣言します（`contracts/metric_spec.py`）。

| 種類 | 値 | 使える手法 |
|---|---|---|
| `binary()` | 0 か 1 | 二項区間、SPRT、DKW |
| `bounded(下限, 上限)` | 範囲の中 | EBStop、DKW |
| `real()` | 実数（無限大も可） | DKW |

共通基盤は、実行前に手法と指標が合うかを確かめます（例: 範囲のない `min_ttc` に EBStop を選ぶとエラー）。EBStop の範囲は宣言から
取り、コマンドの範囲は狭めることだけができます。評価のたびに宣言の外の値を数え、あれば区間を出さずにエラーで止めます。
範囲が実行の設定で決まる指標は、設定を受け取る関数で宣言します（例: PRISM の `steps_to_failure_capped` は [0, steps + 1]）。
宣言しない指標は検査されません。

成功しなかったケース（タイムアウト、実行エラーなど）は、キューが同じ入力で合計 `--max-attempts`（既定 3）回まで実行します。
外部ワーカーが受け取ってから `--case-timeout` 秒たっても戻らないケースも、失われたとして出し直します。対象は既定の時間を
plugin の `default_case_timeout_sec(case_kind)` で与えられます（AWSIM: シナリオのタイムアウトと既存の起動・猶予の時間の和）。

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

固定 P・固定 Q の重点サンプリングは、`targets/<name>/` だけで対応できます。対象はブラックボックスとして扱い、
共通の runner（`orchestration/importance/runner.py`）が、入力の分布からの標本化、提案分布の成分の選択、乱数の系列、
重みの合成と上限、実行・保存・再開・監査、比較、逐次停止を受け持ちます。設計は `docs/blackbox_importance_sampling_plan.md` にあります。

plugin に属性 `importance_sampling` を持たせ、次の口を持つオブジェクトを返します（`contracts/importance_sampling_plugin.py`）。

| 口 | 内容 |
|---|---|
| `prepare(plan, config) -> PreparedTarget` | プランを読んで検査する。既定の指標、入力の分布（`orchestration/importance/input_law.py` の `parse_input_laws` に自分の変数名を渡す）、内部の乱数への提案分布（`InternalProposal`）、使う乱数の系列の名前を返す |
| `build_case(request) -> TestCase` | 共通の runner が決めた入力・seed・成分（`CaseRequest`）から、1 標本のケースを作る |
| `build_components(args)` | CLI で実行するときの backend と結果の解釈器 |
| 任意: `add_arguments(parser)`、`run_options(args)` | 対象専用のオプションと、その値の受け渡し |
| 任意: `exact_probability(plan)` | 真値（被覆率の検証の道具が使う） |
| 任意: `proposal_keys` | pilot の試行が置き換える `proposal.definition` のキー |

入力を受け取って値を返すだけの対象（AWSIM など）は、これだけで済みます。対象の中に乱数があり、その分布を Q に変える場合は、
plugin で capability `internal_density` を宣言し（`contracts/capabilities.py`）、重点標本ごとに、引いた乱数の P と各成分での
対数密度を `meta.sampling.internal` で返します（`contracts/sampling.py` の `normalize_internal_report`）。重みは返しません。
混合、防御的重み、重みの上限 1/β は共通の runner が計算します。宣言せずに内部の提案分布を指定すると、実行前にエラーになります。

共通の CLI `python3 -m apps.cli.importance_main` と検証の道具（`tools/validation/` の pilot での調整、被覆率、参照との比較、
通常 MC での参照値）は、プランの `target:` から対象を選びます。例は `targets/dynamics/importance_sampling.py`（内部の乱数）、
`targets/awsim/importance_sampling.py`（入力だけ）と、アーキテクチャのテストの一時的な対象 `coin`（入力だけ）、
`gauss`（内部の乱数。被覆率と pilot の道具も確かめる）です。

#### クラスターで実行する

`--cluster` を付けると、ケースを手元ではなくクラスターのワーカー（Ray のキュー）で実行します（`docs/cluster_importance_sampling_plan.md`）。
対象は口 `cluster_launch_options(args)`（ワーカーの起動の設定。AWSIM は U ターンのワーカーを、標本ごとに世界を作り直す設定で起動する）を
持ち、プランは `seed_policy: streams`（系列ごとの seed）を使います。記録は標本の番号順に保存するので、ワーカーが終わる順序に
かかわらず、結果は手元の実行と同じです。ワーカーが受け取ってから `--case-timeout` 秒たっても記録が届かないケースは、同じ入力・同じ seed で
出し直します（`--max-attempts` まで）。逐次停止は番号順に連続した標本だけで判断し、止めた後に届いた記録は結果に入れません。

```bash
python3 -m apps.cli.importance_main --sampling-plan PLAN --output-root DIR --max-samples 2000 \
  --cluster --container-profile autoware190 --scenario-profile autoware190 --headless
```

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
IID 型の対象、PRISM の固定パラメータ、範囲から引く対象（AWSIM などの統計モード、`composed_region`）で使えます。

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

### Using the GP exploration

AWSIM's GP exploration (`explore` / `focus` / `margin`, ...) is a shared engine (`orchestration/exploration/`).
A target uses it by providing (see the throwaway target `slope` of the architecture test, and AWSIM's `targets/awsim/exploration.py`):

- A settings module (the name its plugin's `default_config_module` returns): `PARAM_RANGES` (continuous inputs to explore and their
  ranges), `FIXED_PARAMS`, `TARGET_PRIORITIES` (candidate 0/1 metrics whose boundary is learnt), phase limits, ...
- In the plugin's `strategies`, `build_gp_exploration_strategy` of `orchestration/exploration/builder.py` registered with an
  `ExplorationSpec`, and that name returned by `select_strategy`.
- The `ExplorationSpec` (`orchestration/exploration/spec.py`): GP training rules (`TrainingRules`), which rows are failed runs (searched
  again near the point; not in statistical modes), and the rules of extraction modes. All are optional.
- Region names (usable in `--dkw-region`) are declared in `StatisticalTargetProfile.region_vocabulary` in `statistical.py`
  (`RegionVocabulary` of `evaluation/region_filter.py`: name -> condition, required columns, columns compared as strings), for example
  AWSIM's `emp_safe` / `jama_safe` in `targets/awsim/statistical.py`. The shared statistics know no target's names; an undeclared name
  fails before running. A condition can also be written directly.
- The statistical modes (`binomial_ci` / `sprt` / `dkw` / `dkw_fixed` / `ebstop`) do not run in the exploration engine but on the
  shared statistical path, as region sampling (`orchestration/region_sampling.py`; `build_gp_exploration_strategy` hands them over).
  Samples are drawn uniformly from the settings module's ranges; with a named region the ranges come from the earlier results inside
  it, and points outside it are skipped. Samples get a number (`sample_index`) and a run id (`experiment_id`), and decisions use the
  contiguous prefix of samples in sample order. With `composed_as="composed_region"` in the exploration strategy's `StrategySpec`,
  `--stopping-rule` works too (AWSIM does this).

### Declaring what values a metric takes

Declare each metric in `metric_specs` of the `StatisticalTargetProfile` in `statistical.py` (`contracts/metric_spec.py`).

| Kind | Values | Methods |
|---|---|---|
| `binary()` | 0 or 1 | binomial interval, SPRT, DKW |
| `bounded(lower, upper)` | within the range | EBStop, DKW |
| `real()` | real (infinity allowed) | DKW |

Before a run the shared code checks that the method fits the metric (for example EBStop on the unbounded `min_ttc` is an error).
EBStop takes its range from the declaration; the command line may only narrow it. Every evaluation counts values outside the
declaration and stops with an error instead of an interval. A metric whose range depends on the run's settings is declared with a
function of them (for example PRISM's `steps_to_failure_capped` is in [0, steps + 1]). Undeclared metrics are not checked.

A case that does not succeed (timeout, execution error, ...) is run again by the queue with the same input, up to `--max-attempts`
(default 3) attempts in total. A case not back `--case-timeout` seconds after an external worker took it is treated as lost and
queued again. A target gives the default time through its plugin's `default_case_timeout_sec(case_kind)` (AWSIM: the scenario
timeout plus the existing start-up and grace times).

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

Fixed-P/fixed-Q importance sampling needs code only under `targets/<name>/`. The target is treated as a black box: the shared runner
(`orchestration/importance/runner.py`) draws inputs from their laws, chooses the proposal component, seeds the random streams,
composes and bounds the weights, and runs, stores, resumes, audits, compares and stops sequentially. The design is in
`docs/blackbox_importance_sampling_plan.md`.

Give the plugin an attribute `importance_sampling` that returns an object with these hooks (`contracts/importance_sampling_plugin.py`).

| Hook | What it does |
|---|---|
| `prepare(plan, config) -> PreparedTarget` | Reads and checks the plan: the default metric, the input laws (`parse_input_laws` of `orchestration/importance/input_law.py` with the target's variable names), the proposal over its internal randomness (`InternalProposal`), the names of its random streams |
| `build_case(request) -> TestCase` | Builds one sample's case from the inputs, seeds and component the shared runner chose (`CaseRequest`) |
| `build_components(args)` | The backend and result interpreter of a CLI run |
| Optional: `add_arguments(parser)`, `run_options(args)` | The target's own options and how their values are passed on |
| Optional: `exact_probability(plan)` | The truth, used by the coverage tool |
| Optional: `proposal_keys` | The keys of `proposal.definition` a pilot trial replaces |

A target that takes inputs and returns values (such as AWSIM) needs nothing more. If the target has internal randomness whose law Q
changes, its plugin declares the capability `internal_density` (`contracts/capabilities.py`), and each importance sample reports the
log density of the realised draw under P and under every component in `meta.sampling.internal` (`normalize_internal_report` in
`contracts/sampling.py`). It does not report a weight: the shared runner forms the mixture, the defensive weight and the bound 1/β.
An internal proposal without the declared capability fails before anything runs.

The generic CLI `python3 -m apps.cli.importance_main` and the validation tools in `tools/validation/` (pilot tuning, coverage,
reference comparison, plain-MC reference) choose the target from the plan's `target:`. See `targets/dynamics/importance_sampling.py`
(internal randomness), `targets/awsim/importance_sampling.py` (inputs only), and the throwaway targets `coin` (inputs only) and
`gauss` (internal randomness, also checked with the coverage and pilot tools) of the architecture test.

#### Running on the cluster

With `--cluster` the cases run on the cluster's workers (Ray queue) instead of locally (`docs/cluster_importance_sampling_plan.md`).
The target has the hook `cluster_launch_options(args)` (how its workers start; AWSIM starts U-turn workers with a fresh world per
sample), and the plan uses `seed_policy: streams`. Records are stored in sample order, so the results equal a local run whatever
order the workers finish in. A case whose record has not arrived `--case-timeout` seconds after a worker took it is submitted again
with the same inputs and seeds (up to `--max-attempts`). A sequential stop is decided on the contiguous prefix of samples, and
records that arrive after the stop are kept out of the results.

```bash
python3 -m apps.cli.importance_main --sampling-plan PLAN --output-root DIR --max-samples 2000 \
  --cluster --container-profile autoware190 --scenario-profile autoware190 --headless
```

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
replaced. It works with IID-sampling targets, PRISM fixed parameters, and region sampling (the statistical modes of AWSIM and
similar targets, `composed_region`).

Check the coverage of a new method on `bernoulli`, whose true values are known ([validation/coverage.md](validation/coverage.md)).
Adding a condition to `SCENARIOS` in `tools/validation/coverage_experiment.py` repeats the production stopping logic thousands of times
and measures how often the interval contains the true value.

## 5. Checklist

- [ ] Put the backend, result_interpreter, statistical profile, and plugin in `targets/<name>/`
- [ ] Failed runs get a `status` other than `success`, so they are excluded from statistics
- [ ] If the target is not IID-like, added its handling in the shared places listed above
- [ ] Added unit tests, and `python3 -m pytest` passes
- [ ] If you added a statistical method, checked its coverage
