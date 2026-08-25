# Implementation Rules

## 1. 目的

この文書は、`AWSIM_launch` の新設計を実装するときの開発ルールを定める。

目的は以下の 4 つ。

- 汎用性を優先する
- 変更影響を局所化する
- 入出力を明確化する
- テストで移行の正しさを確認する

## 2. 基本方針

### 2.1 汎用性優先

- 対象固有処理は `targets/` に閉じ込める
- 判定器固有処理は `verifiers/` に閉じ込める
- 評価処理は `evaluation/` に閉じ込める
- コンテナとクラスタ処理は `runtime/` に閉じ込める
- 共通アルゴリズムは特定 target 名や verifier 名を直接参照しない

### 2.2 変更影響の局所化

- 1つの機能は 1つのモジュールに主責務を持たせる
- 他モジュールはその機能の内部構造を知らない
- 内部表現ではなく、共通 contracts 経由で受け渡す
- 既存コードの都合で他層の詳細を漏らさない

### 2.3 抽象化の単位

抽象化は概念のためではなく、入出力を固定するために使う。

固定する単位:

- `TestCase`
- `RawRunResult`
- `EvaluationRecord`
- `StatisticalRequest`
- `StatisticalReport`
- `VerificationInput`
- `FT4DResult`

## 3. モジュール分割ルール

### 3.1 分ける基準

以下のどれかが違えば、別モジュールへ分ける。

- 入力が違う
- 出力が違う
- 失敗の仕方が違う
- 書き換わる理由が違う
- 再利用したい範囲が違う

### 3.2 分けすぎない基準

以下のような分割は避ける。

- 名前だけきれいだが実際は常に一緒に直すもの
- 単に概念上違うだけで入出力が同じもの
- 依存先が同じで責務もほぼ同じもの

### 3.3 共通化と検証のルール

共通化とバリデーションについては、以下をルールとする。

- まだ複数箇所で使っていない検証は共通化しない
- 2回以上同じ検証を書くなら helper 化を検討する
- 共通層を厳しくしすぎない
- 厳密さは必ず「使う直前」でかける

意図:

- 共通層は拡張性を守る
- 消費側は正しさを守る
- helper 化は実際の重複が見えてから行う
- バリデーションは使う文脈に依存するため、消費側の入口で行う

### 3.4 依存方向

- `apps/cli` は下位モジュールを呼ぶだけ
- `orchestration` は `contracts`, `runtime`, `targets`, `verifiers`, `evaluation` を使ってよい
- `evaluation` は `targets` や `runtime` を直接参照しない
- `targets` は `evaluation` を直接参照しない
- `verification_core/ft4d` は最も独立に保つ

## 4. 入出力ルール

### 4.1 各層の標準I/O

- `strategy`
  入力: 過去データ、実行モード、設定
  出力: `TestCase`

- `target backend`
  入力: `TestCase`
  出力: `RawRunResult`

- `result interpreter`
  入力: `RawRunResult`
  出力: `EvaluationRecord`

- `evaluation`
  入力: `list[EvaluationRecord]`, `StatisticalRequest`
  出力: `StatisticalReport`

- `ft4d`
  入力: `VerificationInput`
  出力: `FT4DResult`

### 4.2 禁止事項

- 辞書をその場しのぎで足し引きして境界を越えない
- module 外部へ target 固有列名を漏らさない
- file path や environment variable を統計処理層に持ち込まない
- subprocess の戻り値をそのまま上位へ返さない

## 5. 既存コードからの移行ルール

### 5.1 移行方式

- 旧コードはすぐ消さない
- 新コードは新規ファイルとして作る
- 旧コードは参照実装として残す
- 新旧の出力が揃うことを確認してから切り替える
- とくに `master_orchestrator.py` / `run_manager.py` のような運用本体は当面直接修正しない
- 運用本体の移行は `run_orchestrator_v2.py` / `run_worker_v2.py` のような新ファイルを横に作って進める

### 5.2 置換手順

1. まず新モジュールを追加する
2. 旧コードからロジックをコピーではなく切り出して移植する
3. 同じ入力で旧コードと新コードの出力を比較する
4. 問題なければ旧 CLI から新モジュールを呼ぶ
5. 十分安定したら旧実装を薄いラッパーへ縮退させる

#### 運用本体の追加順

運用本体は、次の順で横に追加する。

1. `run_worker_v2.py`
2. `apps/cli/worker_main.py`
3. `orchestration/worker_loop.py`
4. `run_orchestrator_v2.py`
5. `apps/cli/orchestrator_main.py`
6. `orchestration/orchestrator.py`

理由:

- worker 側のほうが責務を切り出しやすい
- 先に 1 ケース実行できる最小 worker を作ると smoke が書きやすい
- そのあとで司令塔をつなぐほうが比較しやすい

### 5.3 互換性維持

- 旧 CLI 名は当面残す
- 旧 dataset 形式は当面読めるようにする
- 既存の `configs/uturn.py` は移行期は残す

## 6. テストルール

### 6.0 テスト基盤

テスト実行基盤は `pytest` を前提とする。

理由:

- Python で最も標準的
- fixture を使いやすい
- parameterize が使いやすい
- regression 比較テストを書きやすい
- smoke テストをまとめやすい

