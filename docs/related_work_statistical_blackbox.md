# 関連研究との比較：ブラックボックスの対象に対する統計手法の共通基盤

作成: 2026-11-02。発表用スライド（`接続構造と再利用範囲の比較_まとめ修正版.pptx`）の続きとして、**統計手法の側** が
ブラックボックスの対象をどこまで共通に扱えるかを比べる。スライドの比較（接続の構造、統計処理の再利用）を前提にする。

確度の表記: 「論文」は論文・公式資料で確認、「スライド」は既存スライドの記述、「未確認」は確認できていない（無いという意味ではない）。
スライドに使う前に、「未確認」の項目は各論文の本文で確かめる。

## 1. 比べる観点

ブラックボックス（対象の内部の状態・モデルを見られず、入力を与えて実行し結果を受け取るだけ）に対して、統計手法が成り立つために必要なこと。

| 観点 | 問い |
|---|---|
| A. 統計側が対象に求めるもの | 統計手法は、対象から何を受け取れば動くか（判定値だけか、状態・経路・尤度比まで要るか） |
| B. 希少事象（重点サンプリング） | 提案分布 Q をどこで定義し、尤度比を誰が計算するか。ブラックボックスでも使えるか |
| C. 希少事象（重点分割） | 途中の状態からの再開やスコア関数が要るか |
| D. 並列実行の偏り | 結果が届いた順に評価すると、短い（早く終わる）試行に偏る問題を扱うか |
| E. 失敗した試行 | シミュレータが落ちた試行をどう扱うか |
| F. 手法と標本・指標の適合 | 手法の前提（独立な標本、値の範囲、重みの上限）を、実行前・実行中に確かめるか |

## 2. 各研究

### PLASMA-lab（Legay ほか）
- A: 統計アルゴリズムはプラグインで、Checker が状態・経路の API を使う（スライド）。
- B: 重点サンプリングは、**モデルの記述（guarded command）の上で** 提案分布をパラメータ化し、交差エントロピー法で調整する。
  尤度比はシミュレータがモデルから計算する（論文: Jegourel ほか CAV 2012、TCS 2016 "Command-based importance sampling"）。
  → 提案分布の定義に **モデルの構文と遷移確率** が要る。ブラックボックスの対象には、そのままは使えない。
- C: 重点分割（Importance Splitting）も実装（論文: Jegourel ほか CAV 2013）。スコア関数と、途中の状態からの再開が要る。
- D・E・F: 未確認。著者ら自身、シミュレーションモデルが完全である保証が無いと誤差の上界を形式的に与えにくい、と述べている（論文）。

### MultiVeStA（Sebastio・Vandin ほか）
- A: Adaptor が初期化・進行・数値観測を提供し、基盤側でクエリを評価する（スライド）。2017 年の定常状態の拡張では、シミュレーションモデルを
  **ブラックボックスとして扱う** と明記（論文: Gilmore ほか IFM 2017）。
- 統計手法: 独立な試行を、信頼区間の半幅が指定値を下回るまで追加する。過渡・定常解析（論文: 2021 年の経済 ABM の論文ほか）。
- B・C: 重点サンプリング・分割は未確認。
- D: 分散実行するが、届く順序の扱いは未確認。
- E・F: 未確認。

### SBIP 2.0（Mediouni ほか、ATVA 2018）
- A: 実行・監視・解析を Java インターフェースで接続。監視は論理式と経路・記号列を受け取る（スライド）。
- 統計手法: 仮説検定・確率推定、希少事象（論文の要旨: "rare events" に対応）。
- C: 希少事象は重点分割（スライド。同じ研究グループの適応的重点分割の論文あり）。経路の断片とスコアが要る。
- B・D・E・F: 未確認。

### Demeter（Hansen ほか、RV 2016）
- A: Docker で試行を隔離し、試行ごとに性質の 0/1 を返す。Master が入力を配分・集約（論文・スライド）。対象側の作りに依らない点で、
  本研究と同じくブラックボックス寄り。
- 統計手法: 二値の確率と相対誤差。入力と判定結果へのロジスティック回帰（入力要因分析）が主な貢献（論文）。
- B: Q から引いた結果を P へ補正する重み付き評価は、標準の契約に無い（スライド）。
- C・D・E・F: 未確認。

