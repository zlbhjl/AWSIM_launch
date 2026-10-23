# 重点サンプリング設計

## 目的と基本原則

本設計は、PRISM、決定論的・確率的微分方程式、およびAWSIMを適用対象として、重点サンプリングを共通の追跡形式で利用できるようにするものである。PRISMでは経路、決定論的微分方程式とAWSIMでは外部入力パラメータ、確率微分方程式では外部入力に加えてEuler--Maruyamaのノイズ経路をQから標本化できる。AWSIMの具体的な入力P/Q、実行経路、導入順は [awsim_importance_sampling_design.md](awsim_importance_sampling_design.md) に分離して定める。

## 今回の必須範囲と拡張例

今回の必須範囲は、**人間が定義した固定P・固定Qで標本を生成し、P/Q重みを記録して固定標本比較を行うこと**である。具体的には、PRISM adapter によるQ経路生成とDynamics adapterによるQ入力生成、`log_p`・`log_q`・`log_importance_weight` の保存、通常重要度推定、ESS・最大重みを含む比較、再現可能な保存までを完成対象とする。

以下は核を利用する将来の拡張例であり、今回の完了条件や通常運用の前提にはしない。

- `weighted_sequential` と `importance_width` による停止判定
- `suggest` による人間指定範囲内の候補Q比較
- `human_approved` / `auto` によるQ更新ライフサイクル
- BBSLなど別targetへのsampling adapter追加

拡張例もPを変更してはならず、target固有のQ定義・標本生成・P/Q計算はtarget側へ置く。

- `P` は、本来評価したい対象の確率分布または測度である。対象の意味そのものなので、人間が定義し、実験中に自動変更しない。
- `Q` は、効率よく標本を得るための提案分布である。`P` を変えずに、標本を生成する方法だけを変える。
- `P` と `Q` が異なるときは、各標本に対する重要度重み `P/Q` を記録し、推定・判定で用いる。
- 対象のモデル確率を自動的に書き換える機能ではない。特に PRISM では、モデルの遷移確率は `P` の定義であり、変更すると別の対象を検証することになる。
- 共通化するのは追跡形式と統計処理である。`P` と `Q` の具体的な定義、標本生成、密度計算は target ごとに異なるため、target 側に残す。

通常の SPRT は独立なベルヌーイ標本の成功数を前提とする。重み付き標本を使う判定は、既存 SPRT に重みを付加するのではなく、別の重み付き逐次推定・逐次判定として実装する。

## SDEノイズ経路の重点サンプリング

DynamicsのSDEは固定刻みEuler--Maruyamaであり、各stepの標準化Brownian増分を `xi_k` とする。P/Qは人間指定の時刻・mode依存 drift schedule を持つ Gaussian path model として指定する。各stepで過去と現在の状態だけから定まる `u^P_k`, `u^Q_k` により

```text
P: xi_k ~ N(u^P_k sqrt(dt), I)
Q: xi_k ~ N(u^Q_k sqrt(dt), I)
```

