# 統計モードの流れを 1 本にする

作成: 2026-11-03。[探索の共通化の計画](exploration_unification_plan.md) の 3.3（G3 では案 B を選んで見送った部分）を、改めて行う計画。

## 進み具合

| 段階 | 状況 |
|---|---|
| S0 | 完了（`51efb7d`）。判断の記録に、名前つき領域・KDE の重みの DKW・同時評価・再開・4 件ずつの発行と、履歴 CSV・標本 CSV の中身を足した |
| S1〜S5 | 完了（`467a4d3`）。`orchestration/region_sampling.py` の `RegionSamplingStrategy` に、5 つの統計モードをまとめて移した。判断の記録（S0 の分と CSV を含む）は変わらない。設定は、スナップショットで前のランナーと同じことを確かめた |
| S6 | 完了。探索エンジンから統計モードを外し（1,480 → 871 行）、`binomial_mode.py`・`sprt_mode.py`・`ebstop_mode.py` を消した（`3e4c4d1`）。`--stopping-rule` を AWSIM の統計モードで使えるようにした（`composed_region`） |
| S7 | 完了。`docs/extending.md`（日英）、README の構成、発表資料の限界の行 |

### 計画からの変更

- **1 本目の既存の戦略に選択肢を足す代わりに、共通の統計の経路に戦略を 1 つ足した**（`RegionSamplingStrategy`）。
  `RandomParameterSamplingStrategy` とは、発行するケースの形（入力の列、`reason`）、引き方（numpy・捨てた点も番号を進める）、
  評価に渡す行（探索データを含む全体をサービスが `reason_pattern` で絞る）、最終報告の形がすべて違い、決定 1・3（判断の記録と文言を変えない）
  を守ると、選択肢ではなく別の標本の取り方になるため。固定パラメータと IID の戦略が別のクラスであるのと同じ位置づけで、
  戦略の登録、`--stopping-rule` の差し込み口（`StoppingRuleService`）、番号順の判断（`prepare_statistical_sample`）、
  指標の宣言の検査、解決しない標本の報告（`unresolved_payload`）は 1 本目と共通の部品を使う。
- 3 つの手法（二項・SPRT・EBStop）は、前のランナー 3 つの違い（要求、履歴の列、標本の保存先、文言）を表 1 つ（`_METHODS`）にまとめた。
- S3〜S5 を手法ごとに分けず、5 つを一度に切り替えた。どれも判断の記録が一致したため。
- `--stopping-rule` の下では、規則はこの実行の標本だけを見る（探索データは使わない）。標本の種類は `iid_uniform`（範囲は標本を引く前に決まる）。

## 0. 背景

統計モード（`binomial_ci` / `sprt` / `ebstop` / `dkw` / `dkw_fixed`）を動かす流れは、今は 2 本ある。

| | 1 本目: 入力を独立に引く対象の流れ | 2 本目: 探索の中の流れ |
|---|---|---|
| 使う対象 | dynamics・bernoulli など（`supports_iid_sampling`）、PRISM 型（固定パラメータ） | AWSIM、および G4 で探索を使う対象 |
| 戦略 | `iid_sampling`（`RandomParameterSamplingStrategy`）、`fixed_parameter_sampling`、`fixed_parameter_dkw` | `orchestration/exploration/active_learning.py` の中で `orchestration/{binomial,sprt,ebstop,dkw}_mode.py` のランナーを呼ぶ |
| 標本の取り方 | `random.Random(derive_seed(seed, sample_index, INPUT_STREAM))` で一様 | `np.random.default_rng(derive_seed(seed, draw_index, INPUT_STREAM))` で一様。領域に入らない点は捨て、捨てた分も引いた番号（`sampling_draw_index`）を進める |
| 自分の標本の見分け方 | `experiment_id` と `sampling_signature` | `experiment_id`（G3）と、サービス側の `reason_pattern`・`bounds`・`region` による絞り込み |
| 停止の判断 | 統計サービス、または `--stopping-rule` の規則（`StoppingRuleService` で同じ口に差し込む） | 統計サービス（同じ `evaluate_request` の口）。`--stopping-rule` は使えない（探索の戦略に `composed_as` が無い） |
| 番号順・やり直し・指標の宣言 | あり（H0〜H3） | あり（G3 で同じ仕組みを別に実装） |
| この流れにしか無いもの | `--stopping-rule` の 7 つの規則、厳密なモデル検査との比較（PRISM） | 下の 6 つ |

