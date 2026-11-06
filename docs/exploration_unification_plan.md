# AWSIM の GP 探索と統計モードの共通化 計画

作成: 2026-11-02（ローカル `6574f0b` 時点）

## 進捗

| 段階 | 状態 |
|---|---|
| G0 | 完了（`6c3ada3`）。AWSIM の全探索モードの判断の記録を追加（抽出によるモードは 300 行の決まった過去データから始める。新しいモードは全件を記録） |
| G1・G2 | 完了（`0a3d5df`）。GP 探索の本体を `orchestration/exploration/` へ移し、`ExplorationSpec`（学習データの規則、失敗した回の判定、抽出の規則）で対象の知識を受け取る。AWSIM の規則は `targets/awsim/exploration.py`。全モードの判断の記録は同一。抽出の規則は、その後 `targets/awsim/point_rules.py` へ移した（`docs/point_extractors_split_plan.md`） |
| G3 | 完了（`cddaf10`、案 B）。統計モードの今の経路（`orchestration/*_mode.py`）を残し、探索エンジンが統計の標本に実行の ID・番号・決まった抽選番号を付け、番号順の連続した部分だけを渡す。指標の宣言の外の値で停止。統計モードでは失敗した回を近くの点で探し直さない（キューが同じ点でやり直す）。再開時は番号と抽選番号を続ける。判断の記録は AWSIM の統計モードの 4 件だけが変わった（合意した seed の変更） |
| G4 | 完了（`724a5c1`）。`orchestration/exploration/builder.py`（どの対象でも使える組み立て）。一時的な対象 `slope` が `targets/` の下だけで GP 探索と統計モードを使えることをテストで確認 |
| G5 | 完了。`docs/extending.md`（日英）に GP 探索の使い方 |
| 3.3 | 完了（2026-11-03、[statistics_single_path_plan.md](statistics_single_path_plan.md)）。統計モードは探索エンジンから共通の統計の経路（`orchestration/region_sampling.py`）へ移った |

G3 の問題: AWSIM の統計モードの今の経路（`orchestration/{binomial,sprt,dkw,ebstop}_mode.py`）には、共通の IID 型の経路に無い機能がある。
名前つき領域とその範囲の自動決定、DKW の複数指標の同時評価（`--dkw-simultaneous`）、KDE の重みでの探索データの再利用、評価ごとの履歴 CSV
（`*_history.csv`）、停止時の標本 CSV（`*_samples.csv`）、DKW の指標の設定（`DKW_TARGET_METRIC`、U ターンは `z_margin`）。
計画どおり共通の IID 型の経路へ移すと、これらを作り直さない限り失われる。

## 1. 目的

AWSIM の探索戦略（`targets/awsim/strategies/active_learning.py`、1,325 行）は、GP による探索と統計モードを 1 つのクラスで
受け持っている。中身の大部分は汎用のアルゴリズムで、AWSIM の知識は一部に限られる。これを分け、

- **GP 探索**（`explore` / `focus` / `margin`）を共通基盤の探索エンジンにし、どの対象も入力空間とラベルを宣言するだけで使えるようにする。
- **統計モード**（`binomial_ci` / `sprt` / `dkw` / `dkw_fixed` / `ebstop`）を、AWSIM でも共通の IID 型の経路で動かす。これで、
  番号順に連続した標本だけで判断する仕組み（`docs/statistical_modes_hardening_plan.md` の H1）、やり直し・出し直し（H3・H4）、
  指標の宣言（H2）が、AWSIM の統計モードにもそのまま効く。
- **AWSIM 固有の探索**（`jama_edge` / `ttc_edge` / `worst_ttc` / `verify_consistency` / `boundary_gap`）は AWSIM の plugin に残し、
  共通の探索エンジンの上に載せる。

## 2. 今の状態（2026-11-02 の調査）