### 6.1 テストの種類

最低限、以下の 4 種類で確認する。

- 単体テスト
- 契約テスト
- 回帰比較テスト
- スモークテスト

### 6.2 単体テスト

対象:

- `evaluation/*`
- `targets/*/verification_input.py`
- `verifiers/maude/evaluator.py`
- `runtime/repository/*`

確認内容:

- 入出力型が期待どおりか
- 境界値で壊れないか
- 異常系で規定どおり失敗するか

### 6.3 契約テスト

各境界で、入力と出力の形を確認する。

例:

- `TestCase -> RawRunResult`
- `RawRunResult -> EvaluationRecord`
- `EvaluationRecord list -> StatisticalReport`
- `VerificationInput -> FT4DResult`

### 6.4 回帰比較テスト

旧コードを基準にして、新コードの結果を比較する。

比較対象例:

- `awchecker.py` と `targets/awsim/result_interpreter.py`
- `estimator.py` と `evaluation/dkw.py`
- `point_extractors.py` と `orchestration/strategy.py`

#### `result_interpreter` の回帰基準

`targets/*/result_interpreter.py` の regression 比較では、完全一致を求める項目と、
緩めて比較してよい項目を分ける。

完全一致を求めるもの:

- `status`
- `target`
- `case_kind`
- `input` の主要値
- `output` のうち離散値
  例: `c_collision`, `c_ttc_1_5`
- `meta` の必須キー存在

完全一致でなくてよいもの:

- `created_at`
- `source_module`
- path
- log
- 浮動小数の細かい差
  例: `min_ttc`, `min_distance`

浮動小数は、

- 許容誤差つき比較

または

- `status` と主要判定だけ一致

でよいものとする。

### 6.5 スモークテスト

最低限動くことを短時間で確認する。

対象例:

- `apps/cli/ft4d_smoke_main.py`
- BBSL mini 実行
- AWSIM の単一点実行

### 6.6 最小 fixture セット

移行初期に最低限そろえる fixture は以下とする。

- AWSIM 正常 JSON 1件
- AWSIM timeout ケース 1件
- BBSL raw result 1件

可能なら以下も追加する。

- Maude 解析失敗ケース 1件

この追加 fixture は `analysis_error` の確認に有効。

### 6.7 例外処理の方針

例外処理は層ごとに方針を分ける。

- 境界の外側との接続部では例外を捕まえて `status` に落とす
- 純粋ロジック層では無理に握りつぶさず例外を投げる

`status` へ正規化する層:

- `targets/*/result_interpreter.py`
- `verifiers/*`
- `runtime/*`

ここでは主に以下へ正規化する。

- `analysis_error`
- `execution_error`

例外をそのまま扱ってよい層:

- `evaluation/*`
- `verification_core/*`

この方針により、接続部では運用しやすく、純粋ロジックではバグを見つけやすくする。

## 7. 実装時の確認ゲート

各新モジュールは、以下を満たすまで既存呼び出しへ差し込まない。

- 入力と出力が文書化されている
- 旧コードからの移植元が分かる
- 最低 1 件の正常系テストがある
- 最低 1 件の異常系テストがある
- 旧実装との比較結果が確認されている

### 7.1 実務ルール

移行作業では、以下を必須ルールとする。

- 新ファイルを作ったら、最低 1 つは unit テストを書く
- 旧コードを置き換える前に、最低 1 つは regression 比較を書く
- CLI へつなぐ前に、最低 1 つは smoke テストを通す

この 3 つは省略しない。

### 7.2 置き換え前チェックリスト

既存処理を新実装へ差し替える前に、以下を確認する。

- 新ファイルに対応する unit テストがある
- 旧実装との regression 比較がある
- CLI 接続前または接続時に smoke テストが通っている
- 入力と出力が contracts に沿っている
- 失敗時の挙動が決まっている

### 7.3 新旧切替の推奨順

新旧切替は、先に repository 系から置き換える。

理由:

- 保存形式と再開ロジックが基盤である
- ここが安定すると後続の比較がしやすい
- 実行部を先にいじるより回帰確認がしやすい

推奨順:

1. `contracts/*`
2. `runtime/repository/*`
3. `awchecker` 系
4. `evaluation/*`
5. `run_manager` 系
6. `orchestrator` 系
7. `bbsl` 系

## 8. 推奨するテスト配置

将来的には `tests/` を追加し、以下のように置く。

```text
tests/
├── unit/
│   ├── evaluation/
│   ├── targets/
│   ├── verifiers/
│   └── runtime/
├── contracts/
├── regression/
└── smoke/
```

## 9. 実装レビュー時のチェック項目

- このモジュールの入力は何か
- このモジュールの出力は何か
- どの旧コードから何を移したか
- target 固有事情が外へ漏れていないか
- verifier 固有事情が外へ漏れていないか
- 統計層がファイルパスや subprocess を知らないか
- 例外時の挙動が決まっているか
- 旧実装との比較手段があるか

## 10. 補足

今回「たしかそういう機能があった」として意識すべきものは、主に次の 3 つ。

- 共通 contracts による入出力固定
- スモークテストによる最低限の疎通確認
- 回帰比較による旧実装との一致確認

この文書は、実装中に判断がぶれたときのルールブックとして使う。
