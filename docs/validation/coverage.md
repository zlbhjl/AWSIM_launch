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
  - DKW の評価の時期 (ステージ)・区間・推定値は、どちらの経路も同じコード (`evaluation/stopping_rules.py` の `StagewiseSchedule`、
    `dkw_quantile_interval`、`empirical_quantile`) を使います。両方の経路が同じデータで同じ判断をすることをテストで確かめています。
- 評価のタイミング: 標本が 1 つ届くたびに評価します。これは CLI を `--queue-high-water 1` で動かした場合と完全に一致します (同じ seed で確認)。
  既定の CLI は `worker 数 × 4` 件ずつ発行するため、評価の回数は少なく、止めたあとに数件多く集めます。
- 真の値: 失敗確率 p、指数分布 (λ=1) の 5% 点 −ln(0.95) ≈ 0.0513、Beta(2,5) の平均 2/7。
- 判定: 区間が真の値を含むか (二項・DKW)、間違った仮説を採択しなかったか (SPRT)、相対誤差が ε 以内か (EBStop)。
- くり返し: 各条件 2,000 回。表の [ ] は被覆率そのものの 95% Clopper-Pearson 区間です。
- 実施: 2026-10-18 (コミット `d9028e8` 以降のコード)。DKW の行は、legacy の区間の端を直した後に 2026-10-19 に測り直しました
  (下の「分かったこと」4)。修正前の値も ( ) で残しています。

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
| DKW・`DKWModeRunner` の `dkw_fixed` (n=500、1 回だけ評価) | 100% [99.8, 100] (修正前 100%) | 500 |
| DKW・`DKWModeRunner` の `dkw` (逐次、幅 0.15) | 100% [99.8, 100] (修正前 **85.9%**、端の修正のみ 100%) | 511 (修正前 272、端の修正のみ 352) |
| DKW・IID の経路の `dkw` (逐次、幅 0.15) | 100% [99.8, 100] (修正前 **82.6%**、端の修正のみ 99.9%) | 508 (修正前 158、端の修正のみ 215) |
| DKW・IID の経路の `dkw_fixed` (n=500、1 回だけ評価) | 100% [99.8, 100] (修正前は `dkw` と同じ逐次評価) | 500 |

### 探索データを再利用した場合 (`--dkw-pure-smc` なし)

短い故障時間の側に偏った探索データ 300 件 (`u_time = v²`) を先に入れておきました。

| 条件 | 正しかった割合 | 新しく集めた標本数 |
|---|---|---|
| `dkw` (逐次)、探索データを KDE の重みで再利用 | **11.7%** [10.3, 13.2] (修正前 12.6%) | 50 (修正前 0) |
| `dkw_fixed`、探索データを重みなしで混ぜる (新しい標本 500 件) | **5.0%** [4.1, 6.0] | 500 |
| `dkw` (逐次)、`--dkw-pure-smc` で探索データを無視 | 100% [99.8, 100] (修正前 85.9%) | 511 (修正前 272) |

### `--stopping-rule` の新しい統計アルゴリズム

| ルール | 正しかった割合 | 平均の標本数 |
|---|---|---|
| `binomial_ci_fixed_strict` (n=100、p=0.05) | 98.7% [98.1, 99.1] | 100 |
| `binomial_ci_sequential_anytime` (幅 0.05、p=0.05) | 100% [99.8, 100] | 2,657 |
| `binomial_ci_sequential_anytime` (幅 0.1、p=0.2) | 100% [99.8, 100] | 2,158 |
| `dkw_fixed_strict` (n=1,000) | 100% [99.8, 100] | 1,000 |
| `dkw_sequential_anytime` (幅 0.15) | 100% [99.8, 100] | 1,132 |
| `dkw_sequential_stagewise` (幅 0.15、IID の経路の `dkw` と同じ) | 100% [99.8, 100] | 508 |
| `sprt_wald`、真の p=0.1 / 0.05 | 94.95% / 96.65% | 145 / 163 |
| `ebstop_anytime` | 100% [99.8, 100] | 3,417 |

## 分かったこと

1. **1 回だけ評価する区間、SPRT、EBStop は約束どおりです。** 1 回だけ評価する二項区間の値は、理論値
   (n=100、p=0.05 で Wilson 96.6%、Clopper-Pearson 98.3%) と一致しました。
2. **途中で止めると、被覆率が下がります。** 二項区間では約束の 95% ぎりぎりまで下がります (Wilson 94.95%)。
   標本ごとに区間を確かめ、狭くなった時点で止めるため、たまたま狭くなった区間が選ばれやすくなるからです。
   DKW も修正前は 82.6〜85.9% まで下がっていましたが、主な原因は 4 と 3 の不具合でした。
3. **(修正済み) 逐次の `dkw` は、誤り率をステージに分けていたのに、ステージの中で標本ごとに評価していました。** 評価の回数だけ
   外れる機会が増えるので、誤り率の合計 Σ δ/2^s < δ が成り立っていませんでした。現在は、ステージ s の終わり (最初の 50·s² 個の
   標本がそろった時点) に 1 回だけ、誤り率 δ/2^s で評価します。これで、どのステージで止まっても保証が成り立ちます。
   止まる時点は 50 / 200 / 450 / 800 / 1,250 個のどれかになり、標本数は約 1.45 倍 (352 → 511) に増えました。
   また IID の経路の `dkw_fixed` は、名前に反して `dkw` と同じ逐次評価だったので、`--max-samples` で 1 回だけ評価するようにしました。
4. **(修正済み) legacy の DKW の区間は、標本が少ないと狭すぎました。** DKW の帯の幅 ε = √(ln(2/δ)/(2n)) が分位点の q より
   大きいとき、下端は「取りうる最小値」まで、下側の帯が q に届かないときは上端は +∞ まで広がるべきです
   (Howard & Ramdas 2022, 式 14)。legacy は最も小さい標本・最も大きい標本の値で打ち切っていたため、n=2〜10 で
   「十分」と判定して止まり、そのときの被覆率は 10% 程度でした (外れはほぼすべて「真値 < 下端」)。
   `evaluation/dkw.py` を直した後は、逐次の `dkw` でも 99.9〜100% です。q=0.05、δ=0.05 では n ≥ 738 になるまで下端は
   最小値のままなので、幅による停止は事実上「上端が幅以下」で判定されます。
5. **探索データを混ぜると、保証はほぼなくなります。** KDE の重みでも、この偏りはほとんど直せませんでした。
   しかも探索データだけで区間が十分狭く見えるため、新しい一様な標本を 1 つも取らずに止まりました。
   外れはすべて「真値 > 上端」(偏りで区間が低すぎる) なので、4 の修正では変わりません。KDE の補正は近似です。
6. **新しいルールは、すべて約束を満たしました。** 代わりに、逐次のルールは標本数が legacy の 3〜9 倍 (二項 8〜9 倍、DKW は修正後の legacy の約 2.2 倍) になります。
   100% は保守的すぎるので、少ない標本数で保証を保つ方法 (confidence sequence など) が今後の改良の候補です。
7. **(検証の途中で見つけた不具合)** IID の経路は、標本 i の乱数を `seed + i` で作っていたため、seed が 1 つ違うだけの実験が
   ほぼ同じ標本を使っていました (100 標本中 99 標本が共通)。現在は `orchestration/seeding.py` の `derive_seed` で直してあります。

## 使い分けの目安

| 目的 | 選ぶもの |
|---|---|
| 保証が必要で、標本数を事前に決められる | `--stopping-rule binomial_ci_fixed_strict` / `dkw_fixed_strict` (標本数は n ≥ ln(2/δ)/(2ε²) で決める) |
| 保証が必要で、途中で止めたい (二項) | `--stopping-rule binomial_ci_sequential_anytime` |
| 保証が必要で、途中で止めたい (DKW) | `--mode dkw` (= `dkw_sequential_stagewise`、ステージの終わりだけ評価)。評価のたびに止められる `dkw_sequential_anytime` は標本数が約 2.2 倍 |
| 早さを優先し、近似でよい | legacy の `--mode binomial_ci` / `sprt` (被覆率は約束の境目、約 95%) |
| 探索データを使う | 保証はない。結果は経験的な参考値として扱う |

`--stopping-rule` は、現在は IID 型の対象と PRISM の固定パラメータで使えます。AWSIM の探索戦略では、まだ使えません。

## 再現

