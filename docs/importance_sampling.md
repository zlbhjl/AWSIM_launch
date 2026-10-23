# 重点サンプリング (固定P・固定Q) の使い方

Using importance sampling with fixed P and fixed Q. **[English version below ↓](#english)**

稀な事象 (衝突・故障) の確率を少ない標本で推定するため、評価したい元の分布 **P** を固定したまま、
人間が設定した提案分布 **Q** で標本を生成し、各標本に P/Q の重みを記録します。
対象は PRISM、連続ダイナミクス (ODE/SDE)、AWSIM です。

- 現在の範囲は、**固定P・人手で指定した固定Q** での標本生成と、通常の P 標本との固定標本比較です。
  Q の自動更新や停止判定は扱いません。
- P/Q の定義、標本の追跡、比較の考え方は [importance_sampling_design.md](importance_sampling_design.md)、
  AWSIM の入力重点サンプリングは [awsim_importance_sampling_design.md](awsim_importance_sampling_design.md) を参照してください。

## PRISM重点サンプリング（固定P・固定Q）

PRISM の `simple_reliability_dtmc` では、元のモデル遷移確率を **P** として固定したまま、人間が設定した提案遷移確率 **Q** で経路を生成できます。各経路に `log_p`、`log_q`、`log_importance_weight = log(P/Q)` を記録し、通常重要度推定でP上の故障確率を推定します。

標準運用は固定標本比較です。Qによる効率は、通常P標本との推定値、**同じ固定時点Hoeffding区間による区間幅**、ESS、最大重みを同じ標本数で比べて人間が判断します。Pのモデル確率を変更したり、Qを自動採用したりはしません。

```bash
# Q1で100件の重点標本と通常P標本を比較する
python3 -m apps.cli.prism_importance_main \
  --sampling-plan configs/prism_rare_event_q1.yaml \
  --output-root artifacts/prism_rare_event_q1_fixed_100 \
  --proposal-mode fixed_samples \
  --max-samples 100 \
  --sampling-seed 20261009
```

結果は `ordinary/records.jsonl`、`importance/records.jsonl`、`comparison.json` に保存されます。`comparison.json` の重点側は、PRISMでP/Qを厳密に計算できるため、自己正規化版ではなく通常重要度推定を既定として使います。

停止判定、候補Qの `suggest`、承認・自動更新、BBSLなどへのadapter追加は将来の拡張例です。AWSIMの固定P・固定Q入力重点サンプリングは設計済みですが、実装・実験の完了条件は [AWSIM入力重点サンプリング設計](awsim_importance_sampling_design.md) に分離しています。

## 微分方程式の入力重点サンプリング（固定P・固定Q）

U-turn ODEでは、ODEの式・キャリブレーション・ノイズ設定を変更せず、`dx0`、`ego_speed`、`npc_speed` の**入力分布**をP/Qとして扱います。Pは評価したい元の一様入力分布、Qは人間が設定する標本生成用の分布です。QはPの全範囲を残す混合分布に限定しているため、入力ごとの `log_p`、`log_q`、`log_importance_weight = log(P/Q)` を正確に記録できます。

実装済みの範囲は、固定P・人手指定の固定Q・指定件数の標本生成・P/Q重み付き比較です。SDEでは、Euler--MaruyamaのBrownian増分について、人間指定のP/Q Gaussian drift schedule（直接Qまたはdefensive Gaussian Q）を評価し、入力とノイズ経路を合わせたP/Q重みを記録します。拡散係数の変更やQの自動更新は扱いません。数式と設定は[重点サンプリング設計のSDE節](importance_sampling_design.md#sdeノイズ経路の重点サンプリング)を参照してください。

### 1. まず Q=P で計算経路を確認する

`configs/dynamics_importance_baseline.yaml` はQをPと同一にした検証用設定です。同じseedでは通常標本と重点標本の入力列が一致し、各標本の対数重みは0、ESSは標本数になります。

```bash
python3 -m apps.cli.dynamics_importance_main \
  --sampling-plan configs/dynamics_importance_baseline.yaml \
  --output-root artifacts/dynamics_q_equals_p \
  --max-samples 100 \
  --sampling-seed 42
```

### 2. 人間が設定した Q を固定標本で比較する

例えば `configs/dynamics_importance_q4.yaml` は、衝突が起きやすいと考えた入力領域へ `dx0`、ego速度、NPC速度を偏らせるQです。Pは変更されません。

```bash
python3 -m apps.cli.dynamics_importance_main \
  --sampling-plan configs/dynamics_importance_q4.yaml \
  --output-root artifacts/dynamics_importance_q4 \
  --max-samples 1000 \
  --sampling-seed 20261011
```

結果は次に保存されます。

- `ordinary/records.jsonl`: Pで生成した通常標本
- `importance/records.jsonl`: Qで生成した重み付き標本
- `comparison.json`: 推定値、ESS、最大重み、区間幅、標本数

比較器は `target`、`case_kind`、`target_measure_id` の一致を検査するため、異なる対象やPを誤って比較しません。`comparison.json` には二つの目的の区間を分けて保存します。

- `diagnostic`: Q候補の効率比較用。重点推定の正規近似区間幅、ESS、複数seedでの推定値のばらつきを見る。事象を1件も観測しない場合は区間を出さず `information: insufficient` とする。
- `guarantee`: 重み上限を用いた固定時点のHoeffding区間と経験的Bernstein区間。採用後の固定Qについて、区間が真値を含むかを確認するためだけに使う。`comparable` は互換のために残した `guarantee.hoeffding` と同じ内容。

重み上限が大きいQは保証用の区間では不利に見え、稀な事象では現実的なNで区間幅が対象確率より桁違いに広くなるため、**Q候補の順位付けに `guarantee` / `comparable` の区間幅は使いません**。

### 3. 複数の人手候補を同じ条件で比べる

候補Qごとに同じ外側seed集合・同じ標本数で実行します。外側seedから各標本のseedを重複しない範囲へ写像するため、別seed実験の標本は重なりません。

```bash
for candidate in q2 q3 q4; do
  for seed in $(seq 1 20); do
    python3 -m apps.cli.dynamics_importance_main \
      --sampling-plan "configs/dynamics_importance_${candidate}.yaml" \
      --output-root "artifacts/dynamics_candidate_ranking/${candidate}/seed_${seed}" \
      --max-samples 100 \
      --sampling-seed "${seed}"
  done
done
```

実行例では、Q2・Q3・Q4を20 seed × 各100標本で比較しました。診断用区間幅の平均はQ4が `0.02125`、Q3が `0.02476`、Q2が `0.02677` でした。一方で平均ESSはQ4が約`82.5`、Q3が約`83.7`、Q2が約`87.2` です。この結果はQ4を**次に人間が検討する候補**とする根拠であり、自動承認・自動適用を意味しません。

### 4. U ターン以外の SDE モデル（スカラー OU）

`dynamics_schema_version: 2` のプランでは、登録されたモデル（現在は `ou_scalar`）を同じ CLI で実行できます。target 側にモデル・初期値・刻み幅・事象を書き、Q はノイズ経路だけを変えます。真の確率が式で分かるので、推定と区間の検証に使えます。

```bash
python3 -m apps.cli.dynamics_importance_main \
  --sampling-plan configs/dynamics_importance_ou_gaussian_q1.yaml \
  --output-root artifacts/ou_q1 \
  --max-samples 1000 \
  --phase final
```

v2 プランは `manifest.json` に P・Q・乱数の root・予定の N を記録し、同じコマンドで中断した標本から再開します（設定が違えば停止します）。失敗した標本は `--retry-failed` で同じ乱数のまま再試行し、未解決の標本が残る場合 `comparison.json` は `final_status: incomplete` になります。設計と検証結果は [SDE 重点サンプリング実装計画](sde_importance_sampling_plan.md) を参照してください。

### 5. 提案分布の種類と混合

v2 プランでは、Q を `proposal.definition.noise` に書けます。平均・共分散などは innovation（標準化した Brownian 増分）の単位です。既存の `noise_path_q`（SDE ドリフトの単位）も引き続き使えます。

| `kind` | 内容 |
|---|---|
| `gaussian` | 平均の規則と、チャネル名つきの共分散（モード別も可）。正定値でない行列は拒否し、勝手に補正しない |
| `student_t` | 自由度 `dof`（既定 5）、位置の規則、尺度行列 `scale`。`covariance` を書くと、その共分散になるよう尺度を決める |
| `laplace` | チャネルごとに独立な Laplace |
| `mixture` | 経路ごとに成分を 1 回選ぶ混合。`{kind: target}` で P 自身を成分にでき、`defensive_weight`（β）を書くと重みは 1/β 以下になり、保証用の区間が使える |

平均の規則は `constant`、`piecewise`（時刻・モードの区分。区分ごとに別の規則も置ける）、`state_threshold`、`affine_state` です。書き方の例は [重点サンプリング設計](importance_sampling_design.md#提案分布innovation-の法則) を参照してください。

### 6. Q の調整と固定

候補の Q を pilot で比べて記録し、選んだものを最終プランとして固定します。pilot は最終実行と別の乱数系列を使います。

```bash
python3 -m tools.validation.is_tuning run \
  --base-plan configs/is_tuning/ou_a_3sd_upper/base.yaml \
  --candidates configs/is_tuning/ou_a_3sd_upper/q1.yaml \
  --output-dir artifacts/is_tuning/ou_a_3sd_upper/q1 --samples 1000 --root-entropy 1
python3 -m tools.validation.is_tuning fix \
  --output-dir artifacts/is_tuning/ou_a_3sd_upper/q1 --trial q1_s1.0 \
  --plan-out configs/my_final_q1.yaml
```

`configs/is_tuning/` にモデル A・B の候補と pilot の記録、`configs/is_final/` に固定した最終プランがあります。

### 7. そのほかのモデルと保存の方式

- 登録モデルは `ou_scalar`（スカラー OU）、`ou_augmented`（状態を拡大した OU、ノイズは 1 本）、`nonlinear2d`（非線形のドリフトと状態依存の拡散）、`uturn` です。U ターンも v2 プランなら同じ Q を使えます（Q = P なら従来の判定と一致します）。
- `--artifacts summary` は、軌道をファイルに書かずにメモリ内で判定し、判定した記録だけを保存します。
- 真の値が分からないモデルは、`tools.validation.compare_to_reference` で、独立な参照推定と比べます。

---

<a id="english"></a>

# English: Using importance sampling (fixed P, fixed Q)

To estimate the probability of rare events (collisions, failures) with fewer samples, samples are generated from a human-specified
proposal distribution **Q** while the original distribution **P** to be evaluated stays fixed, and each sample records its P/Q weight.
The targets are PRISM, continuous dynamics (ODE/SDE), and AWSIM.

- The current scope is sample generation with **fixed P and a hand-specified fixed Q**, and fixed-sample comparison with ordinary
  P samples. Automatic updates of Q and stopping rules are not supported.
- See [importance_sampling_design.md](importance_sampling_design.md) for the definition of P/Q, sample tracking, and how comparisons
  work, and [awsim_importance_sampling_design.md](awsim_importance_sampling_design.md) for input importance sampling on AWSIM
  (both Japanese only).

## PRISM importance sampling (fixed P, fixed Q)

For PRISM's `simple_reliability_dtmc`, paths can be generated with human-specified proposal transition probabilities **Q** while the
original model transition probabilities **P** stay fixed. Each path records `log_p`, `log_q`, and
`log_importance_weight = log(P/Q)`, and the failure probability under P is estimated with the ordinary importance sampling estimator.

The standard workflow is a fixed-sample comparison. A human judges the efficiency of Q by comparing, at the same sample size, the
estimate, **the width of the same fixed-time Hoeffding interval**, the ESS, and the maximum weight against ordinary P samples. The model
probabilities of P are never changed, and Q is never adopted automatically.

```bash
# Compare 100 importance samples from Q1 with ordinary P samples
python3 -m apps.cli.prism_importance_main \
  --sampling-plan configs/prism_rare_event_q1.yaml \
  --output-root artifacts/prism_rare_event_q1_fixed_100 \
  --proposal-mode fixed_samples \
  --max-samples 100 \
  --sampling-seed 20261009
```

Results are saved in `ordinary/records.jsonl`, `importance/records.jsonl`, and `comparison.json`. Because PRISM can compute P/Q
exactly, the importance side of `comparison.json` uses the ordinary importance sampling estimator by default, not the self-normalized
one.

Stopping rules, `suggest` for candidate Q, approval and automatic updates, and adapters for BBSL and other targets are possible future
extensions. Fixed-P / fixed-Q input importance sampling for AWSIM has been designed; its implementation and experiment completion
criteria are described separately in the [AWSIM input importance sampling design](awsim_importance_sampling_design.md)
(Japanese only).

## Input importance sampling for differential equations (fixed P, fixed Q)

For the U-turn ODE, the **input distribution** of `dx0`, `ego_speed`, and `npc_speed` is treated as P/Q, without changing the ODE
equations, calibration, or noise settings. P is the original uniform input distribution to be evaluated, and Q is a human-specified
distribution for generating samples. Q is restricted to mixtures that keep the whole support of P, so `log_p`, `log_q`, and
`log_importance_weight = log(P/Q)` can be recorded exactly for each input.

The implemented scope is fixed P, a hand-specified fixed Q, generating a given number of samples, and comparison with P/Q weights.
For the SDE, a human-specified P/Q Gaussian drift schedule (direct Q or defensive Gaussian Q) is evaluated on the Brownian increments
of Euler–Maruyama, and the P/Q weight of the inputs and the noise path together is recorded. Changing the diffusion coefficients and
updating Q automatically are not supported. See the SDE section of the [importance sampling design](importance_sampling_design.md)
(Japanese only) for the formulas and settings.

### 1. First check the computation path with Q = P

`configs/dynamics_importance_baseline.yaml` is a check configuration in which Q is identical to P. With the same seed, the input
sequences of the ordinary and importance samples match, the log weight of every sample is 0, and the ESS equals the sample size.

```bash
python3 -m apps.cli.dynamics_importance_main \
  --sampling-plan configs/dynamics_importance_baseline.yaml \
  --output-root artifacts/dynamics_q_equals_p \
  --max-samples 100 \
  --sampling-seed 42
```

### 2. Compare a human-specified Q on fixed samples

For example, `configs/dynamics_importance_q4.yaml` is a Q that biases `dx0`, the ego speed, and the NPC speed toward the input region
considered likely to collide. P is not changed.

```bash
python3 -m apps.cli.dynamics_importance_main \
  --sampling-plan configs/dynamics_importance_q4.yaml \
  --output-root artifacts/dynamics_importance_q4 \
  --max-samples 1000 \
  --sampling-seed 20261011
```

Results are saved as follows.

- `ordinary/records.jsonl`: ordinary samples generated from P
- `importance/records.jsonl`: weighted samples generated from Q
- `comparison.json`: estimates, ESS, maximum weight, interval widths, sample sizes

The comparator checks that `target`, `case_kind`, and `target_measure_id` match, so different targets or different P are never
compared by mistake. `comparison.json` stores intervals for two purposes separately.

- `diagnostic`: for comparing the efficiency of candidate Qs. Look at the normal-approximation interval width of the importance
  estimate, the ESS, and the spread of estimates over several seeds. If no event is observed, no interval is reported and
  `information` is `insufficient`.
- `guarantee`: fixed-time Hoeffding and empirical Bernstein intervals using the weight bound. Used for a fixed Q once it is adopted,
  only to check that the interval contains the true value. `comparable` is kept for compatibility and equals `guarantee.hoeffding`.

A Q with a large weight bound looks worse on these intervals, and for rare events they are orders of magnitude wider than the target
probability at a realistic N, so **the width of the `guarantee` / `comparable` intervals is not used to rank candidate Qs**.

### 3. Compare several hand-written candidates under the same conditions

Run each candidate Q with the same set of outer seeds and the same sample size. The outer seed is mapped to non-overlapping ranges of
per-sample seeds, so samples from experiments with different seeds never overlap.

```bash
for candidate in q2 q3 q4; do
  for seed in $(seq 1 20); do
    python3 -m apps.cli.dynamics_importance_main \
      --sampling-plan "configs/dynamics_importance_${candidate}.yaml" \
      --output-root "artifacts/dynamics_candidate_ranking/${candidate}/seed_${seed}" \
      --max-samples 100 \
      --sampling-seed "${seed}"
  done
done
```

In this example, Q2, Q3, and Q4 were compared with 20 seeds × 100 samples each. The mean diagnostic interval width was `0.02125` for
Q4, `0.02476` for Q3, and `0.02677` for Q2. The mean ESS, on the other hand, was about `82.5` for Q4, `83.7` for Q3, and `87.2` for Q2.
This result supports Q4 as **the next candidate for a human to consider**; it does not mean automatic approval or adoption.

### 4. SDE models other than the U-turn (scalar OU)

A plan with `dynamics_schema_version: 2` runs a registered model (currently `ou_scalar`) through the same CLI. The target side names
the model, the initial value, the step size and the event; Q changes only the Brownian path. The true probability is known in closed
form, so the model is useful for checking estimates and intervals.

```bash
python3 -m apps.cli.dynamics_importance_main \
  --sampling-plan configs/dynamics_importance_ou_gaussian_q1.yaml \
  --output-root artifacts/ou_q1 \
  --max-samples 1000 \
  --phase final
```

A v2 plan records P, Q, the random-number root and the planned N in `manifest.json`, and the same command resumes from the samples
that are missing (it stops if the settings differ). `--retry-failed` retries failed samples with the same random numbers; while any
sample is unresolved, `comparison.json` has `final_status: incomplete`. The design and the validation results are in the
[SDE importance-sampling implementation plan](sde_importance_sampling_plan.md) (Japanese only).

### 5. Proposal families and mixtures

In a v2 plan, Q can be written as `proposal.definition.noise`. Means and covariances are in innovation units (the standardised
Brownian increment). The existing `noise_path_q` (SDE drift units) keeps working.

| `kind` | Contents |
|---|---|
| `gaussian` | a mean rule and a covariance over named channels (optionally per mode). A matrix that is not positive definite is rejected, never adjusted |
| `student_t` | degrees of freedom `dof` (default 5), a location rule, and a scale matrix `scale`. With `covariance`, the scale is set so that the law has that covariance |
| `laplace` | independent Laplace per channel |
| `mixture` | a component chosen once per path. `{kind: target}` makes P itself a component; with `defensive_weight` (beta) the weight is at most 1/beta and the guarantee intervals are available |

Mean rules are `constant`, `piecewise` (time/mode windows; a window may hold another rule), `state_threshold` and `affine_state`.
See the [importance sampling design](importance_sampling_design.md) (Japanese only) for examples.

### 6. Tuning and fixing Q

Compare candidate Qs in pilot runs, record them, and fix the chosen one as the final plan. Pilot runs use random streams separate
from the final runs.

```bash
python3 -m tools.validation.is_tuning run \
  --base-plan configs/is_tuning/ou_a_3sd_upper/base.yaml \
  --candidates configs/is_tuning/ou_a_3sd_upper/q1.yaml \
  --output-dir artifacts/is_tuning/ou_a_3sd_upper/q1 --samples 1000 --root-entropy 1
python3 -m tools.validation.is_tuning fix \
  --output-dir artifacts/is_tuning/ou_a_3sd_upper/q1 --trial q1_s1.0 \
  --plan-out configs/my_final_q1.yaml
```

`configs/is_tuning/` holds the candidates and pilot records for models A and B, and `configs/is_final/` the fixed final plans.

### 7. Other models and storage

- The registered models are `ou_scalar` (scalar OU), `ou_augmented` (augmented-state OU with one noise channel), `nonlinear2d`
  (nonlinear drift and state-dependent diffusion) and `uturn`. With a v2 plan the U-turn takes the same proposals (with Q = P it
  reproduces the existing result).
- `--artifacts summary` judges trajectories in memory without writing them and stores only the judged records.
- Models without a known truth are compared with an independent reference by `tools.validation.compare_to_reference`.