2 本目にしか無い機能:

1. 名前つきの領域（`--dkw-region` など）と、探索データからの範囲の自動決定（`initialize_bounds`）
2. 領域に入らない点を捨てる標本の取り方（`issue_region_aware_random_task`）
3. DKW の複数指標の同時評価（`--dkw-simultaneous`）
4. 探索データを KDE の重みで混ぜる DKW（`--dkw-pure-smc` なし。保証は「近似」）
5. 評価ごとの履歴 CSV（`*_history.csv`）と、停止時の標本 CSV（`*_samples.csv`）
6. 停止の理由の文言（`Binomial CI Complete: …` など）と、タスクの `reason`（`BINOMIAL_CI: Sampling (n)` など）

統計モードの中では、ガウス過程回帰は使っていない（`decide_next_target` は統計モードならすぐランナーへ渡す）。探索から受け取っているのは
**探索データ（データセットの行）** だけで、使い道は 1（範囲の自動決定）と 4（KDE の重み）である。したがって、統計モードは探索の戦略の外へ出せる。

## 1. 目的

- 統計モードの流れを 1 本目だけにする。2 本目の 6 つの機能は、1 本目の選択肢として移す。
- その結果、次のようになる。
  - `--stopping-rule` の規則が AWSIM の統計モードでも使える。
  - 新しい統計手法・停止規則・仕組みは 1 か所に足せばよい。
  - `active_learning.py` は探索のモードだけを受け持つ。
- `binomial_mode.py`・`sprt_mode.py`・`ebstop_mode.py` を消す。`DKWModeRunner`（`dkw_mode.py`）は、PRISM の `fixed_parameter_dkw` と被覆率の検証も使うので、
  1 本目の DKW の部品として残す。

## 2. 守ること

- **判断の記録を変えない**: `tests/unit/architecture/decision_traces.json` は、各段階で変更なしで通ること（下の決めてほしいこと 1 で「変えない」を選んだ場合）。
- **出力を変えない**: 履歴 CSV・標本 CSV の列と値、停止の理由の文言、最終報告（`FinalReport`）の中身。
- **再開を変えない**: 途中で止めて再開したとき、続きの標本の番号と引いた番号（`sample_index`・`sampling_draw_index`）が今と同じに続くこと。
- **CLI を変えない**: 今あるオプションの意味と既定値。新しく使えるようになるのは、AWSIM の統計モードでの `--stopping-rule` だけ。
- **1 本目の今の利用者を変えない**: dynamics・bernoulli・PRISM の判断の記録、`target_snapshot.json`、重点サンプリングの照合（55 件と実験の再現）。
- 旧来のルートのスクリプト（BBSL の旧経路、`strategist.py`、`estimator.py`）は触らない（最後にまとめて消す）。

## 3. 設計

### 3.1 標本の取り方を部品にする

1 本目の `RandomParameterSamplingStrategy` は、今は `sample_parameters(sample_index)` の中で一様分布から直接引く。これを「入力の引き方」の部品に分ける。

```
InputSampler
  draw(sample_index, draw_state) -> (point, new_draw_state)
  identity() -> dict          # experiment_id の計算に入れる
```

- `IndependentUniformSampler`: 今の 1 本目の引き方（`random.Random`、`sample_index` から直接）。既定。
- `RegionRejectingUniformSampler`: 今の 2 本目の引き方。`np.random.default_rng(derive_seed(seed, draw_index, INPUT_STREAM))` で
  範囲から一様に引き、領域に入らない点は捨てて `draw_index` を進める。`draw_base`（再開時は記録の `sampling_draw_index` の最大 + 1）も持つ。
  領域の判定は、今の `region_policy` と対象の領域の語彙（P5）を使う。
- 対象は、統計モードで使う引き方を plugin で選ぶ（AWSIM は `RegionRejectingUniformSampler`）。共通側は対象の名前で分けない。

### 3.2 範囲の自動決定を部品にする