```bash
python3 -m tools.validation.coverage_experiment --list
OMP_NUM_THREADS=1 python3 -m tools.validation.coverage_experiment --reps 2000 --workers 28 \
  --output-dir artifacts/coverage
```

各条件の結果は `artifacts/coverage/summary.json`、1 回ごとの結果は `runs.csv` に保存されます。
`tests/unit/tools/test_coverage_baseline.py` は、9 条件 × 3 seed の結果が変わっていないことを確かめます。

## 制約

- 評価は標本ごとに行う場合 (最も不利な場合) で測っています。クラスターでの評価の間隔や遅れの影響は測っていません。
- 探索データの偏りは 1 種類 (`u_time = v²`) だけです。KDE の効き方は偏り方によって変わります。
- 対象は単純な分布です。分布の形によって、特に有限標本での被覆率は変わりえます。

## SDE の重点サンプリング（固定 N、モデル A・B）

[SDE 重点サンプリングの計画書](../sde_importance_sampling_plan.md) §13 の本番の検証です。真の確率が式で分かる 2 つのモデルで、
固定 N の比較（通常 MC と重点サンプリング）を 2,000 回くり返しました。

### 方法

- 道具: 上と同じ `tools/validation/coverage_experiment.py` の engine `importance_fixed`。実験で使う
  `run_strict_fixed_importance_sampling_comparison` をそのまま呼び、1 回の実行で通常 MC と重点サンプリング（IS）の推定値と区間を得ます。
- モデル A: スカラー OU（dX = −X dt + dW、X₀ = 0、時間 1、dt = 0.01）。事象は終端の X が 3σ・4σ を超えること（片側・両側）。
- モデル B: 拡大 OU（2 次元、ノイズは 1 チャネル）。事象は終端の x が 3σ を超えること（片側）。
- 真値: Euler–Maruyama で離散化したモデルの終端分布（正規分布）から求めた厳密な確率。
- 提案分布 Q: `configs/is_final/` に固定した最終プラン（pilot で選んだもの。選んだ記録は `configs/is_tuning/*/records/`）。
  pilot は root entropy 20261023、この検証は seed 20261101〜 で、乱数の系列は別です。

| Q | 内容 |
|---|---|
| Q0 | Q = P（重み 1。通常 MC と同じ分布で、IS の集計の確認用） |
| Q1 | 時刻区分ごとの Gaussian 平均（上側へ一方向に押す） |
| Q1d | Q1 に防御的混合 β = 0.2（重みの上限 1/β = 5） |
| Q2 | Q1 と同じ平均で、分散も変える |
| Q3 | 状態に依存する平均（時刻区分ごとのアフィン） |
| Q4 | Q3 と同じ規則の Student-t（ν = 5） |
| Q5 | 経路単位の 2 成分混合（片側: 強さの違う 2 つの Q1。両側: 上向きと下向き） |

- N: 通常 MC 1,000 経路 + IS 1,000 経路。名目の信頼度は 95%。
- 区間: 通常 MC は Wilson、IS は正規近似（診断用）、Hoeffding と経験的 Bernstein（保証の確認用。重みに上限があるときだけ作れる）。
- 事象を 1 件も観測しなかった回は、正規近似の区間を作らず（情報不足）、被覆していない回として数えます。
- 効率比は (通常 MC の RMSE ÷ IS の RMSE)²、つまり同じ精度に通常 MC が何倍の経路を要するかです。1 経路の時間は、Q0 が約 1.8 ms、
  Q1〜Q5 が 2.0〜3.4 ms（30 並列）なので、時間あたりでは効率比を 0.5〜0.9 倍して読みます。
- 実施: 2026-10-23（コミット `794a628` 以降のコード）。35 条件 × 2,000 回。結果は
  [data/is_coverage_n1000_summary.json](data/is_coverage_n1000_summary.json)。

### 結果（N = 1,000）

表の [ ] は被覆率の 95% Clopper-Pearson 区間です。上端が 95% に届かないものを太字にしました。
「—」は、重みに上限が無いため保証用の区間を作れない Q です。

| 条件 | Q | 正規近似 [CP 区間] | 事象なし | Hoeffding（平均幅） | 経験的 Bernstein（平均幅） | 相対 RMSE | 効率比 | 推定値の平均 / 中央値（真値比） |
|---|---|---|---|---|---|---|---|---|
| A・3σ・片側（P = 0.00135） | 通常 MC（Wilson） | 95.0% [94.0, 95.9] | 26.8% | — | — | 0.856 | 1 | 0.98 / — |
| | Q0 | **76.7% [74.8, 78.5]** | 23.2% | 100.0%（0.044） | 100.0%（0.017） | 0.849 | 1.02 | 1.03 / 0.74 |
| | Q1 | 95.5% [94.4, 96.3] | 0.0% | — | — | 0.061 | 196 | 1.00 / 1.00 |
| | Q1d | 94.8% [93.7, 95.7] | 0.0% | 100.0%（0.22） | 100.0%（0.063） | 0.070 | 148 | 1.00 / 1.00 |
| | Q2 | **92.7% [91.4, 93.8]** | 0.0% | — | — | 0.138 | 38.6 | 1.01 / 1.00 |
| | Q3 | **69.0% [66.9, 71.0]** | 0.0% | — | — | 1.239 | 0.477 | 1.00 / 0.88 |
| | Q4 | **42.6% [40.4, 44.8]** | 0.0% | — | — | 4.512 | 0.036 | 0.91 / 0.56 |
| | Q5 | 94.9% [93.8, 95.8] | 0.0% | — | — | 0.067 | 163 | 1.00 / 1.00 |
| A・3σ・両側（P = 0.0027） | 通常 MC（Wilson） | 94.7% [93.6, 95.6] | 6.7% | — | — | 0.599 | 1 | 0.99 / — |
| | Q0 | 94.3% [93.2, 95.3] | 5.6% | 100.0%（0.046） | 100.0%（0.019） | 0.603 | 0.988 | 1.02 / 1.11 |
| | Q1 | **0.0% [0.0, 0.2]** | 0.0% | — | — | 0.500 | 1.44 | 0.50 / 0.50 |
| | Q1d | **23.8% [22.0, 25.8]** | 0.0% | 100.0%（0.22） | 100.0%（0.068） | 0.980 | 0.374 | 1.01 / 0.51 |
| | Q2 | **0.4% [0.2, 0.8]** | 0.0% | — | — | 0.501 | 1.43 | 0.50 / 0.50 |
| | Q3 | **92.3% [91.1, 93.5]** | 0.0% | — | — | 0.099 | 36.6 | 1.00 / 0.99 |
| | Q4 | **70.0% [68.0, 72.1]** | 0.0% | — | — | 0.978 | 0.376 | 0.98 / 0.81 |
| | Q5 | **94.0% [92.8, 95.0]** | 0.0% | — | — | 0.061 | 95.5 | 1.00 / 1.00 |
| A・4σ・片側（P = 3.17e-05） | 通常 MC（Wilson） | 96.4% [95.5, 97.2] | 96.4% | — | — | 6.127 | 1 | 1.17 / — |
| | Q0 | **2.9% [2.2, 3.7]** | 97.2% | 100.0%（0.043） | 100.0%（0.012） | 5.392 | 1.29 | 0.92 / 0.00 |
| | Q1 | 94.6% [93.5, 95.5] | 0.0% | — | — | 0.072 | 7.23e+03 | 1.00 / 1.00 |
| | Q1d | 94.8% [93.8, 95.8] | 0.0% | 100.0%（0.21） | 100.0%（0.061） | 0.082 | 5.57e+03 | 1.00 / 1.00 |
| | Q2 | **92.0% [90.7, 93.1]** | 0.0% | — | — | 0.158 | 1.5e+03 | 1.01 / 1.00 |
| | Q3 | **63.7% [61.6, 65.9]** | 0.0% | — | — | 1.831 | 11.2 | 1.01 / 0.83 |
| | Q4 | **36.4% [34.3, 38.6]** | 0.0% | — | — | 3.087 | 3.94 | 0.80 / 0.47 |
| | Q5 | 94.9% [93.8, 95.8] | 0.0% | — | — | 0.086 | 5.09e+03 | 1.00 / 1.00 |
| A・4σ・両側（P = 6.33e-05） | 通常 MC（Wilson） | **93.5% [92.3, 94.5]** | 93.5% | — | — | 4.244 | 1 | 1.09 / — |
| | Q0 | **6.1% [5.1, 7.2]** | 93.9% | 100.0%（0.043） | 100.0%（0.013） | 3.872 | 1.2 | 0.98 / 0.00 |
| | Q1 | **0.0% [0.0, 0.2]** | 0.0% | — | — | 0.500 | 72 | 0.50 / 0.50 |
| | Q1d | **0.7% [0.3, 1.1]** | 0.0% | 100.0%（0.21） | 100.0%（0.062） | 6.343 | 0.448 | 1.01 / 0.50 |
| | Q2 | **1.0% [0.6, 1.5]** | 0.0% | — | — | 0.502 | 71.3 | 0.50 / 0.50 |
| | Q3 | **91.3% [90.0, 92.5]** | 0.0% | — | — | 0.121 | 1.22e+03 | 1.00 / 0.98 |
| | Q4 | **67.5% [65.3, 69.5]** | 0.0% | — | — | 1.515 | 7.85 | 1.02 / 0.77 |
| | Q5 | 94.0% [92.9, 95.0] | 0.0% | — | — | 0.072 | 3.5e+03 | 1.00 / 1.00 |
| B・3σ・片側（P = 0.00135） | 通常 MC（Wilson） | 95.5% [94.4, 96.3] | 26.2% | — | — | 0.860 | 1 | 1.00 / — |
| | Q0 | **74.1% [72.1, 76.0]** | 25.9% | 100.0%（0.044） | 100.0%（0.016） | 0.859 | 1 | 1.00 / 0.74 |
| | Q1 | 94.2% [93.0, 95.1] | 0.0% | — | — | 0.066 | 172 | 1.00 / 1.00 |
| | Q1d | 94.0% [92.9, 95.0] | 0.0% | 100.0%（0.22） | 100.0%（0.063） | 0.076 | 130 | 1.00 / 1.00 |
| | Q2 | **92.8% [91.6, 93.9]** | 0.0% | — | — | 0.139 | 38.5 | 1.01 / 1.00 |
| | Q3 | **25.4% [23.5, 27.3]** | 0.0% | — | — | 1.392 | 0.382 | 0.69 / 0.48 |
| | Q4 | **16.2% [14.6, 17.8]** | 0.0% | — | — | 1.277 | 0.454 | 0.43 / 0.25 |
| | Q5 | **93.8% [92.7, 94.8]** | 0.0% | — | — | 0.074 | 137 | 1.00 / 1.00 |

