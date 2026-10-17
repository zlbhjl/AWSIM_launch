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

- `diagnostic`: Q候補の効率比較用。重点推定の正規近似区間幅、ESS、複数seedでの推定値のばらつきを見る。
- `comparable`: 重み上限を用いた固定時点Hoeffding区間。採用後の固定Qについて、保証を確認するために使う。

重み上限が大きいQはHoeffding区間だけでは不利に見えるため、**Q候補の順位付けに `comparable` の区間幅は使いません**。

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
  estimate, the ESS, and the spread of estimates over several seeds.
- `comparable`: a fixed-time Hoeffding interval using the weight bound. Used to check the guarantee for a fixed Q once it is adopted.

A Q with a large weight bound looks worse on the Hoeffding interval alone, so **the width of the `comparable` interval is not used to
rank candidate Qs**.

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
