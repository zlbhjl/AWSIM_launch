# `point_extractors.py` の分割 計画

作成: 2026-11-02（ローカル `bff14b4` 時点）

## 進捗（2026-11-02）

| 段階 | 状態 |
|---|---|
| P0 | 完了（`41ff24d`）。抽出・領域・boundary_gap・一貫性の分類の出力を固定 |
| P1〜P4 | 完了（`25ac915`）。P0 の出力、判断の記録、本番の実験の再現は同一 |
| P5 | 完了。`docs/extending.md`（日英）に領域の宣言 |

実装で計画と変えた点:
- 名前つき領域の名前は、共通の統計に渡す前に展開する代わりに、対象の語彙（`RegionVocabulary`）を統計の計算まで渡す形にした
  （必要な列と、文字列として比べる列も対象ごとに違うため）。語彙は、統計モードでは対象の領域の方針（`vocabulary` 属性）、GP 探索では
  `ExplorationSpec.region_vocabulary` から来る。
- 共通の統計モードの単体テストで AWSIM の領域名を使っていたものは、代わりの部品に AWSIM の語彙を持たせた（以前は共通の側が AWSIM の
  名前を知っていたので不要だった）。
- 新しい版が古いファイルを読み込まないことのテストで、古い BBSL の入口 `apps/cli/bbsl_local_ft4d_main.py`（古い `strategist.py` を使う）
  だけは例外にした。古い版と一緒に消す。

## 1. 目的

ルートの `point_extractors.py`（421 行）は古い版のファイルだが、新しい版の共通の統計も使っている。

| 使っている所 | 使っている機能 |
|---|---|
| 共通の統計の計算 `evaluation/{binomial_ci,sprt,dkw,ebstop}.py` | `filter_by_region_and_bounds`（条件式の領域と範囲で行を絞る） |
| 共通の統計モード `orchestration/{binomial,sprt,dkw,ebstop}_mode.py` | 同上（候補点の棄却、範囲の自動決定） |
| `runtime/repository/consistency_classification.py` | `save_dataframe_to_csv` |
| AWSIM の抽出モード（`targets/awsim/exploration.py` の `point_rules`） | 抽出の規則（`EXTRACTORS`）、boundary_gap の集計、一貫性の分類、CSV 保存 |
| 分析の道具 `tools/analysis/{analyze_boundary_gap,extract_region_data,analyze_ttc_consistency}.py` | 上のいくつか |
| 古い版 `strategist.py`・`estimator.py` | 全体 |

このため、

1. 最後に古い版を消すと、新しい版の共通の統計が動かなくなる。
2. 共通の統計の中に AWSIM の知識がある。名前つき領域（`emp_safe` = `c_collision == 0`、`jama_safe` = `theory_margin_a_human >= 0`、
   `intersect_safe`、`union_safe`）、その必要な列、文字列として扱う列（`theory_zone_a` など）が、共通のコードから直接使われている。

これを、汎用の部分（共通）、AWSIM の知識（AWSIM の宣言）、古い版のための互換の入口、に分ける。

## 2. 守ること

- 中身（計算）は変えない。置き場所と依存の向きだけを変える。
- 全モードの判断の記録（`tests/fixtures/architecture/decision_traces.json`）、スナップショット、被覆率の基準、本番の実験の再現
  （`tools.validation.check_experiment_replay`）が変わらない。
- 古い版（`strategist.py`、`estimator.py`、`master_orchestrator.py` など）と分析の道具は、今のまま動く（ルートの
  `point_extractors.py` を同じ名前で残す）。

## 3. 設計

### 3.1 共通の部分（新規 `evaluation/region_filter.py`）

- `filter_rows(df, expression=None, bounds=None, string_columns=())`: 条件式（pandas の query）と矩形の範囲で行を絞る。
  今の `filter_by_region_and_bounds` の処理そのもの（数値化、条件式に必要な列が無ければ空、範囲の外を除く）。
  名前つき領域は知らない。
- `required_columns(expression)`: 条件式が使う列。
- `save_dataframe_to_csv(df, path, message=None)`: 今と同じ。

### 3.2 名前つき領域は対象が宣言する

- `StatisticalTargetProfile` に `region_macros`（名前 → 条件式）と `string_columns`（数値化しない列）を足す。
  AWSIM は今の `REGION_MACROS` と `STRING_QUERY_COLUMNS` を宣言する。
- 名前の展開は、共通の統計に渡す前に 1 か所で行う（新規 `targets.statistical.resolve_region(target, region)`）。
  共通の統計（`evaluation/*`、`orchestration/*_mode.py`）は、展開済みの条件式と、数値化しない列だけを受け取る。
- `--dkw-region` などに名前でなく式を直接書く今の使い方は、そのまま使える。
- 対象が宣言していない名前を指定すると、実行前にエラーにする（今は条件式の評価の失敗として実行中にエラー）。

### 3.3 AWSIM の抽出の規則は AWSIM へ（新規 `targets/awsim/point_rules.py`）

- `extract_jama_edge` / `extract_ttc_edge` / `extract_worst_ttc` / `extract_verify_consistency` / `extract_boundary_gap`、
  `collect_boundary_gap_cells`、`summarize_boundary_gap_progress`、`classify_consistency` と、それらの補助関数。
- `targets/awsim/exploration.py` の `point_rules` は、このモジュールを指す。

### 3.4 ルートの `point_extractors.py` は互換の入口に

- 古い版と分析の道具のために残す。中身は 3.1 と 3.3 を呼ぶだけにし、今の名前（`REGION_MACROS`、`filter_by_region_and_bounds`、
  `EXTRACTORS` など）をすべて同じ動きで提供する。
- 新しい版のコードは、このファイルを読み込まない（アーキテクチャのテストで確かめる: `evaluation/`、`orchestration/`、`runtime/`、
  `targets/`、`apps/` から `point_extractors` を import していないこと）。

## 4. 進め方

| 段階 | 内容 | 終わりの条件 |
|---|---|---|
| P0 | 基準: ルートの `point_extractors.py` の各関数の出力を固定するテストを足す（決まったデータでの抽出結果、領域で絞った結果、boundary_gap の集計、一貫性の分類） | テストが通る |
| P1 | 3.1 の共通の部分を作り、共通の統計と `runtime/` をそちらに切り替える | 判断の記録・P0 のテストが同一 |
| P2 | 3.2 の名前つき領域の宣言と展開。AWSIM の宣言。共通の統計から名前の展開を外す | 同上。宣言にない名前が実行前にエラーになるテスト |
| P3 | 3.3 の AWSIM の抽出の規則を移す。`exploration.py` の参照を切り替える | 同上 |
| P4 | 3.4 のルートの互換の入口。新しい版がルートのファイルを読み込まないことのテスト | 古い版のテスト（`tests/unit/test_point_extractors.py`、分析の道具のテスト）と、新しい版の全テストが通る |
| P5 | 文書（`docs/extending.md` の領域の宣言、`docs/exploration_unification_plan.md` の残りの項目、この計画の進捗） | |

各段階でテストを全部流してコミットする。公開版への反映は指示を受けてから。

## 5. 範囲外

- 古い版（`strategist.py` など）の削除。最後にまとめて行う（`docs/exploration_unification_plan.md` 決定 3）。
- 抽出の規則そのものの見直し（中身は変えない）。