### 分かったこと

1. **保証用の区間（Hoeffding、経験的 Bernstein）は、作れたすべての条件で約束を守りました。** Q0 と Q1d の 10 条件すべてで 100%
   [99.8, 100] です。代わりに幅は対象確率の約 10〜7,000 倍あり、計画どおり、効率の比較には使えません。
   経験的 Bernstein の幅は、Hoeffding の約 1/3 です。
2. **Q が事象の起こり方に合っていれば、正規近似の区間はほぼ 95% で、効率は大きく上がります。** 片側の事象では Q1・Q1d・Q5 が
   93.8〜95.5% で、通常 MC と比べた効率比は 3σ で 130〜196 倍、4σ で 5,000〜7,200 倍です。両側の事象では Q5（上下の混合）が
   94.0% で、効率比は 3σ で 96 倍、4σ で 3,500 倍です。Q2 は効率が下がり、被覆率も 92〜93% でした。
3. **片方向の Q を両側の事象に使うと、推定値は真値のほぼ半分になり、被覆率は 0〜1% です。** Q1・Q2 は下側の事象を
   ほとんど標本化しません。推定値の分散は小さく見えるので、pilot の相対標準誤差では見分けられません（pilot でも推定値は半分でした）。
   防御的混合（Q1d）にすると推定値の平均は正しくなりますが、下側の事象を引いた回だけ値が跳ぶので、正規近似の区間は
   24%（3σ）・0.7%（4σ）しか被覆しません。このとき正しいのは保証用の区間だけです。
4. **Student-t（Q4）と、片側の事象での状態依存の平均（Q3）は、重みの裾が重く、N = 1,000 では使えません。** 多くの回で推定値は
   真値より小さく（中央値は真値の 0.25〜0.88 倍）、まれに真値の数十〜188 倍の値が出ます（上位 1% の回が合計の 7〜22% を占める）。
   被覆率は 16〜70%、効率比は 1 未満になる条件もあります。両側の事象での Q3 は、被覆率 91〜92%、効率比 37 倍（3σ）・
   1,200 倍（4σ）で、Q5 には及びませんが使える範囲でした。モデル B の Q3・Q4 では、2,000 回の平均でも真値の 0.69・0.43 倍に
   とどまりました。同じ規則のモデル A の片側で平均がほぼ真値（Q3 で 0.997 倍）であること、外れの形が中央値の偏りとまれな
   大きな値であることから、実装の誤りではなく、重みの分散が非常に大きい（無限の可能性がある）提案分布だと判断しています。
   ただし、これは経験的な判断で、重みの分散の有限性は確かめていません。pilot（1,000 経路 × 数試行）ではこれを見分けられず、
   Q3・Q4 を候補の 1 位に選んでいました。
5. **Q0（Q = P）は、通常 MC と同じ精度でした。** 効率比は 0.99〜1.3 倍で、IS の集計（対数領域、ゼロ重み）が通常 MC と
   一致することを確かめられます。稀な事象では事象なしの回が多く（3σ 片側 23%、4σ 93〜97%）、正規近似の区間はその分だけ欠けます。
6. **通常 MC の Wilson 区間は、4σ 両側で 93.5% [92.3, 94.5] と約束を下回りました。** 事象がほぼ観測されない（93.5% の回で 0 件）
   ためで、上の bernoulli の結果とは別の、極端に稀な事象での性質です。

### 結果（N = 10,000）

同じ 35 条件を、N = 10,000（通常 MC 10,000 経路 + IS 10,000 経路）で 2,000 回ずつくり返しました。seed は 20270101〜で、
N = 1,000 の seed とは重なりません。2026-10-23〜25 に実行しました（コード `34370ff`、途中で 1 回再起動し、中断した 1 条件だけを
やり直しました）。結果は [data/is_coverage_n10000_summary.json](data/is_coverage_n10000_summary.json)。

相対 RMSE の「縮み」は N = 1,000 の値 ÷ N = 10,000 の値です。重みの分散が有限なら、約 √10 ≈ 3.16 倍に縮みます。
効率比と推定値の平均・中央値は N = 10,000 の値です。