`initialize_bounds`（4 つのランナーでほぼ同じコード）を 1 つの関数にまとめ、1 本目の戦略の組み立てで呼ぶ。

- 入力: データセット（探索データを含む）、名前つきの領域、対象の領域の語彙、指定された範囲（`--dkw-bounds` など）
- 出力: 統計モードで引く範囲。今と同じ規則（`custom` か範囲の指定があればそれ、無ければ領域に入る探索データの各入力の最小〜最大）

### 3.3 記録と文言を差し込めるようにする

- 1 本目の戦略に、任意の「記録先」を足す: 評価ごとの履歴（`statistical_history_repository`）、停止時の標本（`statistical_samples_repository`）。
  渡されなければ今の 1 本目と同じ（書かない）。
- 停止の理由とタスクの `reason` の文言は、統計手法ごとの小さな表にまとめる（今の 2 本目の文言をそのまま持つ）。1 本目の今の文言は、
  1 本目の対象の既定として残す。どちらを使うかは、引き方と同じく plugin が選ぶ。

### 3.4 自分の標本の選び方をそろえる

- 2 本目は、統計サービスに全データを渡し、`reason_pattern`・`bounds`・`region` で絞らせている。1 本目は `experiment_id` で自分の行だけを渡す。
- G3 以降、2 本目の標本にも `experiment_id` が付いているので、1 本目の方式（`experiment_id` で選んでから渡す）にそろえる。
- ただし今の 2 本目と同じ行が選ばれることを、S0 の記録で確かめてから切り替える（G3 より前の記録で `experiment_id` が無い行の扱いを含む）。
- KDE の重みで探索データを混ぜる DKW だけは、自分の標本に加えて探索データの行も渡す（今と同じ）。

### 3.5 DKW

- 1 本目の DKW は、今の `fixed_parameter_dkw`（`FixedParameterDkwStrategy`）と同じく `DKWModeRunner` を使う形にする。
  引き方を 3.1 の部品にすれば、AWSIM の `dkw`・`dkw_fixed` も同じ戦略で動く。
- 複数指標の同時評価（`--dkw-simultaneous`）と KDE の重み（`--dkw-pure-smc` なし）は、`DKWModeRunner` が今持っている機能をそのまま使う。
  保証の表示（KDE の重みなら「近似」）も今のまま。

### 3.6 戦略の選び方

- AWSIM の plugin の `select_strategy` は、統計モードなら 1 本目の戦略（`iid_sampling`、DKW なら新しい `random_parameter_dkw`）を返す。
  探索のモードは今のまま `active_learning`。
- 1 本目の戦略には `composed_as` があるので、AWSIM の統計モードでも `--stopping-rule` が使えるようになる（`RULE_MODES` の検査はそのまま）。
  AWSIM の `min_ttc` のように範囲の無い指標で経験的 Bernstein 停止則を選ぶと、今と同じく実行前にエラーになる（指標の宣言）。
- `orchestrator._resolve_target_total` などの統計モードの扱いは、1 本目の戦略の扱い（自分で止まる戦略）にそろう。

### 3.7 消すもの

- `active_learning.py` の統計モードの部分: ランナーの組み立て（`from_runtime_config` の 4 か所）、状態の同期（`_sync_*` の 8 つ）、
  `_handle_*_mode` の 5 つ、番号付け（`_init_statistical_numbering`・`_number_statistical_payload`・`_prepare_statistical_dataset`）、
  `decide_next_target` と `next_test_case` の統計モードの分岐。
- `orchestration/binomial_mode.py`・`sprt_mode.py`・`ebstop_mode.py` と、その単体テスト（中身の確認は 1 本目のテストへ移す）。
- `orchestration/__init__.py` の再 export は、使っている所が無いことを確かめて消す。

## 4. 段階