を用いる。これは同じ拡散係数のままdriftだけを変えるGirsanov変換のEuler離散化である。連続時間でのdrift変更にはGirsanov変換を用い、拡散係数は変更できないことは[Goodman, *Girsanov – change of measure from Brownian*](https://math.nyu.edu/~goodman/teaching/StochCalc2019/notes/Class13.pdf)を参照する。stepごとの尤度比は正確に計算する。

```text
log P_noise = Σ log φ(xi_k - u^P_k sqrt(dt))
log Q_noise = Σ log φ(xi_k - u^Q_k sqrt(dt))
```

直接 Gaussian Q は実装済みであり、通常ISの推定値・重み診断には使える。ただし、正規分布の平均shiftだけでは重みが有界でないため、有界重みHoeffding比較・逐次判定には使えない。必要なら、経路全体で一度だけ選ぶdefensive mixture Q を選ぶ。

```text
Q_noise = b P_noise + (1 - b) R_noise,  0 < b <= 1
log Q_noise = logaddexp(log b + log P_noise,
                         log(1-b) + log R_noise)
```

従って `P_noise / Q_noise <= 1/b` である。Pをproposalの一成分として残して重みを有界にするのはdefensive mixture importance samplingの標準的な構成である。[Hesterberg (1995)](https://statistics.stanford.edu/technical-reports/weighted-average-importance-sampling-and-defensive-mixture-distributions)

入力も重点化する場合は、入力とノイズ経路を合わせた重みを用いる。

```text
log P = log P_input + log P_noise
log Q = log Q_input + log Q_noise
log(P/Q) = log P - log Q
W <= W_input / b
```

P は `target_measure.definition.execution.noise_path_p`、Q は `proposal.definition.noise_path_q` に指定する。`drift_schedule` は将来情報を使わない時刻・mode依存関数である。

```yaml
target_measure:
  definition:
    execution:
      solver_kind: sde
      sde_noise: {npc_acceleration_std: 0.2}
      noise_path_p:
        distribution: gaussian_drift
        channel_drifts: {npc_acceleration: 0.0}
proposal:
  definition:
    noise_path_q:
      distribution: gaussian_drift
      drift_schedule:
        - time: [0.0, 2.0]
          mode: turn
          channel_drifts: {npc_acceleration: -0.4}
```

`distribution: defensive_gaussian_drift` と `base_weight` をQに指定すれば、上記のdefensive mixtureを使い、`P_noise / Q_noise <= 1/base_weight` を得る。

対象は**Euler--Maruyamaで離散化された現行シミュレータの確率法則**である。連続時間SDEそのものへの極限保証、拡散係数の変更、状態・未来の結果に応じて更新するadaptive controlは本実装の対象外とする。

### 登録モデルと v2 プラン

U ターン以外のモデル（現在はスカラー OU `ou_scalar`）は、`target_measure.definition.dynamics_schema_version: 2` のプランで指定する。モデル・パラメータ・初期法則（現在は `point_mass` のみ）・刻み幅と終端時刻・事象を target 側に書き、proposal 側はノイズ経路の `noise_path_q` だけを変える。例は `configs/dynamics_importance_ou_*.yaml`、計画と根拠は [SDE 重点サンプリング実装計画](sde_importance_sampling_plan.md) を参照。

v2 プランは固定 N の厳格な経路で実行する。

- 最初の標本の前に `<output-root>/manifest.json` を書く。P・数値写像・事象、Q、乱数の root と phase、予定の N を指紋として保存し、再開時に食い違えば停止する。
- 乱数は記録した 128 bit の root から、phase・run kind・標本番号・用途（入力、混合成分の選択、innovation）ごとに導出する。通常標本と重点標本は既定で独立で、`--pairing paired` で共通乱数にできる。
- 再開は標本 ID で行う。失敗した標本は `--retry-failed` で同じ入力・同じ乱数のまま再試行し、新しい乱数で置き換えない。
- `comparison.json` に完了の監査 (`audit`) と `final_status: complete | incomplete` を書く。未解決の標本が残る場合、推定値は `diagnostic_only` である。

v1 プラン（U ターンの入力 P/Q）は従来どおり件数で再開し、通常標本と重点標本で同じ seed を使う。

登録モデルは `ou_scalar`（モデル A）、`ou_augmented`（モデル B）、`nonlinear2d`（モデル C）、`uturn`（モデル D）である。U ターンは v1 の入力では従来の経路、v2 の入力では登録モデルの経路で動く（Q = P なら両者の判定は一致する）。

### 提案分布（innovation の法則）

v2 プランでは、Q を `proposal.definition.noise` に書く。平均・位置・共分散・尺度は **innovation の単位**（標準化した Brownian 増分）である。既存の `noise_path_q`（`gaussian_drift`、SDE ドリフトの単位で innovation 平均は u√dt）も使えるが、両方は書けない。

各ステップでは、生成の直前の context（時刻、実際の刻み、状態、モード、モードに入った時刻、有効なチャネル、初期入力）だけを使い、同じ context で P と Q の密度を評価する。

| `kind` | 内容 |
|---|---|
| `gaussian` | `mean_rule` と `covariance`（チャネル名つきの行列、またはモード別）。Cholesky で検査し、正則化しない |
| `student_t` | `dof`（既定 5）、`location_rule`、`scale`（尺度行列）。`covariance` を書くと尺度 = (ν−2)/ν × 共分散（ν > 2、全チャネルを含むこと） |
| `laplace` | チャネルごとに独立。`location_rule` と `scale`（既定 1/√2 で分散 1） |
| `mixture` | `components: [{weight, law}]`、`law: {kind: target}` で P 自身。任意の `defensive_weight`（β） |

平均の規則（`mean_rule` / `location_rule`）は `constant`、`piecewise`（時刻・モードの区分。区分の中に別の規則を `rule` で入れられる）、`state_threshold`、`affine_state` である。行列や規則が名前を挙げないチャネルは平均 0・単位分散（独立）とし、モードで有効なチャネルが変わるときは、その部分行列（周辺分布）を使う。

混合は経路ごとに成分を 1 回だけ選び、選ばれなかった成分も含めた経路密度で `q = β p + (1−β) Σ α_j r_j` を作る。β を書けばノイズ部分の重みは 1/β 以下で、これが保証用の区間の上限 B になる。

```yaml
proposal:
  definition:
    dynamics_schema_version: 2
    input: {kind: same_as_target}
    noise:
      kind: mixture
      defensive_weight: 0.2
      components:
        - weight: 0.5
          law:
            kind: gaussian
            mean_rule:
              kind: piecewise
              pieces:
                - {time: [0.0, 0.5], values: {w: 0.2}}
                - time: [0.5, null]
                  rule: {kind: affine_state, channels: [w], states: [x], offset: [0.5], matrix: [[-0.2]]}
        - {weight: 0.5, law: {kind: student_t, dof: 5, location_rule: {kind: constant, values: {w: 0.3}}}}
```

新しい分布族は、`targets/dynamics/proposals/` に `sample` / `log_density` を持つクラスを置き、`LAW_PARSERS` に 1 行登録すれば使える（Laplace の追加はモジュール 63 行と登録 2 行だった）。

### Q の調整と固定

`tools/validation/is_tuning.py` が、候補ごとの pilot（phase `pilot`、最終実行とは別の乱数系列）を記録し、相対標準誤差で順位を付けて図にする。採否は利用者が決め、`fix` で選んだ試行を最終プランとして書き出す（調整の記録を来歴として含む）。モデル A・B の候補は `tools/validation/ou_proposal_designs.py` が線形 Gaussian モデルの指数ティルトから作り、pilot では倍率だけを選ぶ。

## 設定の正本

実験ごとに `sampling_plan.yaml` を置く。必須なのは、人間が定義・確認する `P` と固定 `Q` だけである。設定ファイルは実験の意味を再現するための正本である。候補探索・更新条件は将来の拡張を使う場合だけ追加する。候補探索を使う場合も、人間が「変更してよい確率」「候補値」「安全制約」を先に定義する。システムは、その範囲外のモデル構造・遷移・確率を解釈または変更しない。

共通スキーマで厳密に固定するのは `experiment_id`、識別子、mode、version、更新制約だけとする。`target_measure.definition` と `proposal.definition` の内容は target ごとに異なるため、共通層では解釈・検証しない。各 target の sampling adapter が、実際に使う直前に検証する。

```yaml
schema_version: 1
experiment_id: prism_collision_v1
target: prism

target_measure:                 # P: 評価対象。原則として固定
  id: original_model_v1
  kind: prism_path_probability  # input_distribution / prism_path_probability
  model_hash: "..."
  definition:
    model: simple_reliability_dtmc

proposal:                       # 初期Q: 標本生成方法
  mode: manual_proposal         # uniform / manual_proposal
  id: boundary_focused_v1
  definition:
    transition_probabilities:
      normal:   {normal: 0.60, degraded: 0.20, failure: 0.20}
      degraded: {normal: 0.20, degraded: 0.40, failure: 0.40}
      failure:  {failure: 1.00}
  constraints:
    preserve_support: true      # P > 0 の事象を Q = 0 にしない
    max_weight: 1000

proposal_search:                # 拡張例: Q候補を作る場合だけ指定する
  enabled: true
  policy: suggest               # suggest / human_approved / auto
  adjustable_transitions:       # 人間が変更を許可する遷移だけを列挙する
    - from: normal
      to: failure
      compensate_to: normal     # 差分を吸収する遷移も人間が指定する
      values: [0.05, 0.10, 0.20]
    - from: degraded
      to: failure
      compensate_to: degraded
      values: [0.20, 0.30, 0.40]
  constraints:
    preserve_support: true
    preserve_row_sum: true
    max_importance_weight: 1000
    unchanged_transitions:
      - {from: failure, to: failure}
  evaluation:
    metric: c_failure
    pilot_samples: 100
    rank_by: [valid_weight_calculation, estimated_interval_width,
              effective_sample_size, max_observed_weight]

proposal_update:                # 拡張例: 承認・更新を使う場合だけ指定する
  min_samples_before_update: 200
  approval_required: true        # policy: human_approved で必須
```

`target_measure` は何を推定・検証するかを定義する。`proposal` は初期の標本生成方法を定義する。`proposal_search` がある場合に限り、同じ `P` に対して、人間が定義した候補集合の中で複数の `Q` を比較・更新できる。指定がなければ、候補生成は行わない。

## 標本ごとの実現値と条件付き必須項目

`sampling` は、標本を生成した実行だけが持つ追跡情報である。厳密モデル検査のように標本を生成しない実行には、`meta.sampling` 自体を付けない。

`log_p` と `log_q` は、生成された入力値や実行経路ごとに異なる。したがって設定ファイルではなく、`RawRunResult.meta.sampling` から `EvaluationRecord.meta.sampling` へ引き継いで保存する。

| 項目 | 必須となる条件 |
|---|---|
| `plan_id` | `sampling` を持つ全標本 |
| `target_measure_id` | `sampling` を持つ全標本 |
| `sampling_mode` | `sampling` を持つ全標本 |
| `proposal_id` | `manual_proposal` または `adaptive` の標本 |
| `proposal_version` | `adaptive` の標本。固定 Q では任意 |
| `importance_weight_status` | `sampling` を持つ全標本 |
| `log_p`、`log_q`、`log_importance_weight` | `importance_weight_status: available` の標本 |

`importance_weight_status` は以下のいずれかとする。

- `available`: P/Q を計算済みで、重み付き評価に利用できる。
- `not_applicable`: 通常標本または重みを必要としない標本である。
- `unavailable`: Q を使用したが、まだ P/Q を計算できず、重み付き評価には利用できない。

`log_p`、`log_q`、`log_importance_weight` の三つが全て存在する場合だけ、`log_importance_weight = log_p - log_q` を検証する。

```yaml
sampling:
  plan_id: prism_collision_v1
  target_measure_id: original_model_v1
  proposal_id: boundary_focused_v1
  proposal_version: 1
  sampling_mode: manual_proposal
  importance_weight_status: available
  log_p: -4.21
  log_q: -2.35
  log_importance_weight: -1.86  # log(P / Q)
  update_reason: null
```

通常標本の例は次の通りである。

```yaml
sampling:
  plan_id: prism_baseline_v1
  target_measure_id: original_model_v1
  sampling_mode: uniform
  importance_weight_status: not_applicable
```

これにより、各評価結果について「どの対象分布 `P` を、どの提案分布 `Q` で生成し、どの重みで集計できるか」を追跡できる。

## 提案分布の更新履歴

適応的に `Q` を変更する場合、最終版の設定だけでは標本を再現できない。各更新を `runtime/repository/` が管理する `proposal_history.jsonl` に追記する。更新の要否を判断するのは `orchestration/` であり、履歴ファイルへの直接書込みを target や evaluation から行わない。

```json
{
  "proposal_id": "boundary_focused_v2",
  "proposal_version": 2,
  "parent_version": 1,
  "updated_at_sample": 500,
  "update_reason": "境界付近の有効標本が不足",
  "approved_by": "researcher"
}
```

更新後に生成した標本は新しい `proposal_version` を参照する。提案・承認・自動更新のいずれの場合も、更新理由と適用開始点を残す。

### 候補比較の保存規則

候補比較の指標を新しい厳格な共通contractとして固定しない。ESS、最大重み、被覆率、区間幅などは、target と統計手法によって増減するためである。

固定する共通の外枠は、既存の三つだけとする。

- `EvaluationRecord`: 個々の標本。`meta.sampling` に `proposal_id`、必要なら `proposal_version`、重み計算可否を持つ。
- `ProposalCandidate`: target が作る候補の受渡し型。`plan_id`、候補識別子、版、target 固有の `definition`、理由、`diagnostics` を持つ。`definition` は共通層で解釈しない。
- `StatisticalReport`: 候補の標本群を評価した結果。`method`、`metric`、標本数、推定値、区間、`diagnostics` を持つ。

候補の評価結果は `proposal_history.jsonl` の追記型イベント `candidate_evaluated` として保存する。固定するのは提案の識別と評価reportの外枠だけであり、ESS・最大対数重みなど手法固有の値は `report.diagnostics`、target 固有の補助情報は `target_diagnostics` に置く。

```json
{
  "event_type": "candidate_evaluated",
  "plan_id": "prism_is_v1",
  "proposal_id": "failure_biased_candidate_001",
  "proposal_version": 1,
  "report": {
    "method": "self_normalized_importance_sampling",
    "metric": "c_failure",
    "sample_count": 100,
    "estimate": 0.04,
    "interval": [0.02, 0.06],
    "diagnostics": {
      "effective_sample_size": 73.1,
      "max_log_importance_weight": 1.8
    }
  },
  "target_diagnostics": {}
}
```

標本ファイルは候補ごとに分ける。repositoryは `candidates/<proposal_id>/records.jsonl` のように配置するが、Q定義の意味や診断値の内容は判断しない。`suggest` では `candidate_evaluated` と `proposed` を追記するだけで、候補を次の本実験へ適用しない。人間の確認は `approved` イベントに承認者を記録し、その後の `applied` イベントで初めて残す。

## 運用モード

標準運用は `uniform` と `manual_proposal` だけである。残りは、固定P・固定Qの比較を完了した後に必要性を評価する拡張例である。

| モード | Q を決める者 | システムの役割 |
|---|---|---|
| `uniform` | 人間が一様分布を指定 | 通常標本を生成・記録する |
| `manual_proposal` | 人間 | 指定された Q で生成し、P/Q と重みを保存する |
| `suggest`（拡張） | 人間が可変範囲を定義 | 許可範囲内の候補を比較・提示する。適用はしない |
| `human_approved`（拡張） | 人間 | 提示候補を、人間の承認後にのみ適用する |
| `auto`（拡張） | 人間が定義した探索規則 | 事前に定めた候補集合・評価規則・安全制約内だけで適用する |

`suggest`、`human_approved`、`auto` は `proposal_search` が定義され、target がその候補を生成し重みを計算できる場合だけ有効にできる。最初に実装するのは `manual_proposal` である。自動更新は、手動設定・重み計算・再現性記録が確立された後に追加する。

## target ごとの P と Q

| target | P として定義するもの | Q として変更するもの | 初期対応 |
|---|---|---|---|
| PRISM | モデルの遷移確率による実行経路の確率 | 経路生成時の遷移選択確率 | 固定 Q による経路重点サンプリング |
| 決定論的微分方程式 | 初期値・パラメータの入力分布 | 初期値・パラメータの選択分布 | 入力重点サンプリング |
| 確率微分方程式 | 入力分布とノイズ過程 | まず入力分布、後にノイズ過程 | 段階的に対応 |
| AWSIM | シナリオ・パラメータの入力分布 | 危険境界を多く選ぶ入力分布 | 固定P・固定Qの入力重点サンプリングを段階的に追加。詳細はAWSIM設計書 |
| BBSL | データ・条件の母集団分布 | データ・条件を選ぶ分布 | 標本選択の重み付け |

共通するのは標本追跡の契約であり、`log_p` と `log_q` の計算方法は target ごとに異なる。各 target の `sampling.py` または同等の adapter がこの計算と入力検証を担当する。

- PRISM では、実行経路の各遷移の確率を用いて経路全体の `log_p` と `log_q` を計算する。
- 決定論的微分方程式と AWSIM では、選んだ入力値・パラメータの密度から計算する。
- 確率微分方程式では、最初は入力分布のみを扱う。ノイズ過程を変更する重要度サンプリングは、別の高度な対応として追加する。

## モジュール配置と依存方向

```text
orchestration/
  sampling_plan.py              # 共通外枠の設定読込
  prism_importance_sampling.py  # 固定P・固定QのTestCase発行と固定標本比較
  proposal_updates.py           # 拡張: proposed / approved / applied の状態遷移

targets/prism/
  sampling.py                   # 経路ごとの log P / log Q
  proposal.py                   # 拡張: 人が定義した範囲内で候補Qを構成
targets/dynamics/
  sampling.py                   # 拡張: 入力分布の log P / log Q

evaluation/
  importance_samples.py         # 利用可能な重み付き標本の共通検証・抽出
  importance_sampling.py        # 通常IS / 自己正規化IS の推定・区間・診断
  importance_sampling_comparison.py # PRISMでは通常ISを既定に通常標本と比較
  weighted_sequential.py        # 拡張: 重み付き逐次判定

runtime/repository/
  proposal_history.py           # 拡張: Qの更新履歴の永続化
  importance_sampling_results.py # 通常・候補別の標本と比較成果物の永続化
```

- `apps/cli/` は `--sampling-plan`、seed、標本数などの引数を受け、orchestrationを呼ぶだけにする。統計計算やQの解釈を持たない。標準運用は `--proposal-mode fixed_samples --max-samples N` である。
- `targets/*/sampling.py` は `TestCase.input` と target 固有定義を読み、標本生成と `log_p`・`log_q` の計算を行う。
- `targets/prism/proposal.py` は、`proposal_search` にある可変遷移、候補値、制約を検証し、その範囲内だけで候補Qを構成する。ESS、失敗標本数、重みの偏りは候補の比較・順位付けに用いる。可変範囲が未定義、または重みを計算できないモデルでは候補を生成しない。候補Qの意味はPRISM固有なので、orchestrationへ置かない。
- `RawRunResult.meta.sampling` から `EvaluationRecord.meta.sampling` への転記は、backend と result interpreter の責務として行う。
- `evaluation/` は `EvaluationRecord` に記録された重みだけを消費し、target 名、設定パス、環境変数を直接参照しない。
- `evaluation/importance_samples.py` は、`importance_weight_status: available`、有限の対数重み、[0,1] の指標値を一度だけ検証して各評価器へ渡す。固定時点推定器と逐次判定器で検証規則を複製しない。
- PRISM は正規化済みの P と経路ごとの厳密な P/Q を持つため、比較器の既定は通常重要度推定 `(1/n)Σ(P/Q)Y` とする。自己正規化推定は、P の正規化定数が得られない対象で明示的に選ぶ補助手法である。
- Wilson区間と重み付き正規近似区間は診断用に保存するが、両者の幅を効率比較に用いない。比較器は通常標本を `W=1`、重点標本をtarget adapterが記録した `importance_weight_upper_bound=W` として、同じ固定時点Hoeffding区間を両側へ適用する。`Q=P`なら入力列・重み・比較区間が一致する。
- 次の標本を追加するか、Qの変更を提案・適用するかは `orchestration/` が判断する。
- `runtime/repository/` は更新履歴、通常標本run、候補別の重点標本run、評価reportを保存するだけで、Qの内容や診断指標の意味を判断しない。
- `orchestration/proposal_updates.py` は、target側が作った候補Qを提案・承認・適用する。target固有のQ生成規則はここへ入れない。

## PRISM 実験 runner の接続設計

### 入力と出力

CLIは設定ファイルの場所と実行条件だけを受け取る。

```text
apps/cli/importance_main.py          (prism_importance_main.py も同じ)
  --sampling-plan sampling_plan.yaml   ← target: prism で PRISM の plugin を選ぶ
  --sampling-seed 17
  --stop-mode fixed_samples | importance_width | weighted_sequential
  --max-samples N
        ↓
targets/prism/importance_sampling.py (PrismImportanceSamplingSupport → Runner)
```

対象ごとの実験の組み立ては、各対象の plugin の `importance_sampling`（`contracts/importance_sampling_plugin.py`）が受け持つ。新しい対象は `targets/<name>/` に `add_arguments` と `run` を持つクラスを置けば、共通の CLI から使える。

`sampling_plan.yaml` はPとQの正本である。更新方針は拡張を使う場合だけ追加する。orchestrationは共通項目だけを読み、`proposal.definition` のPRISM固有の意味は `targets/prism/sampling.py` が実行直前に検証する。

通常PRISM実行では `--sampling-plan` を省略できる。既存の `sample_path` と `model_check` の振る舞いは変更しない。

### 通常標本と重点標本の分離

比較実験は、同じP、同じモデル定数、同じhorizon、同じ二値指標、同じ信頼水準で、通常標本runと重点標本runを別に実行する。

```text
orchestration
  ├─ ordinary TestCase群       → Pに従う通常標本
  └─ importance TestCase群     → Qに従う重点標本
                                  ↓
                         targets/prism/sampling.py
                                  ↓
                          log P / log Q / log(P/Q)
```

orchestrationはrunを区別する識別子だけを付与する。ディレクトリ作成、JSONL、比較JSONの保存は `runtime/repository/importance_sampling_results.py` が担当する。候補探索では、重点標本を候補ごとの保存先へ分離する。

### 比較と停止

標本の保存後、orchestrationは `evaluation/` を呼び出す。

```text
ordinary EvaluationRecord群
  └─ importance_sampling_comparison.py
importance EvaluationRecord群
  ├─ importance_sampling.py
  └─ weighted_sequential.py
```

比較結果は、次を含む。

- 診断用 (`diagnostic`)：通常推定値とWilson区間、重点推定値と正規近似区間、ESS、除外した記録の件数と理由 (`excluded_records`)。Q候補の比較にはこの区間だけを使う。
- 保証の確認用 (`guarantee`)：重み上限Bを持つQについて、固定時点のHoeffding区間と経験的Bernstein区間。区間が真値を含むかの確認だけに使い、幅でQを順位付けしない。`comparable` は互換のために残した `guarantee.hoeffding` と同じ内容である。
- 目標幅への到達標本数（Hoeffding、`--target-width` 指定時）
- target・case kind・target measureが一致するか。記録が `target_fingerprint` / `proposal_fingerprint` を持つ場合は、Pの一致と群内でQが1つであることも検査し、`identity_check` に `fingerprint` か `legacy_ids_only` を記録する。

重みは対数領域で集計する。厳密なゼロ重み (`weight_kind: zero`) はNに含める。通常ISの点推定は1を超えても切り詰めない。事象を1件も観測しない場合、または標本が1件の場合は、正規近似区間を出さず `information: insufficient` とする（以前は幅0の区間 [0, 0] になっていた）。

停止方法は、固定標本比較の後に必要性が生じた場合の拡張例である。今回の標準運用は `fixed_samples` とする。

| stop mode | 評価器 | 用途 |
|---|---|---|
| `fixed_samples` | なし | 指定件数を集めて比較する初回検証 |
| `importance_width` | `StandardImportanceSamplingService`（PRISM） | 固定時点の目標区間幅で停止（反復確認の95%保証には使わない） |
| `weighted_sequential` | `WeightedSequentialService` | 重み上限を前提に逐次判定 |

初回の接続では `fixed_samples` を使う。推定値、重み、ESSを確認してから、区間幅停止と逐次判定を有効化するかを別途判断する。

固定Qを承認した後だけ、CLIから `--stop-mode weighted_sequential` を選べる。`sampling_plan.yaml` の `sequential_decision.null_upper` と `alternative_lower` に、判定したい二つの確率境界を人間が置く。`--max-samples` は必ず安全上限として指定する。CLIの同名オプションは一時的な上書きだけに使う。runnerは重点標本だけを一件ずつ追加し、既存の `WeightedSequentialService` が `accept_null`、`accept_alternative`、`continue` を返す。`suggest` 経路はこの停止処理も Q の承認・適用処理も呼ばない。

### Q の探索・更新フロー

```text
sampling_plan.yaml
  └─ 人間が定義した proposal_search
       （可変遷移・候補値・安全制約）
  ↓
targets/prism/proposal.py
  └─ 許可範囲内の候補Qだけを構成
  ↓
小規模な比較実験・診断
  └─ EvaluationRecord群（推定値、ESS、重み、区間）
  ↓
orchestration/proposal_updates.py
  ├─ suggest          : proposedのみ保存
  ├─ human_approved   : proposed → approved → applied
  └─ auto             : proposed → applied
       ↓
runtime/repository/proposal_history.py
       ↓
次のimportance TestCaseに proposal_id / proposal_version を付与
```

Qを更新しても、target measure P、PRISMモデル定数、変更対象外の遷移、既存標本の重みは変更しない。更新後のQは新しい `proposal_version` を持ち、それ以降に発行する標本だけへ適用する。

## 実装順序

1. `orchestration/sampling_plan.py` に、追跡項目だけを固定した `SamplingPlan` 読込を追加する。target 固有の `definition` は保持するが、ここでは検証しない。
2. `RawRunResult.meta.sampling` と `EvaluationRecord.meta.sampling` に、条件付き必須の共通追跡形式を追加する。`EvaluationRecord.meta` の既存必須キーである `schema_version` と `source_module` は維持する。
3. PRISM に、固定 Q による経路標本生成と、経路ごとの `log_p`・`log_q` 計算を実装する。
4. `evaluation/importance_sampling.py` に、重み付き推定値と信頼区間を実装する。`importance_weight_status: available` の標本だけを受け付ける。P/Qを厳密に計算でき、Pが正規化済みのPRISM経路では通常重要度推定を主推定器とする。自己正規化重点サンプリングは、正規化定数が不明なtarget向けの別推定器として保存する。正規近似区間は固定時点の効率診断であり、逐次停止・承認保証には使わない。
5. PRISM・Dynamicsの通常標本と重点サンプリング標本で、推定値と区間を比較する。比較は同じP、同じ二値指標、同じ信頼水準で行い、`W=1` とtarget記録の重み上限を使う共通Hoeffding区間の重なり、区間幅、目標幅への到達標本数を記録する。Qによる改善は保証せず、実測で判断する。
6. `runtime/repository/importance_sampling_results.py` を追加し、通常標本run、重点標本run、比較結果を永続化する。

次は必須範囲の後に選べる拡張例である。

7. 通常 SPRT と分離した、重み付き標本用の逐次判定を `evaluation/weighted_sequential.py` として使う。重要度重みの上限を前提にした時点一様なHoeffding信頼区間であり、`upper <= null_upper` または `lower >= alternative_lower` の場合だけ停止する。固定時点Hoeffding区間を別評価器として重複実装しない。
8. `contracts/sampling.py` の `ProposalCandidate`、候補別の標本保存先、`candidate_evaluated` 追記イベントを使い、`targets/prism/proposal.py` が構成した候補Qを `suggest` モードでpilot評価・履歴保存する。ESSなどの可変な診断値は `StatisticalReport.diagnostics` に置き、新しい共通contractの必須項目にしない。
9. `human_approved`、次に `auto` を `orchestration/proposal_updates.py` へ接続する。`auto` は人間が定義した候補集合・評価規則・安全制約の範囲内に限定する。
10. 微分方程式、AWSIMなどへの適用は、対象ごとの P が研究上定義できた場合に追加する。

## 受入条件

- `sampling` を持つ標本から、使用した `sampling_plan.yaml`、`target_measure_id`、`sampling_mode` を特定できる。
- `manual_proposal` または `adaptive` の標本から、`proposal_id` を特定できる。`adaptive` の標本では `proposal_version` も特定できる。
- `importance_weight_status: available` の標本について、`log_p`、`log_q`、`log_importance_weight` を確認できる。
- `importance_weight_status: available` の標本だけが重み付き評価へ渡される。
- Q の変更前後で、どの標本がどの proposal version に属するか分かる。
- P の定義と Q の定義が設定上も実装上も分離されている。
- 重み付き標本を既存の非重み付き SPRT に投入しない。
- `evaluation/` が target 名、target 固有設定パス、環境変数を直接参照しない。
- target 固有の `definition` は、その target が実行直前に検証する。
- Qの更新は `proposed`、`approved`、`applied` の追記型履歴として残る。`suggest` は適用せず、`human_approved` は承認後のみ適用し、`auto` は候補記録後に自動適用する。
- `proposal_search` がない場合、Q候補の自動生成を実行しない。
- 生成したQは、人間が定義した可変遷移・候補値・制約から逸脱しない。変更対象外の遷移確率とPの定義は不変である。
- `auto` は、人間が定義した候補集合・評価規則・安全制約の範囲内でのみQを適用する。
- 候補比較の可変な診断値は `StatisticalReport.diagnostics` または `target_diagnostics` に保存し、共通contractの必須項目へ昇格させない。
- `suggest` の候補標本は候補ごとに分離して保存し、`candidate_evaluated` と `proposed` の追記だけを行う。承認・適用は別の履歴イベントとして残す。
- CLIは統計計算、Qの確率計算、Q候補の生成を直接行わない。
- Q候補の生成は `targets/prism/proposal.py`、比較結果とrun成果物の保存は `runtime/repository/` が担当する。