| 条件 | Q | 正規近似 N=1,000 | 正規近似 N=10,000 [CP 区間] | 相対 RMSE 1,000 → 10,000（縮み） | 効率比 | 推定値の平均 / 中央値（真値比） |
|---|---|---|---|---|---|---|
| A・3σ・片側（P = 0.00135） | 通常 MC（Wilson） | 95.0% | 94.8% [93.7, 95.7] | 0.856 → 0.272（×3.15） | 1 | 1.00 / — |
| | Q0 | 76.7% | 94.8% [93.7, 95.7] | 0.849 → 0.272（×3.12） | 1 | 1.00 / 0.96 |
| | Q1 | 95.5% | **93.6% [92.4, 94.6]** | 0.061 → 0.020（×3.08） | 187 | 1.00 / 1.00 |
| | Q1d | 94.8% | 94.5% [93.4, 95.5] | 0.070 → 0.023（×3.09） | 143 | 1.00 / 1.00 |
| | Q2 | 92.7% | 94.3% [93.2, 95.3] | 0.138 → 0.043（×3.17） | 39.2 | 1.00 / 1.00 |
| | Q3 | 69.0% | **65.6% [63.5, 67.7]** | 1.239 → 0.228（×5.43） | 1.42 | 0.96 / 0.93 |
| | Q4 | 42.6% | **47.4% [45.2, 49.7]** | 4.512 → 0.875（×5.15） | 0.0965 | 0.87 / 0.71 |
| | Q5 | 94.9% | 95.4% [94.4, 96.3] | 0.067 → 0.021（×3.16） | 164 | 1.00 / 1.00 |
| A・3σ・両側（P = 0.0027） | 通常 MC（Wilson） | 94.7% | 95.0% [93.9, 95.9] | 0.599 → 0.196（×3.06） | 1 | 1.00 / — |
| | Q0 | 94.3% | 94.9% [93.8, 95.8] | 0.603 → 0.195（×3.09） | 1.01 | 1.00 / 1.00 |
| | Q1 | 0.0% | **0.0% [0.0, 0.2]** | 0.500 → 0.500（×1.00） | 0.153 | 0.50 / 0.50 |
| | Q1d | 23.8% | **93.7% [92.5, 94.7]** | 0.980 → 0.306（×3.20） | 0.41 | 1.00 / 1.04 |
| | Q2 | 0.4% | **0.0% [0.0, 0.2]** | 0.501 → 0.501（×1.00） | 0.153 | 0.50 / 0.50 |
| | Q3 | 92.3% | 94.0% [92.9, 95.0] | 0.099 → 0.034（×2.94） | 33.8 | 1.00 / 1.00 |
| | Q4 | 70.0% | **82.2% [80.5, 83.9]** | 0.978 → 0.339（×2.88） | 0.333 | 1.00 / 0.94 |
| | Q5 | 94.0% | 95.0% [94.0, 96.0] | 0.061 → 0.019（×3.19） | 104 | 1.00 / 1.00 |
| A・4σ・片側（P = 3.17e-05） | 通常 MC（Wilson） | 96.4% | 95.6% [94.6, 96.5] | 6.127 → 1.785（×3.43） | 1 | 0.99 / — |
| | Q0 | 2.9% | **26.7% [24.7, 28.6]** | 5.392 → 1.731（×3.11） | 1.06 | 0.97 / 0.00 |
| | Q1 | 94.6% | **93.6% [92.4, 94.6]** | 0.072 → 0.023（×3.11） | 5.93e+03 | 1.00 / 1.00 |
| | Q1d | 94.8% | **94.0% [92.8, 95.0]** | 0.082 → 0.026（×3.11） | 4.58e+03 | 1.00 / 1.00 |
| | Q2 | 92.0% | 94.3% [93.2, 95.3] | 0.158 → 0.050（×3.18） | 1.29e+03 | 1.00 / 1.00 |
| | Q3 | 63.7% | **62.4% [60.2, 64.5]** | 1.831 → 0.308（×5.94） | 33.5 | 0.96 / 0.90 |
| | Q4 | 36.4% | **40.5% [38.3, 42.6]** | 3.087 → 1.462（×2.11） | 1.49 | 0.84 / 0.63 |
| | Q5 | 94.9% | 95.5% [94.5, 96.4] | 0.086 → 0.027（×3.18） | 4.37e+03 | 1.00 / 1.00 |
| A・4σ・両側（P = 6.33e-05） | 通常 MC（Wilson） | 93.5% | 97.3% [96.5, 98.0] | 4.244 → 1.248（×3.40） | 1 | 1.00 / — |
| | Q0 | 6.1% | **48.4% [46.2, 50.7]** | 3.872 → 1.247（×3.11） | 1 | 1.03 / 0.00 |
| | Q1 | 0.0% | **0.0% [0.0, 0.2]** | 0.500 → 0.500（×1.00） | 6.22 | 0.50 / 0.50 |
| | Q1d | 0.7% | **6.2% [5.2, 7.4]** | 6.343 → 2.047（×3.10） | 0.372 | 1.02 / 0.50 |
| | Q2 | 1.0% | **0.0% [0.0, 0.2]** | 0.502 → 0.501（×1.00） | 6.22 | 0.50 / 0.50 |
| | Q3 | 91.3% | **92.3% [91.1, 93.5]** | 0.121 → 0.042（×2.86） | 868 | 1.00 / 1.00 |
| | Q4 | 67.5% | **77.8% [75.9, 79.6]** | 1.515 → 0.428（×3.54） | 8.52 | 1.00 / 0.92 |
| | Q5 | 94.0% | 95.2% [94.2, 96.1] | 0.072 → 0.023（×3.19） | 3.08e+03 | 1.00 / 1.00 |
| B・3σ・片側（P = 0.00135） | 通常 MC（Wilson） | 95.5% | 94.2% [93.1, 95.2] | 0.860 → 0.273（×3.15） | 1 | 1.00 / — |
| | Q0 | 74.1% | 94.9% [93.8, 95.8] | 0.859 → 0.272（×3.17） | 1.01 | 1.01 / 0.96 |
| | Q1 | 94.2% | 94.7% [93.6, 95.6] | 0.066 → 0.021（×3.20） | 177 | 1.00 / 1.00 |
| | Q1d | 94.0% | 95.0% [93.9, 95.9] | 0.076 → 0.024（×3.20） | 134 | 1.00 / 1.00 |
| | Q2 | 92.8% | 95.5% [94.4, 96.3] | 0.139 → 0.045（×3.06） | 36.3 | 1.00 / 1.00 |
| | Q3 | 25.4% | **22.2% [20.4, 24.1]** | 1.392 → 0.933（×1.49） | 0.0857 | 0.69 / 0.58 |
| | Q4 | 16.2% | **16.4% [14.8, 18.1]** | 1.277 → 1.213（×1.05） | 0.0507 | 0.52 / 0.34 |
| | Q5 | 93.8% | 94.9% [93.8, 95.8] | 0.074 → 0.023（×3.24） | 145 | 1.00 / 1.00 |

### 分かったこと（N = 10,000）

1. **保証用の区間は、N = 10,000 でも 10 条件すべてで 100% [99.8, 100] でした。** §13 の基準を満たしています。平均の幅は、
   Hoeffding が約 1/3（1/√10）に、経験的 Bernstein が 1/4.6〜1/9.8 に縮みました。経験的 Bernstein は分散に適応し、
   N が大きいと 1/N の項が効かなくなるためです。それでも幅は対象確率の約 6 倍（3σ 片側、Q1d）〜42 倍（4σ 片側、Q0）あり、
   効率の比較には使えないことは変わりません。
2. **事象に合った Q（Q1・Q1d・Q2・Q5）は、相対 RMSE が √10 のとおりに縮みました（×3.06〜3.24）。** 重みの分散が有限な場合の
   ふるまいです。効率比も N = 1,000 とほぼ同じです（3σ で 36〜187 倍、4σ で 1,300〜5,900 倍）。
   正規近似の区間は、Q5 が全条件で 94.9〜95.5%、Q2（片側）が 92〜93% から 94.3〜95.5% に上がりました。
   ただし **Q1 の片側は 93.6〜94.7%** で、モデル A の 3σ・4σ の片側は Clopper-Pearson 区間の上端が 94.6% と、名目をわずかに
   下回りました（N = 1,000 では 94.2〜95.5%）。Q1d の 4σ 片側も、上端がわずかに 95% に届きません（94.0% [92.8, 95.0]）。正規近似の区間には保証がなく、N を増やせば必ず 95% に近づくわけではありません。
3. **片方向の Q を両側の事象に使った場合（Q1・Q2）は、N を増やしても推定値は真値の 0.50 倍のままで、被覆率は 0% です。**
   相対 RMSE は 0.50 から縮みません（×1.00）。偏りが原因なので、標本数では直りません。防御的混合（Q1d）は、3σ 両側では
   被覆率が 23.8% → 93.7% に上がりましたが、4σ 両側では 0.7% → 6.2% にとどまります（中央値は真値の 0.50 倍のまま）。
   下側の事象を引く確率が β × P（下側）≈ 6×10⁻⁶ なので、N = 10,000 でもほとんど引けないためです。
4. **Q3・Q4 は、片側の事象では N を 10 倍にしても被覆率が上がりません（Q3 62〜66%、Q4 40〜47%、モデル B は 16〜22%）。**
   モデル B の Q3・Q4 は、2,000 回の平均でも真値の 0.69・0.52 倍で、相対 RMSE もほとんど縮みません（×1.49・×1.05）。
   これは、重みの分散が無限か非常に大きいときのふるまいで、N = 1,000 での判断と合います。モデル A の片側の Q3 は、相対 RMSE が
   √10 より速く縮みましたが（×5.4〜5.9）、これは N = 1,000 の RMSE が少数の巨大な値で決まっていたためで、推定値の中央値は
   真値の 0.90〜0.93 倍のまま、被覆率も上がっていません。両側の Q3 は 92.3〜94.0% で、使える範囲のままです。
5. **Q0（Q = P）は、N = 10,000 でも通常 MC と同じ精度でした**（効率比 1.00〜1.06、相対 RMSE ×3.09〜3.17）。3σ では事象なしの回が
   なくなり、被覆率は 94.8〜94.9% です。4σ ではまだ 52〜73% の回で事象が出ず、被覆率は 27〜48% です。