| 段階 | 内容 | 確かめ方 |
|---|---|---|
| S0 | 今の動きを固定する。判断の記録に足すもの: 名前つきの領域で範囲を自動で決める `binomial_ci`・`sprt`・`dkw`、KDE の重みの DKW（`--dkw-pure-smc` なし）、`--dkw-simultaneous`、途中で止めて再開する場合、G4 の一時的な対象での `binomial_ci`。あわせて、履歴 CSV・標本 CSV・停止の理由・最終報告の中身も記録に入れる | 今のコードで記録を作る（コードは変えない） |
| S1 | 3.1 の引き方の部品と、3.2 の範囲の自動決定を 1 本目に足す。既定は今の 1 本目のまま | 1 本目の判断の記録・スナップショット・重点サンプリングの照合が変わらない。新しい部品の単体テスト（2 本目の `get_random_point` と同じ点を同じ順に出す） |
| S2 | 3.3 の記録先と文言、3.4 の標本の選び方を 1 本目に足す。既定は今の 1 本目のまま | 同上。履歴 CSV・標本 CSV を、2 本目と同じ入力から同じ中身で書く単体テスト |
| S3 | AWSIM の `binomial_ci` を 1 本目へ切り替える（3.6）。2 本目のコードはまだ消さない | S0 の `binomial_ci` の記録（領域・再開を含む）が変わらない。偽のクラスターで、届く順序を入れ替えても判断が同じ |
| S4 | `sprt` と `ebstop` を切り替える | 同上（`ebstop` は G4 の一時的な対象で確かめる。AWSIM は実行前のエラーが同じ文言で出ること） |
| S5 | `dkw` と `dkw_fixed` を切り替える（3.5）。KDE の重みと同時評価を含む | S0 の DKW の記録（KDE・同時評価・再開）が変わらない。PRISM の `fixed_parameter_dkw` と被覆率の検証の DKW の経路が変わらない |
| S6 | 2 本目を消す（3.7）。AWSIM の統計モードで `--stopping-rule` を使えるようにし、その動作確認を足す | 全テスト。`decision_traces.json` が変わらない。`target_snapshot.json` の差分は、消したクラス・移したクラスのモジュールのパスだけ |
| S7 | 文書: `docs/extending.md`（日英）、`docs/exploration_unification_plan.md` の 3.3 の状況、README の構成、発表資料の 13 枚目の限界（「統計の経路が 2 本」を消す） | — |

各段階で `tests/unit`・`tests/regression`・`tests/smoke` を全部流してコミットする。最後に release の build と test を流す（公開はしない）。

## 5. 危ないところ

- **標本の選び方（3.4）**: 2 本目はサービスの側で `reason_pattern`・`bounds`・`region` によって絞っている。`experiment_id` での選び方に変えると、
  G3 より前の記録の再開や、領域の境目の点の扱いで、選ばれる行が変わる可能性がある。S0 の記録と、選ばれた行の一致の比較で確かめる。
  一致しない場合は、2 本目の絞り方を 1 本目の選択肢として持つ。
- **評価の時期**: 2 本目はタスクを 1 つ出すたびに評価し、1 本目は `next_test_case` のたびに評価する。キューの深さ（`--queue-high-water`）との
  関係で評価の回数が変わると、逐次の手法（SPRT、anytime の区間）の判断が変わりうる。S0 の記録は既定のキューの深さと 1 の両方で取る。
- **DKW の `dkw_fixed` の発行数の同期**（`_sync_dispatched_task_count` の `dkw_pure_smc` の分岐）: 1 本目の数え方と違う可能性がある。S5 で個別に確かめる。
- **作業量**: 2 本目の 6 つの機能を 1 本目に作り直すので、今回の計画のなかでいちばん大きい。S3 までで止めても、各段階の時点で動く状態に保つ。

## 6. 決めてほしいこと

1. **AWSIM の統計モードの標本の引き方**: 今の 2 本目の引き方（numpy、捨てた点も番号を進める）を部品として残し、判断の記録を変えない（推奨）か、
   1 本目の引き方にそろえて記録を作り直すか。そろえる場合、統計的な正しさは変わらない（どちらも独立な一様標本）が、AWSIM の統計モードの
   判断の記録（4 件）と、AWSIM で取った統計モードの結果との再現性が変わる。
2. **AWSIM の統計モードで `--stopping-rule` を使えるようにするか**: 1 本にすると自然に使えるようになる。使えるようにする（推奨）か、今と同じく拒否するか。
3. **停止の理由とタスクの `reason` の文言**: 対象ごとに今の文言を保つ（推奨）か、1 本目の文言にそろえるか。そろえると、ログを読む道具
   （`BINOMIAL_CI:` などの行を探すもの）があれば直す必要がある。
