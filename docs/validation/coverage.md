# 統計評価の被覆率の検証

Coverage validation of the statistical evaluation on a target with known truth. **[English version below ↓](#english)**

## 目的

95% 信頼区間は「実験全体をくり返すと、95% 以上の回で真の値を含む」という約束です。AWSIM では真の衝突確率が分からないため、
この約束が守られているかを確かめられません。そこで、真の確率・分位点・平均が式で分かる対象 `bernoulli`
([targets/bernoulli/](../../targets/bernoulli/)) で、実験全体 (標本を集める → 止めるかを判断する → 最終の区間) を 2,000 回くり返し、
結果が正しかった割合 (被覆率) を測りました。統計評価と停止判定はすべての対象で共通なので、この結果は AWSIM・PRISM・dynamics にも当てはまります。

## 方法

- 道具: `tools/validation/coverage_experiment.py`。実験で実際に使う停止判定のコードをそのまま動かし、データの保存先だけをメモリに置き換えます。
  - 経路 `iid`: IID 型の対象 (dynamics、bernoulli) と `--stopping-rule` の戦略
  - 経路 `dkw_runner`: AWSIM と PRISM の `--mode dkw` / `dkw_fixed` が使う `DKWModeRunner`
- 評価のタイミング: 標本が 1 つ届くたびに評価します。これは CLI を `--queue-high-water 1` で動かした場合と完全に一致します (同じ seed で確認)。
  既定の CLI は `worker 数 × 4` 件ずつ発行するため、評価の回数は少なく、止めたあとに数件多く集めます。
- 真の値: 失敗確率 p、指数分布 (λ=1) の 5% 点 −ln(0.95) ≈ 0.0513、Beta(2,5) の平均 2/7。
- 判定: 区間が真の値を含むか (二項・DKW)、間違った仮説を採択しなかったか (SPRT)、相対誤差が ε 以内か (EBStop)。
- くり返し: 各条件 2,000 回。表の [ ] は被覆率そのものの 95% Clopper-Pearson 区間です。
- 実施: 2026-10-18 (コミット `d9028e8` 以降のコード)。

## 結果

### legacy の統計モード (今の既定)

| 条件 | 正しかった割合 | 平均の標本数 |
|---|---|---|
| 二項・Wilson、n=100 で 1 回だけ評価 (p=0.05) | 97.4% [96.6, 98.1] | 100 |
| 二項・Clopper-Pearson、n=100 で 1 回だけ評価 (p=0.05) | 98.7% [98.1, 99.1] | 100 |
| 二項・Wilson、標本ごとに評価し幅 0.05 で停止 (p=0.05) | 94.95% [93.9, 95.9] | 283 |
| 二項・Clopper-Pearson、同上 | 95.5% [94.5, 96.4] | 311 |
| 二項・Clopper-Pearson + `--binomial-anytime-valid` | 100% [99.8, 100] | 1,826 |
| 二項・Wilson / Clopper-Pearson、幅 0.1 で停止 (p=0.2) | 95.3% / 96.1% | 242 / 262 |
| SPRT (H0: p=0.1、H1: p=0.05、α=β=0.05)、真の p=0.1 / 0.05 | 94.95% / 96.65% | 145 / 163 |
| EBStop (Beta(2,5) の平均、相対誤差 0.1) | 100% [99.8, 100] | 3,417 |
| DKW・`DKWModeRunner` の `dkw_fixed` (n=500、1 回だけ評価) | 100% [99.8, 100] | 500 |
| **DKW・`DKWModeRunner` の `dkw` (逐次、幅 0.15)** | **85.9%** [84.2, 87.3] | 272 |
| **DKW・IID の経路の `dkw` / `dkw_fixed` (逐次、幅 0.15)** | **82.6%** [80.9, 84.2] | 158 |

### 探索データを再利用した場合 (`--dkw-pure-smc` なし)

短い故障時間の側に偏った探索データ 300 件 (`u_time = v²`) を先に入れておきました。

| 条件 | 正しかった割合 | 新しく集めた標本数 |
|---|---|---|
| `dkw` (逐次)、探索データを KDE の重みで再利用 | **12.6%** [11.2, 14.1] | 0 |
| `dkw_fixed`、探索データを重みなしで混ぜる (新しい標本 500 件) | **5.0%** [4.1, 6.0] | 500 |
| `dkw` (逐次)、`--dkw-pure-smc` で探索データを無視 | 85.9% [84.2, 87.3] | 272 |

### `--stopping-rule` の新しい統計アルゴリズム

| ルール | 正しかった割合 | 平均の標本数 |
|---|---|---|
| `binomial_ci_fixed_strict` (n=100、p=0.05) | 98.7% [98.1, 99.1] | 100 |
| `binomial_ci_sequential_anytime` (幅 0.05、p=0.05) | 100% [99.8, 100] | 2,657 |
| `binomial_ci_sequential_anytime` (幅 0.1、p=0.2) | 100% [99.8, 100] | 2,158 |
| `dkw_fixed_strict` (n=1,000) | 100% [99.8, 100] | 1,000 |
| `dkw_sequential_anytime` (幅 0.15) | 100% [99.8, 100] | 1,132 |
| `sprt_wald`、真の p=0.1 / 0.05 | 94.95% / 96.65% | 145 / 163 |
| `ebstop_anytime` | 100% [99.8, 100] | 3,417 |

## 分かったこと

1. **1 回だけ評価する区間、SPRT、EBStop は約束どおりです。** 1 回だけ評価する二項区間の値は、理論値
   (n=100、p=0.05 で Wilson 96.6%、Clopper-Pearson 98.3%) と一致しました。
2. **途中で止めると、被覆率が下がります。** 二項区間でも約束の 95% ぎりぎりまで下がり (Wilson 94.95%)、
   DKW では 82.6〜85.9% まで下がります。標本ごとに区間を確かめ、狭くなった時点で止めるため、たまたま狭くなった区間が選ばれやすくなるからです。
3. **IID の経路の `dkw_fixed` は、名前に反して逐次評価です。** `dkw` と同じ処理で、`--max-samples` に達する前に止まります。
4. **legacy の DKW の区間は、標本が少ないと狭すぎます。** DKW の帯の幅 ε = √(ln(2/δ)/(2n)) が分位点の q より大きいとき、
   下端は「取りうる最小値」まで広がるべきですが、legacy は最も小さい標本の値で打ち切っています。新しいルールは正しく広げます。
5. **探索データを混ぜると、保証はほぼなくなります。** KDE の重みでも、この偏りはほとんど直せませんでした。
   しかも探索データだけで区間が十分狭く見えるため、新しい一様な標本を 1 つも取らずに止まりました。
6. **新しいルールは、すべて約束を満たしました。** 代わりに、逐次のルールは標本数が legacy の 7〜9 倍になります。
   100% は保守的すぎるので、少ない標本数で保証を保つ方法 (confidence sequence など) が今後の改良の候補です。
7. **(検証の途中で見つけた不具合)** IID の経路は、標本 i の乱数を `seed + i` で作っていたため、seed が 1 つ違うだけの実験が
   ほぼ同じ標本を使っていました (100 標本中 99 標本が共通)。現在は `orchestration/seeding.py` の `derive_seed` で直してあります。

## 使い分けの目安

| 目的 | 選ぶもの |
|---|---|
| 保証が必要で、標本数を事前に決められる | `--stopping-rule binomial_ci_fixed_strict` / `dkw_fixed_strict` (標本数は n ≥ ln(2/δ)/(2ε²) で決める) |
| 保証が必要で、途中で止めたい | `--stopping-rule binomial_ci_sequential_anytime` / `dkw_sequential_anytime` |
| 早さを優先し、近似でよい | legacy (既定) |
| 探索データを使う | 保証はない。結果は経験的な参考値として扱う |

`--stopping-rule` は、現在は IID 型の対象と PRISM の固定パラメータで使えます。AWSIM の探索戦略では、まだ使えません。

## 再現

```bash
python3 -m tools.validation.coverage_experiment --list
OMP_NUM_THREADS=1 python3 -m tools.validation.coverage_experiment --reps 2000 --workers 28 \
  --output-dir artifacts/coverage
```

各条件の結果は `artifacts/coverage/summary.json`、1 回ごとの結果は `runs.csv` に保存されます。
`tests/unit/tools/test_coverage_baseline.py` は、8 条件 × 3 seed の結果が変わっていないことを確かめます。

## 制約

- 評価は標本ごとに行う場合 (最も不利な場合) で測っています。クラスターでの評価の間隔や遅れの影響は測っていません。
- 探索データの偏りは 1 種類 (`u_time = v²`) だけです。KDE の効き方は偏り方によって変わります。
- 対象は単純な分布です。分布の形によって、特に有限標本での被覆率は変わりえます。

---

<a id="english"></a>

# English: Coverage validation of the statistical evaluation

## Purpose

A 95% confidence interval promises that, if the whole experiment is repeated, the interval contains the true value in at least 95% of
the repetitions. AWSIM cannot check this because the true collision probability is unknown. So on `bernoulli`
([targets/bernoulli/](../../targets/bernoulli/)), a target whose true probability, quantile, and mean are known exactly, the whole
experiment (collect samples → decide whether to stop → final interval) was repeated 2,000 times, and the fraction of correct results
(the coverage) was measured. The statistical evaluation and the stopping logic are shared by all targets, so the results also apply to
AWSIM, PRISM, and dynamics.

## Method

- Tool: `tools/validation/coverage_experiment.py`. It runs the production stopping logic and replaces only the dataset storage with an
  in-memory table.
  - Engine `iid`: IID-sampling targets (dynamics, bernoulli) and the `--stopping-rule` strategies
  - Engine `dkw_runner`: `DKWModeRunner`, used by `--mode dkw` / `dkw_fixed` on AWSIM and PRISM
- Evaluation timing: after every sample. This is identical to running the CLI with `--queue-high-water 1` (checked with the same
  seeds). By default the CLI issues `4 × workers` cases at a time, so it evaluates less often and collects a few extra samples after
  deciding to stop.
- Truth: the failure probability p, the 5% quantile of Exponential(λ=1), −ln(0.95) ≈ 0.0513, and the mean of Beta(2,5), 2/7.
- Correctness: the interval contains the truth (binomial, DKW), the wrong hypothesis was not accepted (SPRT), or the relative error is
  within ε (EBStop).
- Repetitions: 2,000 per condition. The brackets [ ] are 95% Clopper-Pearson intervals of the coverage itself.
- Run on 2026-10-18 (code from commit `d9028e8` on).

## Results

### Legacy statistical modes (the current default)

| Condition | Correct | Mean sample size |
|---|---|---|
| Binomial, Wilson, one evaluation at n=100 (p=0.05) | 97.4% [96.6, 98.1] | 100 |
| Binomial, Clopper-Pearson, one evaluation at n=100 (p=0.05) | 98.7% [98.1, 99.1] | 100 |
| Binomial, Wilson, evaluated after every sample, stop at width 0.05 (p=0.05) | 94.95% [93.9, 95.9] | 283 |
| Binomial, Clopper-Pearson, same | 95.5% [94.5, 96.4] | 311 |
| Binomial, Clopper-Pearson + `--binomial-anytime-valid` | 100% [99.8, 100] | 1,826 |
| Binomial, Wilson / Clopper-Pearson, stop at width 0.1 (p=0.2) | 95.3% / 96.1% | 242 / 262 |
| SPRT (H0: p=0.1, H1: p=0.05, α=β=0.05), true p=0.1 / 0.05 | 94.95% / 96.65% | 145 / 163 |
| EBStop (mean of Beta(2,5), relative error 0.1) | 100% [99.8, 100] | 3,417 |
| DKW, `DKWModeRunner` `dkw_fixed` (n=500, one evaluation) | 100% [99.8, 100] | 500 |
| **DKW, `DKWModeRunner` `dkw` (sequential, width 0.15)** | **85.9%** [84.2, 87.3] | 272 |
| **DKW, IID path `dkw` / `dkw_fixed` (sequential, width 0.15)** | **82.6%** [80.9, 84.2] | 158 |

### Reusing exploration data (without `--dkw-pure-smc`)

300 exploration rows biased toward short times to failure (`u_time = v²`) were present before sampling.

| Condition | Correct | New samples collected |
|---|---|---|
| `dkw` (sequential), exploration data reused with KDE weights | **12.6%** [11.2, 14.1] | 0 |
| `dkw_fixed`, exploration data mixed in unweighted (500 new samples) | **5.0%** [4.1, 6.0] | 500 |
| `dkw` (sequential), `--dkw-pure-smc` ignores the exploration data | 85.9% [84.2, 87.3] | 272 |

### New statistical algorithms (`--stopping-rule`)

| Rule | Correct | Mean sample size |
|---|---|---|
| `binomial_ci_fixed_strict` (n=100, p=0.05) | 98.7% [98.1, 99.1] | 100 |
| `binomial_ci_sequential_anytime` (width 0.05, p=0.05) | 100% [99.8, 100] | 2,657 |
| `binomial_ci_sequential_anytime` (width 0.1, p=0.2) | 100% [99.8, 100] | 2,158 |
| `dkw_fixed_strict` (n=1,000) | 100% [99.8, 100] | 1,000 |
| `dkw_sequential_anytime` (width 0.15) | 100% [99.8, 100] | 1,132 |
| `sprt_wald`, true p=0.1 / 0.05 | 94.95% / 96.65% | 145 / 163 |
| `ebstop_anytime` | 100% [99.8, 100] | 3,417 |

## Findings

1. **Intervals evaluated once, SPRT, and EBStop keep their promise.** The binomial intervals evaluated once match theory (Wilson 96.6%
   and Clopper-Pearson 98.3% at n=100, p=0.05).
2. **Stopping early lowers coverage.** Even binomial intervals fall to the edge of 95% (Wilson 94.95%), and DKW falls to 82.6–85.9%.
   Checking the interval after every sample and stopping as soon as it is narrow favors intervals that happen to be narrow.
3. **On the IID path, `dkw_fixed` is sequential despite its name.** It is the same as `dkw` and stops before `--max-samples`.
4. **Legacy DKW intervals are too narrow with few samples.** When the band width ε = √(ln(2/δ)/(2n)) exceeds the quantile q, the lower
   bound should extend to the lower support, but the legacy code stops at the smallest sample. The new rules extend it correctly.
5. **Mixing in exploration data removes the guarantee almost entirely.** KDE weights barely corrected this bias, and because the
   exploration data alone already made the interval look narrow, the run stopped without collecting a single new uniform sample.
6. **All new rules keep their promise.** In exchange, the sequential rules need 7–9 times as many samples as legacy. 100% is overly
   conservative; methods that keep the guarantee with fewer samples (such as confidence sequences) are candidates for improvement.
7. **(A bug found during validation)** The IID path seeded sample i with `seed + i`, so experiments whose seeds differed by one shared
   almost all samples (99 of 100). This is fixed by `derive_seed` in `orchestration/seeding.py`.

## Which to use

| Goal | Choice |
|---|---|
| A guarantee, with the sample size fixed in advance | `--stopping-rule binomial_ci_fixed_strict` / `dkw_fixed_strict` (choose n ≥ ln(2/δ)/(2ε²)) |
| A guarantee, stopping as soon as possible | `--stopping-rule binomial_ci_sequential_anytime` / `dkw_sequential_anytime` |
| Speed first, an approximation is acceptable | legacy (default) |
| Using exploration data | No guarantee; treat the result as an empirical reference |

`--stopping-rule` currently works with IID-sampling targets and PRISM fixed parameters, not yet with the AWSIM exploration strategist.

## Reproducing

```bash
python3 -m tools.validation.coverage_experiment --list
OMP_NUM_THREADS=1 python3 -m tools.validation.coverage_experiment --reps 2000 --workers 28 \
  --output-dir artifacts/coverage
```

Per-condition results are saved in `artifacts/coverage/summary.json` and per-repetition results in `runs.csv`.
`tests/unit/tools/test_coverage_baseline.py` checks that the results of 8 conditions × 3 seeds do not change.

## Limitations

- Evaluation after every sample (the least favorable case) was measured; the effect of the evaluation interval and delays on a cluster
  was not.
- Only one form of exploration bias (`u_time = v²`) was used; how well KDE works depends on the bias.
- The target uses simple distributions; finite-sample coverage can depend on the shape of the distribution.