6. **通常 MC の Wilson 区間は、全条件で 94.2〜97.3% でした。** N = 1,000 で下回った 4σ 両側（93.5%）も、97.3% になりました。

### 再現

```bash
OMP_NUM_THREADS=1 python3 -m tools.validation.coverage_experiment --reps 2000 --seed-base 20261101 \
  --scenarios $(python3 -m tools.validation.coverage_experiment --list | grep -o '^is_final_[^:]*_n1000') \
  --output-dir artifacts/coverage_final_n1000
```

N = 10,000 は条件ごとに実行し、中断しても失うのは 1 条件（28 並列で約 1 時間）だけにしました。

```bash
for name in $(python3 -m tools.validation.coverage_experiment --list | grep -o '^is_final_[^:]*_n10000'); do
  [ -f "artifacts/coverage_final_n10000/$name/summary.json" ] && continue
  OMP_NUM_THREADS=1 python3 -m tools.validation.coverage_experiment --reps 2000 --workers 28 --seed-base 20270101 \
    --scenarios "$name" --output-dir "artifacts/coverage_final_n10000/$name"
done
```

### 制約

- 1 経路の時間は N = 10,000 で 1.7〜3.3 ms（28 並列）。35 条件の合計は約 34 時間でした。
- 真値があるのは A・B だけです。モデル C・D（U ターン）は参照の MC と比べます（`tools/validation/compare_to_reference.py`）。
- Q の候補は pilot で選んだ 1 つずつです。Q3・Q4 の結果は、この調整のしかたでの結果で、状態依存の平均や Student-t 一般の性質ではありません。

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
  - Both engines use the same code for the DKW evaluation schedule (stages), interval, and estimate (`StagewiseSchedule`,
    `dkw_quantile_interval`, and `empirical_quantile` in `evaluation/stopping_rules.py`). A test checks that both make the same decisions
    on the same data.
- Evaluation timing: after every sample. This is identical to running the CLI with `--queue-high-water 1` (checked with the same
  seeds). By default the CLI issues `4 × workers` cases at a time, so it evaluates less often and collects a few extra samples after
  deciding to stop.
- Truth: the failure probability p, the 5% quantile of Exponential(λ=1), −ln(0.95) ≈ 0.0513, and the mean of Beta(2,5), 2/7.
- Correctness: the interval contains the truth (binomial, DKW), the wrong hypothesis was not accepted (SPRT), or the relative error is
  within ε (EBStop).
- Repetitions: 2,000 per condition. The brackets [ ] are 95% Clopper-Pearson intervals of the coverage itself.
- Run on 2026-10-18 (code from commit `d9028e8` on). The DKW rows were measured again on 2026-10-19 after fixing the legacy interval
  ends (Finding 4) and the evaluation schedule of sequential `dkw` (Finding 3); the earlier values are kept in ( ).

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
| DKW, `DKWModeRunner` `dkw_fixed` (n=500, one evaluation) | 100% [99.8, 100] (before the fix 100%) | 500 |
| DKW, `DKWModeRunner` `dkw` (sequential, width 0.15) | 100% [99.8, 100] (before the fixes **85.9%**, ends fix only 100%) | 511 (before 272, ends fix only 352) |
| DKW, IID path `dkw` (sequential, width 0.15) | 100% [99.8, 100] (before the fixes **82.6%**, ends fix only 99.9%) | 508 (before 158, ends fix only 215) |
| DKW, IID path `dkw_fixed` (n=500, one evaluation) | 100% [99.8, 100] (before: sequential, the same as `dkw`) | 500 |

### Reusing exploration data (without `--dkw-pure-smc`)

300 exploration rows biased toward short times to failure (`u_time = v²`) were present before sampling.

| Condition | Correct | New samples collected |
|---|---|---|
| `dkw` (sequential), exploration data reused with KDE weights | **11.7%** [10.3, 13.2] (before 12.6%) | 50 (before 0) |
| `dkw_fixed`, exploration data mixed in unweighted (500 new samples) | **5.0%** [4.1, 6.0] | 500 |
| `dkw` (sequential), `--dkw-pure-smc` ignores the exploration data | 100% [99.8, 100] (before the fixes 85.9%) | 511 (before 272) |

### New statistical algorithms (`--stopping-rule`)

| Rule | Correct | Mean sample size |
|---|---|---|
| `binomial_ci_fixed_strict` (n=100, p=0.05) | 98.7% [98.1, 99.1] | 100 |
| `binomial_ci_sequential_anytime` (width 0.05, p=0.05) | 100% [99.8, 100] | 2,657 |
| `binomial_ci_sequential_anytime` (width 0.1, p=0.2) | 100% [99.8, 100] | 2,158 |
| `dkw_fixed_strict` (n=1,000) | 100% [99.8, 100] | 1,000 |
| `dkw_sequential_anytime` (width 0.15) | 100% [99.8, 100] | 1,132 |
| `dkw_sequential_stagewise` (width 0.15, the same as IID-path `dkw`) | 100% [99.8, 100] | 508 |
| `sprt_wald`, true p=0.1 / 0.05 | 94.95% / 96.65% | 145 / 163 |
| `ebstop_anytime` | 100% [99.8, 100] | 3,417 |

## Findings

1. **Intervals evaluated once, SPRT, and EBStop keep their promise.** The binomial intervals evaluated once match theory (Wilson 96.6%
   and Clopper-Pearson 98.3% at n=100, p=0.05).
2. **Stopping early lowers coverage.** Binomial intervals fall to the edge of 95% (Wilson 94.95%). Checking the interval after every
   sample and stopping as soon as it is narrow favors intervals that happen to be narrow. DKW fell to 82.6–85.9% before the fix, but
   mainly because of the bugs in Findings 4 and 3.
3. **(Fixed) Sequential `dkw` split the error rate across stages but evaluated after every sample within a stage.** Each evaluation is
   another chance to miss, so the total Σ δ/2^s < δ did not hold. Now stage s is evaluated once, when the first 50·s² samples are in,
   with error rate δ/2^s, so the guarantee holds at whatever stage the run stops. Runs stop at 50 / 200 / 450 / 800 / 1,250 samples,
   and the sample size grew about 1.45 times (352 → 511). On the IID path, `dkw_fixed` was sequential like `dkw` despite its name; it
   now evaluates once at `--max-samples`.
4. **(Fixed) Legacy DKW intervals were too narrow with few samples.** When the band width ε = √(ln(2/δ)/(2n)) exceeds the quantile q,
   the lower bound should extend to the lower support, and when the lower band never reaches q, the upper bound should be +∞
   (Howard & Ramdas 2022, eq. 14). The legacy code stopped at the smallest and largest samples, so it judged the interval sufficient
   at n=2–10, where coverage was about 10% (almost all misses had the truth below the lower bound). After fixing `evaluation/dkw.py`,
   even the sequential `dkw` covers 99.9–100%. With q=0.05 and δ=0.05, the lower bound stays at the support until n ≥ 738, so
   stopping by width effectively means "the upper bound is at most the width".
5. **Mixing in exploration data removes the guarantee almost entirely.** KDE weights barely corrected this bias, and because the
   exploration data alone already made the interval look narrow, the run stopped without collecting a single new uniform sample.
   All misses have the truth above the upper bound (the bias makes the interval too low), so the fix in Finding 4 does not change
   this. The KDE correction is an approximation.
6. **All new rules keep their promise.** In exchange, the sequential rules need 3–9 times as many samples as legacy (binomial 8–9 times, DKW about 2.2 times the fixed legacy). 100% is overly
   conservative; methods that keep the guarantee with fewer samples (such as confidence sequences) are candidates for improvement.
7. **(A bug found during validation)** The IID path seeded sample i with `seed + i`, so experiments whose seeds differed by one shared
   almost all samples (99 of 100). This is fixed by `derive_seed` in `orchestration/seeding.py`.

## Which to use

| Goal | Choice |
|---|---|
| A guarantee, with the sample size fixed in advance | `--stopping-rule binomial_ci_fixed_strict` / `dkw_fixed_strict` (choose n ≥ ln(2/δ)/(2ε²)) |
| A guarantee, stopping as soon as possible (binomial) | `--stopping-rule binomial_ci_sequential_anytime` |
| A guarantee, stopping as soon as possible (DKW) | `--mode dkw` (= `dkw_sequential_stagewise`, evaluated at stage ends only); `dkw_sequential_anytime`, which can stop at any evaluation, needs about 2.2 times as many samples |
| Speed first, an approximation is acceptable | legacy `--mode binomial_ci` / `sprt` (coverage sits at the edge, about 95%) |
| Using exploration data | No guarantee; treat the result as an empirical reference |