### VerifAI（Dreossi ほか、CAV 2019）
- A: Sampler・Simulator・Monitor をサーバ／クライアントで接続し、判定値を探索・反例解析へ使う（スライド）。ブラックボックスの
  シミュレータ（CARLA など）を扱う。
- 統計手法: 標準は、反例数と試行数からの二項区間（スライド）。Sampler には交差エントロピー・焼きなまし・ベイズ最適化（能動的）、
  一様・Halton（受動的）がある（公式資料）。
- B: 交差エントロピーの Sampler は **反例を探すための** 分布の適応で、P の確率を推定するための重み補正は標準の区間関数に無い（スライド）。
  能動的な Sampler の標本で二項区間を作ると、P のもとでの確率の区間にはならない。
- C・D・E・F: 未確認。

### modes（Budde ほか、TACAS 2018 / STTT 2020）
- 対象: Modest・JANI などの **形式モデル**（スライド）。ブラックボックスではない。
- C: 重点分割を自動化（論文）。モデルの構造からスコア関数を作るので、モデルが見えることが前提。
- D: **並列実行で、届いた順に評価すると偏る** と明記。失敗する試行ほど早く戻るので、失敗確率を過大に見積もる（論文: STTT 2020）。
  分散実行の偏りを防ぐ仕組みを持つ（関連の学位論文）。

### 先行研究の要点（比較の背景として）
- **Younes, "Probabilistic Verification for 'Black-Box' Systems"（CAV 2005）**: ここでの「ブラックボックス」は、
  **新しい試行を生成できず、手元の観測だけで検証する** 意味。本研究のブラックボックス（実行はできるが、内部は見えない）とは意味が違う。
- **並列実行の偏りは古くから知られた問題**: Younes の Ymer（CAV 2005）は、結果を到着順ではなく決まった順（ラウンドロビン）で
  処理して偏りを防いだ。UPPAAL-SMC も「素朴な分散実装は短い試行の結果に偏る」と指摘している。

## 3. 本研究との違い

| 観点 | 本研究 | 先行研究 |
|---|---|---|
| A. 対象に求めるもの | 判定値（指標）だけ。内部の乱数に重点サンプリングを使う対象だけが、引いた乱数の P と Q の各成分での**対数密度**を返す（capability `internal_density`） | 形式モデル系（PLASMA-lab、SBIP、modes）は状態・経路・モデルの構文。Demeter・VerifAI は判定値だけ（本研究に近い） |
| B. 重点サンプリング | **入力の分布の P・Q は共通基盤が持ち、尤度比も共通基盤が計算する**。対象の内部の乱数は、対象が対数密度を返すだけで、混合・防御的重み・重みの上限は共通基盤が計算。AWSIM のような完全なブラックボックスでも入力の重点サンプリングが使える | PLASMA-lab は提案分布をモデルの命令の上で定義し、シミュレータが尤度比を計算（モデルが見える前提）。Demeter・VerifAI の標準には P への重み補正が無い |
| 保証の区別 | 重みの上限が分かる場合だけ保証用の区間（Hoeffding、経験的 Bernstein）を作り、正規近似は診断用と区別。真値の分かるモデルで被覆率を 2,000 回くり返して測定（`docs/validation/coverage.md`） | 重点サンプリングの誤差の上界は難しいと PLASMA-lab の著者自身が述べている。被覆率の測定は未確認 |
| C. 重点分割 | **未対応**（状態の保存・再開が必要。今後の capability `checkpoint_restart` として整理済み） | PLASMA-lab・SBIP・modes が対応（いずれも状態・スコアが見える前提） |
| D. 並列実行の偏り | 標本に発行時の番号を付け、**番号順に欠けずにそろった部分だけ** で判断する（重点サンプリング、統計モード、AWSIM の GP 探索の統計モード） | Younes（Ymer）・UPPAAL-SMC・modes が同じ問題を指摘し、決まった順序で処理する。本研究はこの先行研究と同じ考え方（新規性ではなく、正しさのための対応として示す） |
| E. 失敗した試行 | 同じ入力・同じ seed で 3 回までやり直し、全試行を記録。解決しない点があれば保証の表示を「近似」に下げ、それらを全部 0・全部 1 とした区間（Horowitz & Manski 2000）を報告 | 未確認（比べた範囲の資料に記述が見当たらない） |
| F. 手法の前提の検査 | 指標の性質（0/1・範囲つき・範囲なし）を対象が宣言し、合わない手法は実行前にエラー、宣言の外の値は区間を出さずに停止。標本の種類と停止規則から保証の有無を表示 | 未確認 |