| 部分 | 中身 | AWSIM 固有か |
|---|---|---|
| 3 段階の GP 探索、候補の生成、境界の安定判定、focus 点のくり返し、最終報告 | `active_learning.py` | 汎用（AWSIM の名前は使っていない。入力名は設定の `PARAM_RANGES` から来る） |
| 学習データの整形 | `evaluation/gp_boundary.py:72-117` | AWSIM 固有の規則が埋め込まれている（`c_*` の -1 を除く、`c_collision` → `c_ttc_*` の伝播、`min_ttc < 1.5`・`min_distance < 2.0` を重要な行とする） |
| 異常な結果の扱い | `active_learning.py:857-909` | AWSIM 固有（`min_ttc == -1` / `c_collision == -1` をエラーとみなす） |
| 抽出による探索、名前つき領域（`emp_safe` など）、一貫性の分類 | `orchestration/point_extractors.py`（421 行） | AWSIM 固有（`c_collision`、`min_ttc`、`theory_margin_a_human`） |
| 統計モード | `orchestration/{binomial,sprt,ebstop,dkw}_mode.py` を戦略が呼ぶ | 汎用。ただし AWSIM の探索の記録と同じ CSV を読み、`reason` の文字列で選り分け、順序は `loop_num`。番号順の連続した部分、やり直し、指標の宣言は効いていない |
| 統計モードの標本の取り方 | `PARAM_RANGES`（または `dkw_bounds`）の一様分布から引き、領域に入らない点を捨てる（`binomial_mode.py:139-157`） | 汎用（共通の IID 型の経路とほぼ同じ。違いは領域による棄却と、seed の番号の付け方） |
| DKW の探索データの再利用 | `--dkw-pure-smc` が無いと、探索の記録を KDE の重みで混ぜる | 汎用。保証はない（被覆率は 11.7%、`docs/validation/coverage.md`） |
| 他の対象での利用 | なし。`DKWModeRunner` だけは PRISM の固定パラメータの DKW が使う | |
| 旧来の `strategist.py`（1,402 行） | BBSL の旧経路（`master_orchestrator.py` など）が使う別の実装 | 今回は扱わない（§8） |

## 3. 設計

### 3.1 共通の探索エンジン（新規 `orchestration/exploration/`）

`active_learning.py` の GP 探索の部分を、対象の名前を持たない形で移す（中身は変えない）。対象は plugin の任意の口
`exploration`（遅延 import の property）で次を宣言する。

| 宣言 | 内容 | AWSIM での値 |
|---|---|---|
| 入力空間 | 連続の入力の名前と範囲、固定の入力 | 設定モジュールの `PARAM_RANGES`、`FIXED_PARAMS` |
| ラベル | 境界を学ぶ 0/1 の指標の候補（先頭から、0 と 1 の両方を含む最初のもの） | `TARGET_PRIORITIES`（`c_collision`、`c_ttc_*`） |
| 学習データの規則 | 使わない値（例: -1）、ラベル間の伝播、重要な行の条件 | 今の `gp_boundary.py` の AWSIM の規則をここへ移す |
| 異常な結果 | エラーとみなす値 | `min_ttc == -1`、`c_collision == -1` |
| 探索の設定 | 段階の上限、focus 点、余白の基準など | 今どおり設定モジュールから |

GP の部分（`GPBoundaryService`）は、学習データの規則を引数で受け取る形にする（`gp_boundary.py` から AWSIM の列名を除く）。

### 3.2 AWSIM 固有の探索は plugin に残す

`jama_edge` / `ttc_edge` / `worst_ttc` / `verify_consistency` / `boundary_gap` と、名前つき領域・一貫性の分類
（`point_extractors.py` の AWSIM の部分）は `targets/awsim/exploration/` へ移す。これらは「くり返す点を選ぶ規則」として共通の
探索エンジンに渡す（エンジンは、渡された点を決まった回数くり返してから GP 探索に移る、という今の流れを共通に持つ）。

### 3.3 統計モードは共通の IID 型の経路で動かす

- AWSIM の `binomial_ci` / `sprt` / `dkw` / `dkw_fixed` / `ebstop` は、AWSIM の探索戦略を通さず、共通の IID 型の戦略
  （`random_parameter_sampling.py`）で動かす。入力の分布は `PARAM_RANGES`（`--dkw-bounds` などの範囲指定があればそれ）の一様分布。
- 共通の IID 型の戦略に **領域による棄却** を足す。対象の領域の方針（`StatisticalRegionPolicy`。AWSIM は `awsim_theory`）で、
  領域に入らない点を捨てて引き直す（= 領域で切り詰めた一様分布からの標本。入力だけで決まる条件なので偏らない）。
- これで、番号順の連続した部分、やり直し・出し直し、指標の宣言、`--stopping-rule` の各規則が、AWSIM の統計モードでもそのまま使える。
  保証の表示は、IID 型の標本なので、規則の保証どおりになる（今は AWSIM の統計モードの保証の表示が不十分）。
- 統計モードの記録は、探索の記録と `experiment_id` で分けて保存する（今は同じ CSV を `reason` の文字列で選り分けている）。
- 探索データを KDE の重みで混ぜる DKW（`--dkw-pure-smc` なし）は残す（決定 2）。共通の IID 型の経路でも、`--dkw-pure-smc` が無ければ、
  同じ対象の探索の記録を KDE の重みで加えて評価する。保証の表示は今どおり「近似」（`approximate_kde`）。
  加える探索の記録は、新しい標本と同じ入力の範囲・領域に入るものに限る（今の動きと同じ）。