`--stopping-rule` currently works with IID-sampling targets and PRISM fixed parameters, not yet with the AWSIM exploration strategist.

## Reproducing

```bash
python3 -m tools.validation.coverage_experiment --list
OMP_NUM_THREADS=1 python3 -m tools.validation.coverage_experiment --reps 2000 --workers 28 \
  --output-dir artifacts/coverage
```

Per-condition results are saved in `artifacts/coverage/summary.json` and per-repetition results in `runs.csv`.
`tests/unit/tools/test_coverage_baseline.py` checks that the results of 9 conditions × 3 seeds do not change.

## Limitations

- Evaluation after every sample (the least favorable case) was measured; the effect of the evaluation interval and delays on a cluster
  was not.
- Only one form of exploration bias (`u_time = v²`) was used; how well KDE works depends on the bias.
- The target uses simple distributions; finite-sample coverage can depend on the shape of the distribution.

## SDE importance sampling (fixed N, models A and B)

This is the production check of §13 of the [SDE importance-sampling plan](../sde_importance_sampling_plan.md). On two models with
exactly known probabilities, the fixed-N comparison (plain MC and importance sampling) was repeated 2,000 times.

### Method

- Tool: the engine `importance_fixed` of the same `tools/validation/coverage_experiment.py`. It calls the production
  `run_strict_fixed_importance_sampling_comparison`; one run gives the estimates and intervals of plain MC and of importance sampling (IS).
- Model A: scalar OU (dX = −X dt + dW, X₀ = 0, horizon 1, dt = 0.01). The event is the terminal X beyond 3σ or 4σ (upper or two-sided).
- Model B: augmented OU (2-D, one noise channel). The event is the terminal x above 3σ.
- Truth: the exact probability under the terminal (Gaussian) law of the Euler–Maruyama-discretised model.
- Proposals Q: the final plans fixed in `configs/is_final/` (chosen by the pilot; the records are in `configs/is_tuning/*/records/`).
  The pilot used root entropy 20261023 and this check seeds from 20261101, so the random streams are separate.

| Q | Content |
|---|---|
| Q0 | Q = P (weight 1; the plain-MC law, a check of the IS aggregation) |
| Q1 | Gaussian mean per time segment (pushing one way, upward) |
| Q1d | Q1 with a defensive mixture β = 0.2 (weights bounded by 1/β = 5) |
| Q2 | Q1's mean with a changed variance |
| Q3 | State-dependent mean (affine per time segment) |
| Q4 | Student-t (ν = 5) with Q3's rule |
| Q5 | Path-level two-component mixture (upper: two Q1 of different strengths; two-sided: upward and downward) |

- N: 1,000 plain-MC paths + 1,000 IS paths. Nominal confidence 95%.
- Intervals: Wilson for plain MC; for IS the normal approximation (diagnostic) and Hoeffding and empirical Bernstein (guarantee checks,
  available only when the weights are bounded).
- A run without an observed event has no normal-approximation interval (insufficient information) and counts as not covering.
- Efficiency is (RMSE of plain MC ÷ RMSE of IS)², the factor of plain-MC paths needed for the same accuracy. A path takes about 1.8 ms
  under Q0 and 2.0–3.4 ms under Q1–Q5 (30 workers), so per unit time multiply the efficiency by 0.5–0.9.
- Run on 2026-10-23 (code from commit `794a628` on). 35 conditions × 2,000 repetitions. Results:
  [data/is_coverage_n1000_summary.json](data/is_coverage_n1000_summary.json).

### Results (N = 1,000)

Brackets [ ] are 95% Clopper-Pearson intervals of the coverage. Bold marks an upper end below 95%.
"—" marks a Q with unbounded weights, for which the guarantee intervals cannot be formed.

| Problem | Q | Normal approx. [CP interval] | No event | Hoeffding (mean width) | Empirical Bernstein (mean width) | Relative RMSE | Efficiency | Mean / median estimate (÷ truth) |
|---|---|---|---|---|---|---|---|---|
| A, 3σ, upper (P = 0.00135) | plain MC (Wilson) | 95.0% [94.0, 95.9] | 26.8% | — | — | 0.856 | 1 | 0.98 / — |
| | Q0 | **76.7% [74.8, 78.5]** | 23.2% | 100.0% (0.044) | 100.0% (0.017) | 0.849 | 1.02 | 1.03 / 0.74 |
| | Q1 | 95.5% [94.4, 96.3] | 0.0% | — | — | 0.061 | 196 | 1.00 / 1.00 |
| | Q1d | 94.8% [93.7, 95.7] | 0.0% | 100.0% (0.22) | 100.0% (0.063) | 0.070 | 148 | 1.00 / 1.00 |
| | Q2 | **92.7% [91.4, 93.8]** | 0.0% | — | — | 0.138 | 38.6 | 1.01 / 1.00 |
| | Q3 | **69.0% [66.9, 71.0]** | 0.0% | — | — | 1.239 | 0.477 | 1.00 / 0.88 |
| | Q4 | **42.6% [40.4, 44.8]** | 0.0% | — | — | 4.512 | 0.036 | 0.91 / 0.56 |
| | Q5 | 94.9% [93.8, 95.8] | 0.0% | — | — | 0.067 | 163 | 1.00 / 1.00 |
| A, 3σ, two-sided (P = 0.0027) | plain MC (Wilson) | 94.7% [93.6, 95.6] | 6.7% | — | — | 0.599 | 1 | 0.99 / — |
| | Q0 | 94.3% [93.2, 95.3] | 5.6% | 100.0% (0.046) | 100.0% (0.019) | 0.603 | 0.988 | 1.02 / 1.11 |
| | Q1 | **0.0% [0.0, 0.2]** | 0.0% | — | — | 0.500 | 1.44 | 0.50 / 0.50 |
| | Q1d | **23.8% [22.0, 25.8]** | 0.0% | 100.0% (0.22) | 100.0% (0.068) | 0.980 | 0.374 | 1.01 / 0.51 |
| | Q2 | **0.4% [0.2, 0.8]** | 0.0% | — | — | 0.501 | 1.43 | 0.50 / 0.50 |
| | Q3 | **92.3% [91.1, 93.5]** | 0.0% | — | — | 0.099 | 36.6 | 1.00 / 0.99 |
| | Q4 | **70.0% [68.0, 72.1]** | 0.0% | — | — | 0.978 | 0.376 | 0.98 / 0.81 |
| | Q5 | **94.0% [92.8, 95.0]** | 0.0% | — | — | 0.061 | 95.5 | 1.00 / 1.00 |
| A, 4σ, upper (P = 3.17e-05) | plain MC (Wilson) | 96.4% [95.5, 97.2] | 96.4% | — | — | 6.127 | 1 | 1.17 / — |
| | Q0 | **2.9% [2.2, 3.7]** | 97.2% | 100.0% (0.043) | 100.0% (0.012) | 5.392 | 1.29 | 0.92 / 0.00 |
| | Q1 | 94.6% [93.5, 95.5] | 0.0% | — | — | 0.072 | 7.23e+03 | 1.00 / 1.00 |
| | Q1d | 94.8% [93.8, 95.8] | 0.0% | 100.0% (0.21) | 100.0% (0.061) | 0.082 | 5.57e+03 | 1.00 / 1.00 |
| | Q2 | **92.0% [90.7, 93.1]** | 0.0% | — | — | 0.158 | 1.5e+03 | 1.01 / 1.00 |
| | Q3 | **63.7% [61.6, 65.9]** | 0.0% | — | — | 1.831 | 11.2 | 1.01 / 0.83 |
| | Q4 | **36.4% [34.3, 38.6]** | 0.0% | — | — | 3.087 | 3.94 | 0.80 / 0.47 |
| | Q5 | 94.9% [93.8, 95.8] | 0.0% | — | — | 0.086 | 5.09e+03 | 1.00 / 1.00 |
| A, 4σ, two-sided (P = 6.33e-05) | plain MC (Wilson) | **93.5% [92.3, 94.5]** | 93.5% | — | — | 4.244 | 1 | 1.09 / — |
| | Q0 | **6.1% [5.1, 7.2]** | 93.9% | 100.0% (0.043) | 100.0% (0.013) | 3.872 | 1.2 | 0.98 / 0.00 |
| | Q1 | **0.0% [0.0, 0.2]** | 0.0% | — | — | 0.500 | 72 | 0.50 / 0.50 |
| | Q1d | **0.7% [0.3, 1.1]** | 0.0% | 100.0% (0.21) | 100.0% (0.062) | 6.343 | 0.448 | 1.01 / 0.50 |
| | Q2 | **1.0% [0.6, 1.5]** | 0.0% | — | — | 0.502 | 71.3 | 0.50 / 0.50 |
| | Q3 | **91.3% [90.0, 92.5]** | 0.0% | — | — | 0.121 | 1.22e+03 | 1.00 / 0.98 |
| | Q4 | **67.5% [65.3, 69.5]** | 0.0% | — | — | 1.515 | 7.85 | 1.02 / 0.77 |
| | Q5 | 94.0% [92.9, 95.0] | 0.0% | — | — | 0.072 | 3.5e+03 | 1.00 / 1.00 |
| B, 3σ, upper (P = 0.00135) | plain MC (Wilson) | 95.5% [94.4, 96.3] | 26.2% | — | — | 0.860 | 1 | 1.00 / — |
| | Q0 | **74.1% [72.1, 76.0]** | 25.9% | 100.0% (0.044) | 100.0% (0.016) | 0.859 | 1 | 1.00 / 0.74 |
| | Q1 | 94.2% [93.0, 95.1] | 0.0% | — | — | 0.066 | 172 | 1.00 / 1.00 |
| | Q1d | 94.0% [92.9, 95.0] | 0.0% | 100.0% (0.22) | 100.0% (0.063) | 0.076 | 130 | 1.00 / 1.00 |
| | Q2 | **92.8% [91.6, 93.9]** | 0.0% | — | — | 0.139 | 38.5 | 1.01 / 1.00 |
| | Q3 | **25.4% [23.5, 27.3]** | 0.0% | — | — | 1.392 | 0.382 | 0.69 / 0.48 |
| | Q4 | **16.2% [14.6, 17.8]** | 0.0% | — | — | 1.277 | 0.454 | 0.43 / 0.25 |
| | Q5 | **93.8% [92.7, 94.8]** | 0.0% | — | — | 0.074 | 137 | 1.00 / 1.00 |