**本研究の位置づけ（スライドでの言い方の案）**
- 先行研究の多くは、統計手法の効率化（重点サンプリング・重点分割）を **対象のモデルが見える前提** で実現している。
- 本研究は、対象を **実行はできるが内部は見えない** ブラックボックスとして扱い、統計手法が必要とする量（判定値、必要なら対数密度）
  だけを対象から受け取り、それ以外（尤度比の合成、保証の判定、並列実行の順序、失敗の扱い、前提の検査）を共通基盤に集めた。
- 代わりに、重点分割のように対象の状態に手を入れる手法は、まだ扱えない。

## 4. スライドに使う前に確かめること

1. MultiVeStA・SBIP 2.0・Demeter・VerifAI の、並列実行の順序（D）と失敗した試行（E）の扱い（本文・実装で確認）。
2. modes の偏り対策の具体的な方式（STTT 2020 の本文）。
3. VerifAI の 2026 年の研究（スライドの「IS・KDE を用いる合成的 SMC」）の出典と、尤度比の扱い。今回の検索では見つからなかった。
4. SBIP 2.0 の重点分割の実装（論文の本文）。

## 出典

- Jegourel, Legay, Sedwards. Cross-entropy optimisation of importance sampling parameters for statistical model checking. CAV 2012. https://arxiv.org/abs/1201.5229
- Jegourel, Legay, Sedwards. Command-based importance sampling for statistical model checking. TCS 2016. https://repository.sutd.edu.sg/esploro/outputs/journalArticle/Command-based-importance-sampling-for-statistical-model/9911507709846
- Jegourel, Legay, Sedwards. Importance splitting for statistical model checking rare properties. CAV 2013. https://link.springer.com/content/pdf/10.1007/978-3-642-39799-8_38.pdf
- Boyer, Corre, Legay, Sedwards. PLASMA-lab: A flexible, distributable statistical model checking library. QEST 2013（スライド［1］）
- Sebastio, Vandin. MultiVeStA. ValueTools 2013. https://eudl.eu/doi/10.4108/icst.valuetools.2013.254377
- Gilmore, Reijsbergen, Vandin. Transient and steady-state statistical analysis for discrete event simulators. IFM 2017. https://link.springer.com/chapter/10.1007/978-3-319-66845-1_10
- Vandin ほか. Automated and Distributed Statistical Analysis of Economic Agent-Based Models. https://arxiv.org/abs/2102.05405
- Mediouni ほか. SBIP 2.0: Statistical Model Checking Stochastic Real-time Systems. ATVA 2018. https://hal.archives-ouvertes.fr/hal-01888538
- Hansen ほか. Input Attribution for Statistical Model Checking Using Logistic Regression. RV 2016. https://www.sei.cmu.edu/library/input-attribution-for-statistical-model-checking-using-logistic-regression
- Dreossi ほか. VerifAI: A Toolkit for the Formal Design and Analysis of Artificial Intelligence-Based Systems. CAV 2019. VerifAI の Sampler: https://verifai.readthedocs.io/en/stable/samplers.html
- Budde, D'Argenio, Hartmanns, Sedwards. A Statistical Model Checker for Nondeterminism and Rare Events. TACAS 2018. https://link.springer.com/chapter/10.1007/978-3-319-89963-3_20 ／ STTT 2020: https://link.springer.com/article/10.1007/s10009-020-00563-2
- Younes. Probabilistic Verification for "Black-Box" Systems. CAV 2005. https://tempastic.org/papers/cav2005a.php
- Younes. Ymer: A Statistical Model Checker. CAV 2005. https://tempastic.org/papers/cav2005b.php
- David ほか. UPPAAL-SMC: Statistical Model Checking for Priced Timed Automata. https://arxiv.org/abs/1207.1272
- Horowitz, Manski. Nonparametric analysis of randomized experiments with missing covariate and outcome data. JASA 2000.