### 3.4 変わること（決めてほしいこと、§7）

- **AWSIM の統計モードの数値が変わる。** 標本の seed の決め方が、`loop_num` に基づくもの（探索の記録の件数に依存する）から、
  共通の `sample_index` に基づくものに変わるため。同じ設定で同じ標本を引くことは、新しい決め方のもとで保たれる。
  判断の記録（`awsim:binomial_ci`、`sprt`、`dkw`、`dkw_fixed`）は作り直しになる。
- **探索データを KDE の重みで混ぜる DKW（`--dkw-pure-smc` なし）** は残す（決定 2）。共通の IID 型の経路に、この扱いを足す。

## 4. 守ること

- GP 探索（`explore` / `focus` / `margin`）と AWSIM 固有の探索は、移した後も判断を変えない。判断の記録 `awsim:explore` と
  スナップショット（`target_snapshot.json` の `awsim:*`）は、モジュールの場所の違いを除いて同一。
- PRISM の固定パラメータの DKW（`DKWModeRunner` を使う）は変えない。
- dynamics・PRISM・bernoulli の判断の記録、被覆率の基準、本番の実験の再現（`tools.validation.check_experiment_replay`）は同一。

## 5. 進め方

| 段階 | 内容 | 終わりの条件 |
|---|---|---|
| G0 | 基準: AWSIM の全モードの判断の記録を足す（今は `binomial_ci`・`sprt`・`dkw`・`dkw_fixed`・`explore` だけ。`focus`・`margin`・抽出による探索・`boundary_gap`・`verify_consistency` を足す） | 記録ができる |
| G1 | 共通の探索エンジン（3.1）。GP の学習データの規則を引数にし、AWSIM の規則を AWSIM の宣言へ移す | `awsim:explore` などの判断の記録が同一 |
| G2 | AWSIM 固有の探索を plugin へ（3.2） | 同上 |
| G3 | AWSIM の統計モードを共通の IID 型の経路へ（3.3）。領域による棄却を共通の戦略に足す。AWSIM の統計モード用の旧経路（戦略の中の振り分け）を外す | 統計モードの判断の記録を作り直し、差分が seed の決め方による標本の違いだけであること。偽のクラスター（届く順序の入れ替え）で、番号順の判断が効くこと |
| G4 | アーキテクチャのテスト: `targets/` の下だけに置いた一時的な対象が、入力空間とラベルを宣言するだけで GP 探索を使える | 共通部分を変えずに通る |
| G5 | 文書（`docs/extending.md`、`docs/statistical_method_selection.md`、この計画の進捗） | |

各段階でテストを全部流してコミットする。公開版への反映は指示を受けてから。

## 6. 範囲外

- 旧来の `strategist.py`（BBSL の旧経路が使う別の実装、1,402 行）と `estimator.py`。使われ方を確かめてから別に扱う。
- GP のモデルそのものの改良（今は 0/1 のラベルへの回帰。分類器への置き換えなど）。
- カテゴリの入力（レーンなど）を探索の対象にすること（今どおり固定の入力）。

## 7. 決めたこと

| 項目 | 決定 |
|---|---|
| 1. AWSIM の統計モードの seed の決め方の変更 | 受け入れる（2026-11-02）。調べた結果: 統計的な正しさは変わらない（どちらも独立な一様標本）。今の決め方は、番号が「次のケースを出す時点で届いていた結果の `loop_num` の最大値 + 出した数」で、クラスターでは結果の届き方で標本が変わり再現できない。新しい決め方は何番目の標本かだけで決まり、クラスターでも再現できる。手元に AWSIM の統計モードの記録（`BINOMIAL_CI:` などの行）は無く、文書にも数値の結果は無い。作り直しになるのはテスト用の判断の記録 4 件（`awsim:binomial_ci`・`sprt`・`dkw`・`dkw_fixed`）だけ。被覆率の検証の DKW の経路は `DKWModeRunner` を直接使うので変わらない |
| 2. KDE の重みで探索データを混ぜる DKW | 残す（2026-11-02）。統計モードの選択肢として共通の経路にも入れ、保証の表示は「近似」のまま |
| 3. 旧来の `strategist.py` などの旧版 | 今回は残す（2026-11-02）。旧版の多くは使っていないが、新しい版が旧版の機能をきちんと再現していない所があるため残してある。最後にまとめて消す。消す前に、旧版にあって新しい版に無い機能を洗い出す |