### Findings

1. **The guarantee intervals (Hoeffding, empirical Bernstein) kept their promise wherever they could be formed:** 100% [99.8, 100]
   in all 10 conditions (Q0 and Q1d). Their widths are about 10 to 7,000 times the target probability, so, as planned, they are not used to compare
   efficiency. Empirical Bernstein is about a third as wide as Hoeffding.
2. **When Q matches how the event happens, the normal-approximation interval is close to 95% and the efficiency gain is large.** For
   upper events Q1, Q1d and Q5 cover 93.8–95.5%, with efficiency 130–196 at 3σ and 5,000–7,200 at 4σ. For two-sided events Q5 (an
   up/down mixture) covers 94.0%, with efficiency 96 at 3σ and 3,500 at 4σ. Q2 is less efficient and covers 92–93%.
3. **A one-way Q on a two-sided event estimates about half the truth and covers 0–1%.** Q1 and Q2 almost never sample the lower event.
   The estimates look precise, so the pilot's relative standard error cannot detect this (the pilot estimate was also half).
   The defensive mixture (Q1d) makes the mean correct, but a run jumps only when it draws a lower event, so the normal interval covers
   only 24% (3σ) and 0.7% (4σ). Only the guarantee intervals are right here.
4. **Student-t (Q4), and the state-dependent mean (Q3) on upper events, have heavy-tailed weights and are unusable at N = 1,000.**
   Most runs underestimate (medians 0.25–0.88 of the truth) and rare runs give tens to 188 times the truth (the top 1% of runs hold
   7–22% of the total). Coverage is 16–70%, and the efficiency is below 1 in some conditions. Q3 on two-sided events covers 91–92% with
   efficiency 37 (3σ) and 1,200 (4σ): short of Q5 but usable. For model B, Q3 and Q4 average only 0.69 and 0.43 of
   the truth over 2,000 runs. Because the same rule averages close to the truth on model A's upper event (0.997 for Q3) and the failures
   take the form of a low median plus rare huge values, we read this as proposals with very large (possibly infinite) weight variance
   rather than an implementation error. This is an empirical judgement; finiteness of the weight variance was not checked. The pilot
   (1,000 paths × a few trials) could not tell and ranked Q3 and Q4 first among their candidates.
5. **Q0 (Q = P) matches plain MC:** efficiency 0.99–1.3, confirming that the IS aggregation (log space, zero weights) agrees with plain
   MC. Rare events often have no event (23% at 3σ upper, 93–97% at 4σ), and the normal interval is missing in those runs.
6. **Plain MC's Wilson interval falls short at 4σ two-sided: 93.5% [92.3, 94.5].** Events are almost never observed (0 events in 93.5%
   of runs); this is a property of extremely rare events, separate from the bernoulli results above.

### Results (N = 10,000)

The same 35 conditions were repeated 2,000 times each at N = 10,000 (10,000 plain-MC paths + 10,000 IS paths), seeding from
20270101, which does not overlap the N = 1,000 seeds. Run on 2026-10-23 to 25 (code `34370ff`; one reboot interrupted one
condition, which was rerun from the start). Results: [data/is_coverage_n10000_summary.json](data/is_coverage_n10000_summary.json).

"Shrink" is the relative RMSE at N = 1,000 divided by that at N = 10,000; with a finite weight variance it is about √10 ≈ 3.16.
Efficiency and the mean/median estimates are at N = 10,000.

| Problem | Q | Normal approx. N=1,000 | Normal approx. N=10,000 [CP interval] | Relative RMSE 1,000 → 10,000 (shrink) | Efficiency | Mean / median estimate (÷ truth) |
|---|---|---|---|---|---|---|
| A, 3σ, upper (P = 0.00135) | plain MC (Wilson) | 95.0% | 94.8% [93.7, 95.7] | 0.856 → 0.272 (×3.15) | 1 | 1.00 / — |
| | Q0 | 76.7% | 94.8% [93.7, 95.7] | 0.849 → 0.272 (×3.12) | 1 | 1.00 / 0.96 |
| | Q1 | 95.5% | **93.6% [92.4, 94.6]** | 0.061 → 0.020 (×3.08) | 187 | 1.00 / 1.00 |
| | Q1d | 94.8% | 94.5% [93.4, 95.5] | 0.070 → 0.023 (×3.09) | 143 | 1.00 / 1.00 |
| | Q2 | 92.7% | 94.3% [93.2, 95.3] | 0.138 → 0.043 (×3.17) | 39.2 | 1.00 / 1.00 |
| | Q3 | 69.0% | **65.6% [63.5, 67.7]** | 1.239 → 0.228 (×5.43) | 1.42 | 0.96 / 0.93 |
| | Q4 | 42.6% | **47.4% [45.2, 49.7]** | 4.512 → 0.875 (×5.15) | 0.0965 | 0.87 / 0.71 |
| | Q5 | 94.9% | 95.4% [94.4, 96.3] | 0.067 → 0.021 (×3.16) | 164 | 1.00 / 1.00 |
| A, 3σ, two-sided (P = 0.0027) | plain MC (Wilson) | 94.7% | 95.0% [93.9, 95.9] | 0.599 → 0.196 (×3.06) | 1 | 1.00 / — |
| | Q0 | 94.3% | 94.9% [93.8, 95.8] | 0.603 → 0.195 (×3.09) | 1.01 | 1.00 / 1.00 |
| | Q1 | 0.0% | **0.0% [0.0, 0.2]** | 0.500 → 0.500 (×1.00) | 0.153 | 0.50 / 0.50 |
| | Q1d | 23.8% | **93.7% [92.5, 94.7]** | 0.980 → 0.306 (×3.20) | 0.41 | 1.00 / 1.04 |
| | Q2 | 0.4% | **0.0% [0.0, 0.2]** | 0.501 → 0.501 (×1.00) | 0.153 | 0.50 / 0.50 |
| | Q3 | 92.3% | 94.0% [92.9, 95.0] | 0.099 → 0.034 (×2.94) | 33.8 | 1.00 / 1.00 |
| | Q4 | 70.0% | **82.2% [80.5, 83.9]** | 0.978 → 0.339 (×2.88) | 0.333 | 1.00 / 0.94 |
| | Q5 | 94.0% | 95.0% [94.0, 96.0] | 0.061 → 0.019 (×3.19) | 104 | 1.00 / 1.00 |
| A, 4σ, upper (P = 3.17e-05) | plain MC (Wilson) | 96.4% | 95.6% [94.6, 96.5] | 6.127 → 1.785 (×3.43) | 1 | 0.99 / — |
| | Q0 | 2.9% | **26.7% [24.7, 28.6]** | 5.392 → 1.731 (×3.11) | 1.06 | 0.97 / 0.00 |
| | Q1 | 94.6% | **93.6% [92.4, 94.6]** | 0.072 → 0.023 (×3.11) | 5.93e+03 | 1.00 / 1.00 |
| | Q1d | 94.8% | **94.0% [92.8, 95.0]** | 0.082 → 0.026 (×3.11) | 4.58e+03 | 1.00 / 1.00 |
| | Q2 | 92.0% | 94.3% [93.2, 95.3] | 0.158 → 0.050 (×3.18) | 1.29e+03 | 1.00 / 1.00 |
| | Q3 | 63.7% | **62.4% [60.2, 64.5]** | 1.831 → 0.308 (×5.94) | 33.5 | 0.96 / 0.90 |
| | Q4 | 36.4% | **40.5% [38.3, 42.6]** | 3.087 → 1.462 (×2.11) | 1.49 | 0.84 / 0.63 |
| | Q5 | 94.9% | 95.5% [94.5, 96.4] | 0.086 → 0.027 (×3.18) | 4.37e+03 | 1.00 / 1.00 |
| A, 4σ, two-sided (P = 6.33e-05) | plain MC (Wilson) | 93.5% | 97.3% [96.5, 98.0] | 4.244 → 1.248 (×3.40) | 1 | 1.00 / — |
| | Q0 | 6.1% | **48.4% [46.2, 50.7]** | 3.872 → 1.247 (×3.11) | 1 | 1.03 / 0.00 |
| | Q1 | 0.0% | **0.0% [0.0, 0.2]** | 0.500 → 0.500 (×1.00) | 6.22 | 0.50 / 0.50 |
| | Q1d | 0.7% | **6.2% [5.2, 7.4]** | 6.343 → 2.047 (×3.10) | 0.372 | 1.02 / 0.50 |
| | Q2 | 1.0% | **0.0% [0.0, 0.2]** | 0.502 → 0.501 (×1.00) | 6.22 | 0.50 / 0.50 |
| | Q3 | 91.3% | **92.3% [91.1, 93.5]** | 0.121 → 0.042 (×2.86) | 868 | 1.00 / 1.00 |
| | Q4 | 67.5% | **77.8% [75.9, 79.6]** | 1.515 → 0.428 (×3.54) | 8.52 | 1.00 / 0.92 |
| | Q5 | 94.0% | 95.2% [94.2, 96.1] | 0.072 → 0.023 (×3.19) | 3.08e+03 | 1.00 / 1.00 |
| B, 3σ, upper (P = 0.00135) | plain MC (Wilson) | 95.5% | 94.2% [93.1, 95.2] | 0.860 → 0.273 (×3.15) | 1 | 1.00 / — |
| | Q0 | 74.1% | 94.9% [93.8, 95.8] | 0.859 → 0.272 (×3.17) | 1.01 | 1.01 / 0.96 |
| | Q1 | 94.2% | 94.7% [93.6, 95.6] | 0.066 → 0.021 (×3.20) | 177 | 1.00 / 1.00 |
| | Q1d | 94.0% | 95.0% [93.9, 95.9] | 0.076 → 0.024 (×3.20) | 134 | 1.00 / 1.00 |
| | Q2 | 92.8% | 95.5% [94.4, 96.3] | 0.139 → 0.045 (×3.06) | 36.3 | 1.00 / 1.00 |
| | Q3 | 25.4% | **22.2% [20.4, 24.1]** | 1.392 → 0.933 (×1.49) | 0.0857 | 0.69 / 0.58 |
| | Q4 | 16.2% | **16.4% [14.8, 18.1]** | 1.277 → 1.213 (×1.05) | 0.0507 | 0.52 / 0.34 |
| | Q5 | 93.8% | 94.9% [93.8, 95.8] | 0.074 → 0.023 (×3.24) | 145 | 1.00 / 1.00 |

### Findings (N = 10,000)

1. **The guarantee intervals still cover 100% [99.8, 100] in all 10 conditions** and meet the §13 criterion. Mean widths shrank by
   about 1/3 (1/√10) for Hoeffding and by 1/4.6 to 1/9.8 for empirical Bernstein, which adapts to the variance and whose 1/N term
   fades at large N. They remain about 6 (3σ upper, Q1d) to 42 (4σ upper, Q0) times the target probability, so they are still not
   used to compare efficiency.
2. **Matched proposals (Q1, Q1d, Q2, Q5) shrink the relative RMSE by √10 (×3.06–3.24)**, as with a finite weight variance, and keep
   their efficiency (36–187 at 3σ, 1,300–5,900 at 4σ). Normal-approximation coverage is 94.9–95.5% for Q5 everywhere, and Q2 (upper)
   rose from 92–93% to 94.3–95.5%. **Q1 on upper events, however, covers 93.6–94.7%**; for model A at 3σ and 4σ the upper end of the
   Clopper-Pearson interval is 94.6%, slightly below nominal (94.2–95.5% at N = 1,000). Q1d at 4σ upper also falls just short (94.0% [92.8, 95.0]). The normal interval has no guarantee, and a
   larger N does not always bring it closer to 95%.
3. **A one-way Q on a two-sided event (Q1, Q2) still estimates 0.50 of the truth and covers 0% at N = 10,000.** The relative RMSE
   stays at 0.50 (×1.00): the cause is bias, which more samples cannot fix. The defensive mixture (Q1d) rose from 23.8% to 93.7% at
   3σ two-sided but only from 0.7% to 6.2% at 4σ (median still 0.50 of the truth): a lower event is drawn with probability
   β × P(lower) ≈ 6×10⁻⁶, so it is rarely drawn even with 10,000 paths.
4. **On upper events Q3 and Q4 do not improve with 10 times more paths (Q3 62–66%, Q4 40–47%, model B 16–22%).** For model B,
   Q3 and Q4 average only 0.69 and 0.52 of the truth over 2,000 runs, and their relative RMSE barely shrinks (×1.49, ×1.05) — the
   behaviour of an infinite or very large weight variance, consistent with the N = 1,000 reading. Model A's upper Q3 shrank faster than
   √10 (×5.4–5.9) only because a few huge values dominated the RMSE at N = 1,000; its median stays at 0.90–0.93 of the truth and its
   coverage did not rise. Q3 on two-sided events stays usable at 92.3–94.0%.
5. **Q0 (Q = P) still matches plain MC** (efficiency 1.00–1.06, relative RMSE ×3.09–3.17). At 3σ every run now sees events and
   coverage is 94.8–94.9%; at 4σ 52–73% of runs still see none, and coverage is 27–48%.
6. **Plain MC's Wilson interval covers 94.2–97.3% in all conditions**; 4σ two-sided, below nominal at N = 1,000 (93.5%), is 97.3%.

### Reproducing

```bash
OMP_NUM_THREADS=1 python3 -m tools.validation.coverage_experiment --reps 2000 --seed-base 20261101 \
  --scenarios $(python3 -m tools.validation.coverage_experiment --list | grep -o '^is_final_[^:]*_n1000') \
  --output-dir artifacts/coverage_final_n1000
```

N = 10,000 runs one condition at a time, so an interruption loses at most one condition (about 1 hour with 28 workers):

```bash
for name in $(python3 -m tools.validation.coverage_experiment --list | grep -o '^is_final_[^:]*_n10000'); do
  [ -f "artifacts/coverage_final_n10000/$name/summary.json" ] && continue
  OMP_NUM_THREADS=1 python3 -m tools.validation.coverage_experiment --reps 2000 --workers 28 --seed-base 20270101 \
    --scenarios "$name" --output-dir "artifacts/coverage_final_n10000/$name"
done
```

### Limitations

- A path takes 1.7–3.3 ms at N = 10,000 (28 workers); the 35 conditions took about 34 hours in total.
- Only A and B have a truth. Models C and D (U-turn) are compared with reference MC (`tools/validation/compare_to_reference.py`).
- Each Q is the single candidate chosen by the pilot. The Q3/Q4 results describe this tuning, not state-dependent means or Student-t in general.
