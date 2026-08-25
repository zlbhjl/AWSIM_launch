# Refactor Design

## 1. 目的

このリファクタの目的は、現行の `AWSIM_launch` を

- 検査対象を差し替え可能
- 検証ツールを差し替え可能
- 統計評価を共通利用可能
- コンテナ実行基盤を共通利用可能

な統計的検証フレームへ段階移行すること。

移行方針は以下の通り。

- 既存コードはしばらく残す
- 新しい設計は新規ファイルとして追加する
- 旧コードは仕様確認と回帰比較の参照実装として使う
- いきなり全面置換せず、入出力境界から固める

## 2. 設計原則

### 2.1 分け方

概念名を増やすために分割するのではなく、以下で分割する。

- 書き換わる理由が違うもの
- 重複利用したいもの
- 入力と出力が明確に違うもの

### 2.2 最重要な境界

以下の 5 本を固定する。

1. ケース生成
2. 対象実行
3. 結果解釈
4. 統計評価
5. 全体制御

### 2.3 受け渡し型

全体の複雑さを下げるため、まず共通の入出力型を固定する。

- `TestCase`
- `RawRunResult`
- `EvaluationRecord`
- `StatisticalRequest`
- `StatisticalReport`
- `VerificationInput`
- `FT4DResult`

## 3. 新しいディレクトリ構成

```text
AWSIM_launch/
├── docs/
│   ├── architecture.md
│   ├── migration_plan.md
│   └── refactor_design.md
├── run_orchestrator_v2.py
├── run_worker_v2.py
├── apps/
│   └── cli/
│       ├── orchestrator_main.py
│       ├── worker_main.py
│       ├── bbsl_confidence_gap_main.py
│       ├── result_interpreter_main.py
│       ├── external_verifier_main.py
│       ├── ft4d_smoke_main.py
│       └── bbsl_local_ft4d_main.py
├── contracts/
│   ├── execution.py
│   ├── evaluation.py
│   ├── statistics.py
│   └── verification.py
├── orchestration/
│   ├── orchestrator.py
│   ├── worker_loop.py
│   ├── strategy.py
│   ├── final_report.py
│   ├── cache_policy.py
│   └── resume.py
├── runtime/
│   ├── cluster/
│   │   ├── cluster_config.py
│   │   ├── cluster_manager.py
│   │   ├── ray_queue.py
│   │   └── host_worker.py
│   ├── container/
│   │   ├── profile.py
│   │   ├── launcher.py
│   │   ├── supervisor.py
│   │   ├── cleanup.py
│   │   └── xvfb.py
│   └── repository/
│       ├── dataset_csv.py
│       ├── dataset_restore.py
│       ├── shared_store.py
│       ├── parameter_buffer.py
│       └── local_history.py
├── targets/
│   ├── awsim/
│   │   ├── backend.py
│   │   ├── result_interpreter.py
│   │   ├── verification_input.py
│   │   ├── kinematics_bridge.py
│   │   ├── dataset_adapter.py
│   │   └── case_kinds/
│   │       └── uturn.py
│   └── bbsl/
│       ├── backend.py
│       ├── event_builder.py
│       ├── result_interpreter.py
│       ├── runner.py
│       ├── verification_input.py
│       ├── dataset_adapter.py
│       └── profile.py
├── verifiers/
│   ├── maude/
│   │   ├── backend.py
│   │   └── evaluator.py
│   └── compatibility/
│       └── legacy_bbsl_ft4d_adapter.py
├── evaluation/
│   ├── gp_boundary.py
│   ├── dkw.py
│   ├── binomial_ci.py
│   └── ft4d_service.py
├── verification_core/
│   └── ft4d/
├── tools/
│   ├── analysis/
│   ├── visualization/
│   └── ops/
└── third_party/
    └── AW_Kinematics_Extractor/
```

## 4. ディレクトリごとの役割

- `apps/cli/`
  ユーザーが直接叩く入口。引数を受け取り、内部サービスを呼ぶだけにする。

- `contracts/`
  モジュール間で共通に使う入出力型を定義する。ここを先に固める。

- `orchestration/`
  実験全体の流れをつなぐ。探索、実行、保存、停止判断を調停する。

- `runtime/cluster/`
  Ray クラスタ、ワーカー、ホストワーカー、キューなどの分散実行基盤。

- `runtime/container/`
  コンテナやプロセス実行の共通基盤。AWSIM と BBSL のどちらにも従属しない。

- `runtime/repository/`
  CSV、共有ストア、パラメータバッファ、resume 用復元などの永続化。

- `targets/awsim/`
  AWSIM を検査対象として扱うための差分実装。
  触る場所は `backend.py`, `result_interpreter.py`, `verification_input.py`, `case_kinds/*` に固定する。

- `targets/bbsl/`
  BBSL を検査対象として扱うための差分実装。
  触る場所は `backend.py`, `result_interpreter.py`, `verification_input.py` に固定する。

- `verifiers/`
  Maude などの判定器、または外部の検証ツール。
  verifier 追加時は基本的に `backend.py` と `evaluator.py` だけを増やす。
  旧 BBSL FT4D 入口のような互換用途は `compatibility/` に隔離する。

- `evaluation/`
  DKW、二項 CI、GP 境界推定、FT4D 呼び出しなどの共通評価処理。

- `verification_core/ft4d/`
  FT4D の共通コア。対象や verifier に属さない。

- `tools/`
  可視化、解析、運用補助スクリプト。
  ただし今後は役割を `summary` と `plot` に寄せ、
  `viewer` は「人が最初に叩く確認入口」という責務名として使う。
  役割が薄い限り、`tools/viewer/*` のような専用ディレクトリは無理に増やさない。

- `third_party/AW_Kinematics_Extractor/`
  既存外部コンポーネント。すぐに分解せず bridge 経由で使う。

### 4.1 拡張時の固定タッチポイント

新しい機能を足すときに、触る場所を先に固定する。

- 新しい target を足す
  `targets/<name>/backend.py`
  `targets/<name>/result_interpreter.py`
  `targets/<name>/verification_input.py`
  `targets/<name>/dataset_adapter.py`
- AWSIM の新しい case_kind を足す
  `targets/awsim/case_kinds/<case_kind>.py`
- 新しい verifier を足す
  `verifiers/<name>/backend.py`
  `verifiers/<name>/evaluator.py`
- 新しい評価法を足す
  `evaluation/<method>.py`

この形にしておくと、「target を変える」「verifier を変える」「評価法を増やす」で触る場所が混ざらない。

## 5. 共通データ契約

### 5.0 基本方針: 最小共通核 + 拡張可能な箱

`contracts/*` は「最初から全 target / verifier / 統計用途を完全に表現する」ことを目指さない。
代わりに、

- どのケースでも必ず必要な最小共通核だけを固定する
- 将来増える target 固有値や verifier 固有値は拡張用の箱へ入れる

という方針を採る。

この方針の理由は以下。

- 将来どの値が必要になるかを今の時点で完全には決められない
- 早い段階で厳密に固定しすぎると、target 追加のたびに contracts を壊しやすい
- 一方で、何も固定しないとモジュール境界が崩れて辞書の投げ合いになる

したがって、contracts では以下だけを強く固定する。

- レコード識別子
- target 名
- case_kind 名
- status
- 共通カテゴリの名前

逆に、各カテゴリ内の詳細キーは拡張可能にする。

共通カテゴリは次の 4 つとする。

- `input`
- `output`
- `evidence`
- `meta`

それぞれの意味は以下。

- `input`
  実行前に与えるデータ。パラメータ、条件、設定など。
- `output`
  実行後に得るデータ。観測値、判定値、ラベル、スコアなど。
- `evidence`
  生データや成果物への参照。JSON、raw result、log、video など。
- `meta`
  実行管理や追跡のための補助情報。status 以外の管理情報を置く。

### 5.0.1 contracts の型方針

`contracts/*` の型は、型安全と拡張性のバランスを取る。

- `status` は内部では `Enum` として扱う
- 保存時や JSON 化時は文字列へ変換する

これにより、

- typo を防ぎやすい
- 既存の CSV / JSON / ログ出力とも接続しやすい

また、各カテゴリの value 型は次を基本とする。

- `input`: `dict[str, object]`
- `output`: `dict[str, object]`
- `evidence`: `dict[str, str]`
- `meta`: `dict[str, object]`

方針として、

- `evidence` は path/ID 中心なので比較的単純にする
- それ以外は将来拡張しやすいように広めに取る

#### `RunStatus` の定義場所

`RunStatus` は当面 `contracts/execution.py` に置く。

理由:

- 現時点では実行結果と最も強く結びついている
- まだ `contracts/common.py` を切るほどではない
- 本当に共通化が必要になった時点で後から切り出せばよい

#### Enum の保存方法

内部では `Enum` を使い、CSV / JSON / ログへ外部化するときは必ず `.value` を使う。

ルール:

- 内部表現: `RunStatus.SUCCESS`
- 保存表現: `"success"`

このルールは実装全体で統一し、保存時に `Enum` オブジェクトを直接流さない。

### 5.0.2 status の標準値

`status` の標準語彙は最初は以下で固定する。

- `success`
- `timeout`
- `execution_error`
- `analysis_error`
- `invalid`

必要になった場合のみ、後から `skipped` などを追加する。
最初から語彙を増やしすぎない。

#### `invalid` と `analysis_error` の境界

`status` のうち、`invalid` と `analysis_error` は次の基準で分ける。

- 入力は読めたが、意味のある評価対象になっていないなら `invalid`
- 解析処理そのものが失敗したなら `analysis_error`

運用表:

- 空データ
  `invalid`
- 必須キー欠落
  `analysis_error`
- Maude 実行失敗
  `analysis_error`
- JSON は読めるが対象レコードが 0 件
  `invalid`
- JSON 破損
  `analysis_error`

この表を `targets/*/result_interpreter.py` の判定基準として使う。

### 5.0.3 schema_version の運用

`schema_version` は `EvaluationRecord.meta["schema_version"]` に必須で持たせる。

初期値は以下とする。

```python
meta = {
    "schema_version": 1,
}
```

理由:

- 後でフォーマット変更したときに旧データと新データを区別できる
- regression 比較と過去 dataset 読み込みが安全になる

#### `EvaluationRecord` の必須 meta

`EvaluationRecord.meta` では、少なくとも以下の 3 つを必須とする。

- `schema_version`
- `created_at`
- `source_module`

それぞれの意味:

- `schema_version`
  形式の互換管理
- `created_at`
  生成時刻の追跡
- `source_module`
  新旧比較、障害解析、デバッグの追跡

`worker_id` や `reason` は有用だが、最初は必須にしない。

#### `EvaluationRecord.meta` の最低ライン

`EvaluationRecord.meta` では、必須キーと条件付き必須キーを分ける。

常に必須:

- `schema_version`
- `created_at`
- `source_module`

判定系で条件付き必須:

- 判定器が 1 つだけなら `verifier_name`
- 判定器が複数なら `verifiers`

任意だが推奨:

- `verifier_version`
- `analysis_pipeline`

このルールにより、

- 最低限の追跡情報は全件で揃う
- 単独判定と複数判定の両方を無理なく扱える
- 判定器情報を共通核へ混ぜずに記録できる

#### `EvaluationRecord.meta` に入れる判定ツール情報

判定ツールや解析パイプラインの情報は、共通核には入れず `meta` に入れる。

理由:

- `target` は「何を検査したか」を表す
- 判定ツールは「どう解釈したか」を表す
- この 2 つは意味が違うため、同じ固定核へ混ぜない

推奨キー:

- `verifier_name`
- `verifier_version`
- `verifiers`
- `analysis_pipeline`

使い分けの目安:

- 判定器が 1 つだけなら `verifier_name`
- 複数使ったなら `verifiers`
- 実行経路まで残したいなら `analysis_pipeline`

書き方のルール:

- 単独判定なら `verifier_name` だけを書く
- 複数判定なら `verifiers` だけを書く
- `verifier_name` と `verifiers` は同時には書かない

### 5.0.4 evidence のキー規約

`RawRunResult.evidence` のキーは最初は少数精鋭で固定する。

- `trace_json`
- `raw_result_json`
- `video`
- `log`

想定:

- AWSIM は主に `trace_json` を使う
- BBSL は主に `raw_result_json` を使う
- 共通ログは `log`
- 動画がある場合のみ `video`

必要なときだけ後からキーを追加する。

#### path の持ち方

`evidence` に入れる path は、内部では絶対パスを基本とする。

理由:

- 分散実行や container 実行で基準ディレクトリがずれやすい
- 相対パスだと再現や障害解析で混乱しやすい
- 絶対パスなら、生成物を直接追いやすい

表示時や保存時に必要なら相対化してよいが、内部表現は絶対パスを優先する。

#### 外部保存時の相対化ルール

`evidence` の path は、次の境界で扱いを分ける。

- メモリ上・実行中の `EvaluationRecord.evidence`
  絶対パス
- CSV や共有成果物へ保存するとき
  `path_root` 基準で相対化してよい

このとき、

- `meta["path_root"]` は必ず残す
- `working_directory` は補助情報として任意で残す

`working_directory` ではなく `path_root` を基準にする理由:

- `working_directory` は実行時都合で変わりやすい
- `path_root` は成果物群の基準ディレクトリとして意味が明確

#### コマンド記録の方針

この領域はバグが起こりやすいため、生成物の path だけでなく、実行コマンドも残す。

方針:

- `evidence`
  何が生成されたかを持つ
- `meta`
  どう生成したかを持つ

特に、旧実装と新実装の比較や障害解析のため、以下の両方を記録する。

- `legacy_command`
- `normalized_command`

加えて、少なくとも以下を `meta` に持たせることを推奨する。

- `working_directory`
- `host_or_container`
- `container_name`
- `schema_version`
- `created_at`
- `source_module`

例:

```python
evidence = {
    "trace_json": "/home/passd/simulation_traces/uturn_eval_sim42.json",
    "log": "/home/passd/simulation_traces/awsim.log",
}

meta = {
    "schema_version": 1,
    "created_at": "2026-07-31T15:00:00+09:00",
    "source_module": "targets.awsim.backend",
    "path_root": "/home/passd/AWSIM_launch",
    "working_directory": "/home/passd/AWSIM_launch",
    "host_or_container": "container",
    "container_name": "sim_worker_22",
    "legacy_command": "python3 run_scenario.py --type uturn --dx0 15.0 --ego_speed 35.0 --npc_speed 12.0",
    "normalized_command": "target_runner_main.py --target awsim --case-kind uturn --case-id loop_42",
}
```

この記録により、

- 旧実装と新実装の差分比較
- バグ時の再現
- 回帰テスト用のコマンド再利用
- 分散実行時の追跡

をしやすくする。

### 5.0.5 BBSL の最終位置づけ

`targets/bbsl` は、まず `EvaluationRecord` 主体で扱う。

流れの基本は以下。

```text
BBSL raw output -> EvaluationRecord -> 必要なら VerificationInput
```

この方針を採る理由:

- AWSIM と同列の target として扱いやすい
- `evaluation/*` や `tools/*` の共通利用がしやすい
- FT4D 専用入力源に閉じすぎず、将来の分析用途へ広げやすい

必要に応じて `targets/bbsl/verification_input.py` が `EvaluationRecord` から `VerificationInput` を組み立てる。

#### legacy 互換入口の終了条件

`run_external_verifier.py` と `verifiers/compatibility/legacy_bbsl_ft4d_adapter.py` は、
BBSL 系の新設計への完全移植が完了するまで残す。

完全移植完了の判定条件:

- `targets/bbsl/backend.py`
- `targets/bbsl/result_interpreter.py`
- `targets/bbsl/verification_input.py`
- `evaluation/ft4d_service.py`

の新経路で、旧 `bbsl_ft4d` 経路の役割をすべて代替できる。

加えて:

- 旧経路と新経路の regression 比較が最低 1 本ある
- README と運用手順から旧 CLI 依存が消えている

削除条件は、

- 新経路で一部代替できた時

ではなく、

- BBSL の新経路への完全移植完了時

とする。

#### legacy 互換入口の廃止方針

`run_external_verifier.py` と `verifiers/compatibility/legacy_bbsl_ft4d_adapter.py` は、
BBSL 系の新経路への完全移植が完了するまで残す。

完全移植完了後、README・運用手順・regression 比較を新経路ベースへ揃えたうえで、
legacy 互換入口は最後にまとめて削除する。

### 5.0.6 configs/uturn.py の移行方針

移行初期は、当面 `configs/uturn.py` をそのまま読む。

段階移行の方針は以下。

1. 新実装はしばらく既存 `configs/uturn.py` を読む
2. 新構造が安定したら `targets/awsim/case_kinds/uturn.py` へ集約する
3. 最後に旧 config を薄い互換レイヤへ縮退させる

#### `configs/uturn.py` の移行単位

`configs/uturn.py` は一気に移さず、意味のかたまりごとに順番に移す。

推奨順:

1. 探索範囲・基本パラメータ
2. rule spec
3. focus point
4. event 定義

理由:

- 前半は `TestCase` や探索に効く
- 後半は判定や FT4D 入力に効く
- 壊れ方が違うため、分けて移したほうが回帰確認しやすい

したがって、`uturn` 設定は用途別に 4 段で移行する。

この方針を採る理由:

- 初期差分を小さくできる
- 設定意味の取り違いを減らせる
- 既存ワークフローとの互換を保ちやすい

### 5.1 TestCase

- 入力元
  `orchestration/strategy.py`
- 渡し先
  `targets/*/backend.py`

想定フィールド:

- `case_id`
- `target`
- `case_kind`
- `input`
- `tags`
- `reason`

#### `case_kind` の語彙

`case_kind` は小文字スネークケースで統一する。

例:

- `uturn`
- `merge`
- `intersection_left_turn`
- `batch_loop`
- `single_replay`

ルール:

- target 固有名ではなく、検証ケースの種類名にする
- CLI、CSV、可視化で同じ文字列を使う
- 略語はなるべく避ける

最小具体案:

```python
class RunStatus(str, Enum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    EXECUTION_ERROR = "execution_error"
    ANALYSIS_ERROR = "analysis_error"
    INVALID = "invalid"


@dataclass
class TestCase:
    case_id: str
    target: str
    case_kind: str
    input: dict[str, object] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    reason: str = ""
    meta: dict[str, object] = field(default_factory=dict)
```

### 5.2 RawRunResult

- 入力元
  `targets/*/backend.py`
- 渡し先
  `targets/*/result_interpreter.py`

想定フィールド:

- `case_id`
- `target`
- `case_kind`
- `status`
- `evidence`
- `meta`

最小具体案:

```python
@dataclass
class RawRunResult:
    case_id: str
    target: str
    case_kind: str
    status: RunStatus
    evidence: dict[str, str] = field(default_factory=dict)
    meta: dict[str, object] = field(default_factory=dict)
```

### 5.3 EvaluationRecord

- 入力元
  `targets/*/result_interpreter.py`
- 渡し先
  `evaluation/*`, `runtime/repository/*`, `verification_core/ft4d` 用 adapter

想定フィールド:

- `case_id`
- `target`
- `case_kind`
- `status`
- `input`
- `output`
- `evidence`
- `meta`

`EvaluationRecord` は最も重要な共通契約だが、詳細キーを固定しすぎない。
ここでは「用途別のカテゴリ」を固定し、その中身は拡張可能とする。

最小具体案:

```python
@dataclass
class EvaluationRecord:
    case_id: str
    target: str
    case_kind: str
    status: RunStatus

    input: dict[str, object] = field(default_factory=dict)
    output: dict[str, object] = field(default_factory=dict)
    evidence: dict[str, str] = field(default_factory=dict)
    meta: dict[str, object] = field(default_factory=dict)
```

#### `EvaluationRecord` の運用ルール

- `input`
  実行前に与える値を入れる
- `output`
  測定値、判定値、ラベル、スコアなど実行後に得た値を入れる
- `evidence`
  JSON, CSV, 画像, raw result などの path/ID を入れる
- `meta`
  `loop_num`, `worker_id`, `reason`, `schema_version`, `verifier_name` などの補助情報を入れる

#### `EvaluationRecord` の具体例

```python
EvaluationRecord(
    case_id="loop_42",
    target="awsim",
    case_kind="uturn",
    status="success",
    input={
        "dx0": 15.0,
        "ego_speed": 35.0,
        "npc_speed": 12.0,
    },
    output={
        "min_ttc": 1.23,
        "min_distance": 3.8,
        "min_ttb": 0.7,
        "z_margin": 0.18,
        "c_collision": 0,
        "c_ttc_1.5": 1,
        "c_ttc_1.3": 0,
    },
    evidence={
        "trace_json": "/path/to/uturn_eval_sim42.json",
    },
    meta={
        "loop_num": 42,
        "worker_id": "21",
        "reason": "boundary_explore",
        "schema_version": 1,
        "verifier_name": "maude",
        "verifiers": ["maude", "aw_kinematics"],
    },
)
```

#### 拡張のルール

- 新しい target 固有値は、まず `meta` の target 固有キーとして入れてよい
- 判定ツール情報は、まず `meta` に入れる
- よく使う値になったら `input` または `output` へ昇格を検討する
- 共通化できないものは無理に昇格しない
- `evaluation/*` は、必要なキーが存在するときだけ使う書き方にする
- `schema_version` を `meta` に入れておく

この方針により、共通契約を壊さずに新しい target や verifier を追加しやすくする

### 5.4 StatisticalRequest / StatisticalReport

- 入力元
  `orchestration/strategy.py`
- 渡し先
  `evaluation/*`

出力元
  `evaluation/*`
- 渡し先
  `orchestration/strategy.py`, `orchestration/orchestrator.py`

最小具体案:

```python
@dataclass
class StatisticalRequest:
    method: str
    metric: str
    bounds: dict[str, tuple[float, float]] | None = None
    confidence: float = 0.95
    target_width: float | None = None
    options: dict[str, object] = field(default_factory=dict)


@dataclass
class StatisticalReport:
    method: str
    metric: str
    sample_count: int
    estimate: float | None
    interval: tuple[float, float] | None
    sufficient: bool
    next_action: str
    diagnostics: dict[str, object] = field(default_factory=dict)
```

### 5.5 VerificationInput / FT4DResult

- 入力元
  `targets/*/verification_input.py`
- 渡し先
  `verification_core/ft4d/*`

出力元
  `verification_core/ft4d/*`
- 渡し先
  `orchestration/strategy.py`, `tools/analysis/*`

最小具体案:

```python
@dataclass
class VerificationInput:
    tree_mode: str
    universal_dataset: set[str] | set[int]
    events: dict[str, dict[str, object]]
    assumptions: dict[str, object] = field(default_factory=dict)
    meta: dict[str, object] = field(default_factory=dict)


@dataclass
class FT4DResult:
    tree_mode: str
    top_sigma_pe: float | None
    confidence: float | None
    node_summaries: list[dict[str, object]]
    raw_result: dict[str, object]
```

`VerificationInput` は共通の検証入力インターフェイス名として使う。
FT4D はその具体的な受け手であり、`verification_core/ft4d/*` 側がこの入力を FT4D 計算へ使う。
将来ほかの検証手法を追加するときも、まずは `VerificationInput` を受ける形を基本にする。

#### `VerificationInput` の将来範囲

`VerificationInput` は、今は FT4D で必要な最小限にとどめる。

理由:

- 将来の他手法まで先回りして広げすぎると、共通層が曖昧になりやすい
- 実際に別手法が入るまでは、FT4D 用の最小核として固定したほうが安全

将来ほかの手法が必要になったら、その時点で以下を判断する。

- そのまま `VerificationInput` を使う
- `VerificationInput` を拡張して使う
- 別の入力契約を切る

したがって、現時点では `VerificationInput` は FT4D 最小核として設計する。

## 6. 各ファイルの入力 / 出力 / 移植元

### 6.1 docs

#### `docs/architecture.md`
- 入力
  なし
- 出力
  新設計の責務説明
- 移植元
  この設計方針を要約した文書

#### `docs/migration_plan.md`
- 入力
  なし
- 出力
  移行手順、完了条件、検証手順
- 移植元
  既存コードの分解計画

#### `docs/refactor_design.md`
- 入力
  なし
- 出力
  tree、ファイル責務、優先順の統合設計書
- 移植元
  今回の設計内容

### 6.2 apps/cli

#### `apps/cli/orchestrator_main.py`
- 入力
  CLI引数
- 出力
  終了コード
- 補足
  `worker_count` と `cache_size` の解決を受け持ち、
  旧 `strategist.py` 相当の cache 補充ポリシーを
  `orchestration/cache_policy.py` へ委譲する。
- 移植元
  `master_orchestrator.py:main`
  `config_loader.py:load_config`

#### `run_orchestrator_v2.py`
- 入力
  CLI引数
- 出力
  終了コード
- 役割
  新運用本体の root 入口。
  旧 `master_orchestrator.py` を直接書き換えず、内部で `apps/cli/orchestrator_main.py` を呼ぶ薄い wrapper とする。
  `--headless` を受けた場合は、そのまま `apps/cli/worker_main.py` へ forward し、
  最終的に `targets/awsim/backend.py` の `runtime_profile.headless=True` へ届くようにする。
- 移植元
  `master_orchestrator.py:main`

#### `apps/cli/worker_main.py`
- 入力
  CLI引数、環境変数
- 出力
  終了コード
- 移植元
  `run_manager.py:load_config`
  `run_manager.py` のグローバル初期化部

#### `run_worker_v2.py`
- 入力
  CLI引数、環境変数
- 出力
  終了コード
- 役割
  新 worker 本体の root 入口。
  旧 `run_manager.py` を直接書き換えず、内部で `apps/cli/worker_main.py` を呼ぶ薄い wrapper とする。
  `--headless` が指定された場合は、
  `apps/cli/worker_main.py -> targets/awsim/backend.py -> runtime/container/xvfb.py`
  の順で渡し、Xvfb を使った headless 実行を有効化する。
- 移植元
  `run_manager.py:main`

実行例:

```bash
python3 run_worker_v2.py \
  --param dx0=15.0 \
  --param ego_speed=35.0 \
  --param npc_speed=14.0 \
  --output /tmp/worker_records.jsonl \
  --simulation-output-dir /tmp/awsim_traces \
  --headless
```

#### `apps/cli/result_interpreter_main.py`
- 入力
  CLI引数、環境変数
- 出力
  終了コード
- 役割
  `trace JSON` または `raw result JSON` を単独で読み、
  `EvaluationRecord` へ正規化した結果を確認する診断用 CLI。
  これは実行器ではなく、結果解釈器を単独で呼ぶための入口。
- 移植元
  `awchecker.py:main`
- 状態
  実装済み。
  `--input` で `trace JSON` または `raw result JSON` を受け、
  target 別 `ResultInterpreter` を直接呼ぶ診断用 CLI として使う。

#### `apps/cli/external_verifier_main.py`
- 入力
  CLI引数
- 出力
  終了コード、正規化済み JSON
- 現在の新経路
  `run_external_verifier.py`
  -> `apps/cli/external_verifier_main.py`
  -> `external_verifiers.create_verifier()`
  -> `verifiers/compatibility/legacy_bbsl_ft4d_adapter.py`
  -> `targets/bbsl/backend.py`
  -> `targets/bbsl/result_interpreter.py`
  -> `targets/bbsl/verification_input.py`
  -> `evaluation/ft4d_service.py`
- 移植元
  `run_external_verifier.py:parse_args`
  `run_external_verifier.py:main`

#### `apps/cli/ft4d_smoke_main.py`
- 入力
  CLI引数
- 出力
  終了コード
- 現在の新経路
  `run_ft4d_smoke.py`
  -> `apps/cli/ft4d_smoke_main.py`
  -> `targets/awsim/result_interpreter.py`
  -> `targets/awsim/verification_input.py`
  -> `evaluation/ft4d_service.py`
- 移植元
  `run_ft4d_smoke.py:parse_args`
  `run_ft4d_smoke.py:main`

#### `apps/cli/bbsl_local_ft4d_main.py`
- 入力
  CLI引数
- 出力
  終了コード
- 現在の新経路
  `run_bbsl_local_ft4d.py`
  -> `apps/cli/bbsl_local_ft4d_main.py`
  -> `targets/bbsl/profile.py`
  -> 既存 BBSL 実行または既存 raw result 再利用
  -> `targets/bbsl/ft4d_bridge.py`
  -> `targets/bbsl/result_interpreter.py`
  -> `targets/bbsl/verification_input.py`
  -> `evaluation/ft4d_service.py`
- 補足
  `batch-loop` 系は `targets/bbsl/batch_loop.py` を直接通る。
  `condition_policy=underconfident` の適応ループも
  `targets/bbsl/underconfident_loop.py` へ分離済みで、
  旧 `strategist.py` は target 固有の実行詳細を持たず委譲だけ行う。
  最終的な FT4D 再計算は AWSIM_launch 側の
  `targets/bbsl/* + evaluation/ft4d_service.py` へ統一する。
- 移植元
  `run_bbsl_local_ft4d.py:parse_args`
  `run_bbsl_local_ft4d.py:main`

#### `apps/cli/bbsl_confidence_gap_main.py`
- 入力
  CLI引数
- 出力
  終了コード、summary JSON
- 現在の新経路
  `apps/cli/bbsl_confidence_gap_main.py`
  -> `targets/bbsl/ft4d_bridge.py`
  -> `targets/bbsl/batch_loop.py` または `targets/bbsl/underconfident_loop.py`
  -> `targets/bbsl/ft4d_analysis.py`
- 役割
  BBSL confidence gap 専用の v2 入口。
  dataset / output を読み、FT4D confidence gap を集計し、
  `confidence_summary` と `completion_summary` を JSON と標準出力へ出す。
- 移植元
  `strategist.py:inspect_bbsl_ft4d_confidence_gaps`

#### CLI棚卸し結果
- `apps/cli/external_verifier_main.py`
  実装済み。
  `run_external_verifier.py` はこの CLI を呼ぶ薄い wrapper として残す。
- `apps/cli/ft4d_smoke_main.py`
  実装済み。
  `run_ft4d_smoke.py` はこの CLI を呼ぶ薄い wrapper として残す。
- `apps/cli/bbsl_local_ft4d_main.py`
  実装済み。
  `run_bbsl_local_ft4d.py` はこの CLI を呼ぶ薄い wrapper 兼、
  既存 import 互換入口として残す。
- `apps/cli/result_interpreter_main.py`
  現時点では優先度低。
  理由は、現行の運用本体や README の主要経路がまだ直接この CLI を要求しておらず、
  `targets/awsim/result_interpreter.py` を直接呼ぶテスト経路で当面代替できるため。
  これは「不要」ではなく、trace JSON -> `EvaluationRecord` を単独で確認する
  ユーティリティ CLI が必要になった時点で追加する backlog とする。

#### 実装状況
1. `apps/cli/external_verifier_main.py`: 実装済み
2. `apps/cli/ft4d_smoke_main.py`: 実装済み
3. `apps/cli/bbsl_local_ft4d_main.py`: 実装済み
4. `apps/cli/result_interpreter_main.py`: backlog

### 6.3 contracts

#### `contracts/execution.py`
- 入力
  なし
- 出力
  `TestCase`, `RawRunResult`, `RunStatus`
- 移植元
  `run_manager.py` の `next_target`
  `run_manager.py` の timeout/success 概念

#### `contracts/evaluation.py`
- 入力
  なし
- 出力
  `EvaluationRecord`
- 移植元
  `awchecker.py` の `parsed_row`
  `awchecker.py` の出力値辞書構築部

#### `contracts/statistics.py`
- 入力
  なし
- 出力
  `StatisticalRequest`, `StatisticalReport`
- 移植元
  `estimator.py`
  `strategist.py`

#### `contracts/verification.py`
- 入力
  なし
- 出力
`VerificationInput`, `FT4DResult`
- 移植元
  `estimator.py` の FT4D 用 dict 群

### 6.4 orchestration

#### `orchestration/orchestrator.py`
- 入力
  config、queue、strategy、repository
- 出力
  task投入、stop 判断、進行制御
- 移植元
  `master_orchestrator.py:main`

#### `orchestration/worker_loop.py`
- 入力
  worker config、backend、repositories
- 出力
  1ケースずつの実行、完了報告
- 移植元
  `run_manager.py:ProcessManager.execute`

#### `orchestration/strategy.py`
- 入力
  過去 dataset、mode、scenario config
- 出力
  `TestCase`
- 移植元
  `strategist.py:ActiveLearningStrategist`
  `strategist.py:decide_next_target`
  `strategist.py:generate_candidate_points`
  `strategist.py:get_random_point`
  `strategist.py:get_best_target`

#### `orchestration/final_report.py`
- 入力
  `num_samples`, `target`, `reason`
- 出力
  final report payload、整形済み文字列
- 役割
  旧 `strategist.py:_print_final_report` 相当の
  完了要約を v2 側で共通化する。
  `strategy` は何を報告するかだけ決め、
  表示整形はこの helper に逃がす。
- 移植元
  `strategist.py:_print_final_report`

#### `orchestration/cache_policy.py`
- 入力
  `worker_count`, `run_mode`, `resume_from`, `max_samples`, `explicit_cache_size`
- 出力
  `cache_size`
- 役割
  旧 `strategist.py` の
  `worker_count -> CACHE_SIZE` 自動決定を v2 側へ戻す。
  現時点では旧基本則の `worker_count * 2` を正本として扱う。
- 移植元
  `strategist.py:__init__` の `CACHE_SIZE` 初期化

#### `boundary_gap` の移行メモ

`boundary_gap` は、`orchestration/strategy.py` に残す代表的な
探索文脈依存ロジックとして扱う。

理由:

- 候補セル抽出自体は `point_extractors.py` に寄せられる
- ただし「何回回すか」「次 cycle に進むか」「stop するか」は
  strategy 全体の制御文脈に強く依存する

`2026-08-14` 時点での実装到達点:

- `run_mode == "boundary_gap"` の分岐を `orchestration/strategy.py` に実装済み
- 初期ターゲットは config 既定 `FOCUS_POINTS` ではなく
  `extract_boundary_gap(...)` の候補を使う
- exact repeat 消化後に `extract_boundary_gap(...)` を再評価し、
  次 cycle のターゲットへ更新できる
- 候補が消えたら `system_command=stop` を返す
- 進捗 CSV は
  `runtime/repository/boundary_gap_progress.py`
  経由で保存する
- 実 dataset を使った 1 ケース実行で、
  `records.jsonl` / dataset CSV / progress CSV の更新を確認済み
- AWSIM を回さないオフライン回帰テストで、
  dataset 増加後に `candidate_cells -> 0`、
  `densified_cells -> 1` へ変化することを確認済み

この時点での残差:

- 旧 `strategist.py` とログ文言・補助 print の完全一致確認
- 複数 cycle をまたぐ実運用 dataset での
  `candidate_cells` / `status` 推移の最終確認
- README / 運用手順への反映
- `boundary_gap` の主制御は strategy 文脈依存が強いため、
  追加 helper 分割は当面行わない
  （分けるとしても集計 helper までに留める）

完了扱いにしてよい条件:

1. `boundary_gap` の初期ターゲットが
   `extract_boundary_gap(...)` の候補と一致している
2. progress CSV の `point_count` と `candidate_cells` が
   初期 cycle で整合している
3. オフライン回帰で
   `initial -> cycle_complete` の差分が検出できる
4. 少なくとも 1 ケースの v2 実 run で
   dataset CSV へ新規行が追記される

この 4 条件を満たした時点で、
`boundary_gap` は「v2 で運用し始められる準完了モード」とみなす。
その後は、残差の中心を
`verify_consistency` / edge 系 mode の移行へ移す。

#### `verify_consistency` の移行メモ

`verify_consistency` も、`boundary_gap` と同様に
`orchestration/strategy.py` に残す探索文脈依存ロジックとして扱う。

理由:

- 初期ターゲット抽出自体は `point_extractors.py` に寄せられる
- ただし「exact repeat を何回回すか」「reason 付き行だけをどう拾うか」
  「分類後に stop するか」は strategy 全体の制御文脈に強く依存する

`2026-08-14` 時点での v2 実装到達点:

- `run_mode == "verify_consistency"` の分岐を
  `orchestration/strategy.py` に実装済み
- 初期ターゲットは config 既定 `FOCUS_POINTS` ではなく
  `extract_verify_consistency(...)` の候補を使う
- exact repeat の reason は
  `"[CONSISTENCY] Exact Point ..."` に統一済み
- 反復完了後は `reason` に `"[CONSISTENCY]"` を含む行だけを抽出する
  helper を使う
- consistent / stochastic の分類は
  `point_extractors.classify_consistency(...)` に委譲済み
- DKW 評価は `evaluation/dkw.py` の `DKWService` に委譲済み
- 分類 CSV 保存は
  `runtime/repository/consistency_classification.py`
  経由で保存する
- consistent / stochastic ごとの DKW summary CSV 保存は
  `runtime/repository/consistency_dkw_summary.py`
  経由で保存する
- summary CSV から最新結果を読み、人が確認しやすい表示を行う CLI として
  `apps/cli/consistency_summary_main.py`
  を追加済み
- AWSIM を回さないオフライン回帰テストで、
  exact repeat reason、分類 CSV 保存、DKW summary 保存、
  summary CLI 表示まで確認済み

この時点での残差:

- 旧 `strategist.py` の print 文言との完全一致確認
- 実 dataset を使った `verify_consistency` 実 run で、
  `*_consistent_risk.csv` / `*_stochastic_risk.csv` /
  `*_consistency_dkw_summary.csv` の更新確認
- README / 運用手順への反映
- `verify_consistency` と edge 系 mode の抽出ロジックを
  将来的に `tools/analysis/*` へどこまで分けるかの整理

完了扱いにしてよい条件:

1. 初期ターゲットが `extract_verify_consistency(...)` の候補と一致している
2. exact repeat の全行に `"[CONSISTENCY]"` reason が付与される
3. 反復完了後に `reason` フィルタが正しく働き、
   non-consistency 行を分類対象から除外できる
4. consistent / stochastic の分類 CSV が
   repository 経由で保存される
5. DKW summary CSV が
   repository 経由で保存される
6. summary CLI で最新の consistent / stochastic 結果を表示できる

この 6 条件を満たした時点で、
`verify_consistency` は「v2 で運用し始められる準完了モード」とみなす。
その後は、残差の中心を edge 系 mode と実 run 確認へ移す。

#### `jama_edge` / `ttc_edge` / `worst_ttc` の移行メモ

edge 系 3 モードは、
`orchestration/strategy.py` の共通 extractor-focus 導線にまとめて載せる。

理由:

- 初期ターゲット抽出自体は `point_extractors.py` に寄せられる
- ただし「抽出結果が空なら stop する」「抽出結果があれば exact repeat に入る」
  という制御は strategy 全体の文脈に依存する
- `jama_edge` / `ttc_edge` / `worst_ttc` の差分は
  抽出器の中身に閉じ、strategy 側は共通化できる

`2026-08-14` 時点での v2 実装到達点:

- `jama_edge` / `ttc_edge` / `worst_ttc` は
  `orchestration/strategy.py` の共通 extractor-focus mode として実装済み
- 初期ターゲットは config 既定 `FOCUS_POINTS` ではなく
  各 mode に対応する `point_extractors.EXTRACTORS[...]` の候補を使う
- 抽出結果があれば、旧 focus 系と同様に
  `"[FOCUS] Exact Point ..."` の exact repeat に入る
- 抽出結果が空なら
  `No target points found for mode '...'`
  で即時 stop する
- `apps/cli/orchestrator_main.py` で
  `--mode jama_edge`
  `--mode ttc_edge`
  `--mode worst_ttc`
  を v2 strategy mode として許可済み
- AWSIM を回さないオフライン回帰テストで、
  3 モードそれぞれについて
  「抽出成功 -> exact repeat」
  「抽出空 -> stop」
  「CLI 入口で許可」
  を確認済み
- 実 dataset snapshot
  `/home/passd/simulation_traces_shared_20260724_172135`
  を使ったオフライン一致確認で、
  `ttc_edge` / `worst_ttc` は
  extractor の先頭候補と v2 strategist の 1 件目が一致し、
  `jama_edge` は候補 0 件に対して v2 も即時 stop することを確認済み
- edge 系抽出モードでは config 既定 `FOCUS_POINTS` を使わず、
  extractor 候補が空なら空のまま stop するよう修正済み

この時点での残差:

- 旧 `strategist.py` の抽出ログ文言との完全一致確認
- README / 運用手順への反映
- 必要なら抽出対象 CSV の保存や analysis helper を
  mode ごとに repository 化するかの整理

完了扱いにしてよい条件:

1. `jama_edge` / `ttc_edge` / `worst_ttc` の初期ターゲットが
   それぞれ対応 extractor の候補と一致している
2. 抽出成功時に exact repeat へ進む
3. 抽出空時に探索へ落ちず即時 stop する
4. v2 orchestrator CLI から 3 モードすべて指定できる
5. 同じ実 dataset snapshot に対して、
   extractor の先頭候補と v2 strategist の 1 件目が一致する

この 5 条件は `2026-08-14` 時点で満たしているため、
edge 系 3 モードは「v2 で運用し始められる準完了モード」とみなす。
その後は、残差の中心をログ文言の一致確認と
必要な analysis / repository 化の見極めへ移す。

#### `strategist.py` -> `orchestration/strategy.py` 最新機能対応表

| 旧 `strategist.py` の機能 | v2 側の状態 | 現在の受け先 | まだ v2 にない残差 |
| --- | --- | --- | --- |
| `_load_case_definition_from_config` | 移行済み | `orchestration/strategy.py` | なし |
| `_load_strategy_settings_from_config` | 移行済み | `orchestration/strategy.py` | なし |
| `generate_candidate_points` | 移行済み | `orchestration/strategy.py` | 旧ログ文言の完全一致は未実施 |
| `get_random_point` | 移行済み | `orchestration/strategy.py` | 旧の説明コメント・print は未反映 |
| `get_best_target` | 移行済み | `orchestration/strategy.py` | なし |
| explore / margin の STEP1/STEP2/STEP3 制御 | 移行済み | `orchestration/strategy.py` | 旧の詳細 print は未反映 |
| focus exact repeat | 移行済み | `orchestration/strategy.py` | 旧ログ文言の完全一致は未実施 |
| `boundary_gap` 本体 | 準完了まで移行済み | `orchestration/strategy.py` + `runtime/repository/boundary_gap_progress.py` | 実運用複数 cycle での最終確認、README 反映 |
| `verify_consistency` 本体 | 準完了まで移行済み | `orchestration/strategy.py` + `runtime/repository/consistency_classification.py` + `runtime/repository/consistency_dkw_summary.py` + `apps/cli/consistency_summary_main.py` | 実 run での CSV 更新確認、README 反映、旧 print 文言の一致確認 |
| `jama_edge` / `ttc_edge` / `worst_ttc` | 準完了まで移行済み | `orchestration/strategy.py` | 旧抽出ログ文言の一致確認、README 反映 |
| DKW sequential / simultaneous stop 判定 | 移行済み | `orchestration/dkw_mode.py` + `evaluation/dkw.py` | 旧の verbose 証明メッセージは未反映 |
| DKW history CSV 保存 (`_log_dkw_history`) | 移行済み | `runtime/repository/statistical_history.py` | なし |
| DKW fixed mode | 移行済み | `orchestration/dkw_mode.py` + `evaluation/dkw.py` + `runtime/repository/statistical_samples.py` | 実 run での保存内容確認は未実施 |
| Binomial CI stop 判定 | 移行済み | `orchestration/binomial_mode.py` + `evaluation/binomial_ci.py` | 旧の verbose メッセージは未反映 |
| Binomial CI history CSV 保存 (`_log_binomial_history`) | 移行済み | `orchestration/binomial_mode.py` + `runtime/repository/statistical_history.py` | なし |
| `dkw_region != custom` 時の自動 bounds 算出 | 移行済み | `orchestration/dkw_mode.py` | 実 run での bounds 算出ログ確認は未実施 |
| DKW / Binomial の region-aware 棄却サンプリング | 移行済み | `orchestration/dkw_mode.py` + `orchestration/binomial_mode.py` | 実 run でのサンプル保存内容確認は未実施 |
| `dkw_samples.csv` / `binomial_ci_samples.csv` 保存 | 移行済み | `runtime/repository/statistical_samples.py` | 実 run での CSV 更新確認は未実施 |
| error recovery (`last_recovered_loop`, timeout/error 行を少しずらして再試行) | 移行済み | `orchestration/strategy.py` | worker/orchestrator 側へさらに寄せるかは未決着 |
| `_print_final_report` と完了時の装飾 print | 移行済み | `orchestration/final_report.py` + `orchestration/strategy.py` + `orchestration/orchestrator.py` | 旧と同じ情報順で report payload / formatter はあるが、CLI の最終表示文言をどこまで旧に揃えるかは未調整 |
| `worker_count` 依存の `CACHE_SIZE` 自動決定 | 移行済み | `orchestration/cache_policy.py` + `apps/cli/orchestrator_main.py` + `orchestration/orchestrator.py` | 旧の `worker_count * 2` は復元済み。mode / resume ごとの追加最適化を戻すかは未決着 |
| FT4D confidence gap 集計 (`_flatten_ft4d_tree_nodes`, `_aggregate_ft4d_nodes`, `summarize_ft4d_confidence_gaps`) | 移行済み | `targets/bbsl/ft4d_analysis.py` | repository 化は未追加だが必須残差ではない |
| BBSL confidence gap 実行入口 (`inspect_bbsl_ft4d_confidence_gaps`) | 移行済み | `apps/cli/bbsl_confidence_gap_main.py` + `targets/bbsl/ft4d_analysis.py` | 実 run での summary 照合と README 反映は未実施 |

この表を踏まえると、`2026-08-14` 時点で
「まだ v2 にない残差」だけを抜き出した優先候補は次の 4 つである。

1. DKW / Binomial の実 run で、bounds 算出と棄却サンプリングのログ・CSV 挙動を確認
2. `boundary_gap` / `verify_consistency` / edge 系 / BBSL confidence gap の README / 運用手順反映
3. 旧最終表示文言を、v2 CLI 出力へどこまで戻すかの整理
4. BBSL confidence gap の実 dataset / 実 output を使った summary 一致確認

逆に言うと、AWSIM の通常戦略本体として重要な
explore / margin / focus / `boundary_gap` / `verify_consistency` / edge 系 の
主制御は、概ね `orchestration/strategy.py` 側へ寄せ終わっており、
DKW / Binomial CI の実行フローは
`orchestration/dkw_mode.py` / `orchestration/binomial_mode.py`
へ集約し終わっており、FT4D confidence gap 集計 helper は
`targets/bbsl/ft4d_analysis.py`、実行入口は
`apps/cli/bbsl_confidence_gap_main.py` へ分離済みである。

補足:

- `param` 直指定経路と `--headless` 経路は実装自体はあるが、
  代表 smoke の束確認はまだ計画書上の未完項目として残る。

#### モードごとの `viewer / plot / summary` の統一方針

ここでいう用語は次の意味で固定する。

- `summary`
  CSV / JSON / 標準出力で、人が mode の結果を確認できる要約。
  図は出さない。
- `plot`
  dataset や summary を入力に、グラフや画像を生成する可視化。
- `viewer`
  運用者が最初に叩く確認入口。
  実体は `summary` CLI でも `plot` CLI でもよい。

設計上の目的は、「mode ごとにバラバラの確認導線」を減らし、
どの mode でも

1. まず何を見るか
2. どの CLI を叩くか
3. 何が保存されるか

を同じ考え方で揃えることにある。

`2026-08-14` 時点の棚卸しは次の通り。

| 対象 | 現在の summary 入口 | 現在の plot 入口 | 現在の問題 | 正本にしたい受け先 |
| --- | --- | --- | --- | --- |
| dataset 汎用抽出 | `tools/analysis/extract_region_data.py` | なし | CSV 抽出はあるが、その後の確認導線が mode 非依存でない | `tools/analysis/extract_region_data.py` を維持 |
| `boundary_gap` | `tools/analysis/analyze_boundary_gap.py` | なし | 進捗 CSV と summary CLI はあるが、図示入口が未整理 | summary は `tools/analysis/analyze_boundary_gap.py`、必要なら plot を `tools/plot/boundary_gap.py` |
| `verify_consistency` | `apps/cli/consistency_summary_main.py` と `tools/analysis/analyze_ttc_consistency.py` | なし | summary 入口が `apps/cli` と `tools/analysis` に分かれている | summary 正本を `tools/analysis/*` 側へ寄せ、`apps/cli` は orchestrator 直結用途に限定 |
| BBSL confidence gap | `apps/cli/bbsl_confidence_gap_main.py` | なし | BBSL だけ入口命名が別系統で、summary JSON と標準出力の位置づけが未統一 | 実行 CLI は `apps/cli` のまま、閲覧用 summary は `tools/analysis/*` を別途持てる形に整理 |
| 汎用 dataset 3D 表示 | なし | `tools/plot/visualize_traces.py`, `tools/plot/visualize_traces_split.py`, `tools/plot/visualize_min_ttc_3d.py`, `tools/plot/visualize_risk_matrix.py` | 正本は `tools/plot/*` へ移行済み。root `visualize_*.py` は legacy wrapper | `tools/plot/*` を維持 |
| JAMA / collision 面・領域表示 | なし | `tools/plot/visualize_jama_zones.py`, `tools/plot/visualize_collision_regions.py`, `tools/plot/visualize_collision_surfaces.py` | 正本は `tools/plot/*` へ移行済み。root `visualize_*.py` は legacy wrapper | `tools/plot/*` を維持 |
| worker 比較 | なし | `tools/plot/visualize_worker_stats.py`, `tools/plot/visualize_worker_failure_clusters.py` | 正本は `tools/plot/*` へ移行済み。summary との導線整理だけ残る | `tools/plot/*` を維持 |

この表から、まず固定する設計ルールは次の 5 点である。

1. mode 本体は `orchestration/*` / `targets/*` / `runtime/repository/*` に残し、
   `tools/*` へ業務ロジックを戻さない
2. 人向け確認のうち「表・CSV・文章」は `tools/analysis/*` に寄せる
3. 人向け確認のうち「図」は `tools/plot/*` に寄せる
4. root 直下の `visualize_*.py` は互換入口として残し、
   正本は `tools/plot/*` に固定する
5. `viewer` は新ディレクトリ名ではなく、
   「summary を見るか plot を見るかを決める運用入口」という概念として扱う

この統一に向けた修正プランは次の 4 フェーズで進める。

1. 役割固定フェーズ
   `tools/analysis/*` を summary の正本、
   `tools/plot/*` を plot の正本、
   root `visualize_*.py` を legacy wrapper として README / 設計書に明記する。
   `apps/cli/*summary*` は orchestrator / target 直結用途とし、
   人手確認用と混ざる場合だけ `tools/analysis/*` 側へ再公開する。
2. mode 別 viewer 整理フェーズ
   `boundary_gap`,
   `verify_consistency`,
   BBSL confidence gap について、
   「まず summary を見る」「必要なら plot を見る」の順序を README に固定する。
   ここでは新しい directory を増やすより、
   `tools/analysis/<mode>_*` と `tools/plot/<mode>_*` の対応を揃えることを優先する。
3. 回帰確認フェーズ
   summary 系は no-sim で CSV / JSON / 標準出力の軽量回帰を持つ。
   plot 系は少なくとも import / 引数 / 出力 path の smoke を持ち、
   画像の中身比較までは必須にしない。

実装優先順位は次の順が自然である。

1. `verify_consistency` の summary 入口整理
   `apps/cli/consistency_summary_main.py` と
   `tools/analysis/analyze_ttc_consistency.py`
   の役割重複を切り分ける
2. BBSL confidence gap の閲覧専用 summary 入口を
   `tools/analysis/*` に補助追加するか判断
3. mode ごとの README / viewer 導線整理

完了条件は次の通り。

1. 各主要 mode が少なくとも 1 本の `summary` 入口を持つ
2. 汎用 plot CLI の本体が root 直下ではなく `tools/plot/*` にある
3. README に「まず何を見るか」が mode ごとに書かれている
4. 旧 root CLI を消さなくても、新しい正本の位置が一目で分かる

#### `orchestration/resume.py`
- 入力
  scenario、`resume_from`
- 出力
  復元結果、再開 loop
- 移植元
  `dataset_repo.py:restore_base_dataset`
  `dataset_repo.py:get_last_loop_num`
  `dataset_repo.py:get_last_loop_num_from_current`

### 6.5 runtime/cluster

#### `runtime/cluster/cluster_config.py`
- 入力
  なし
- 出力
  ノード設定
- 移植元
  `redis_cluster/cluster_config.py`

#### `runtime/cluster/cluster_manager.py`
- 入力
  scenario、mode、ext_mode、host worker 設定
- 出力
  Ray クラスタ起動状態
- 移植元
  `redis_cluster/cluster_manager.py:ClusterManager`
  `redis_cluster/cluster_manager.py:start_cluster`

#### `runtime/cluster/ray_queue.py`
- 入力
  task、worker status、completion
- 出力
  次タスク、stop signal、queue 状態
- 移植元
  `redis_cluster/task_queue.py:TaskQueueActor`

#### `runtime/cluster/host_worker.py`
- 入力
  scenario、mode、ext_mode、dkw 関連引数
- 出力
  host worker の起動停止
- 移植元
  `local_worker.py:HostWorkerManager`

### 6.6 runtime/container

#### 責務境界の判断基準

`runtime/container` と `targets/awsim/backend.py` の責務境界は、次の単純な基準で判断する。

- AWSIM 以外でも使えるなら `runtime/container`
- AWSIM だから必要なら `targets/awsim/backend.py`

この基準により、実装中に「これは共通基盤か、AWSIM 固有か」を迷ったときの判断を固定する。

具体例:

- `pkill`, `SIGINT`, `SIGKILL`, `cleanup`
  -> `runtime/container`
- `Xvfb`, `DISPLAY`, `headless` 実行
  -> `runtime/container`
- ログ出力先管理
  -> `runtime/container`
- AWSIM Labs の起動コマンド
  -> `targets/awsim/backend.py`
- Autoware の起動コマンド
  -> `targets/awsim/backend.py`
- Runtime Monitor の起動コマンド
  -> `targets/awsim/backend.py`
- `run_scenario.py` 相当のシナリオ起動コマンド生成
  -> `targets/awsim/backend.py`
- AWSIM の JSON 出力ファイル命名規約
  -> `targets/awsim/backend.py`

#### `runtime/container/profile.py`
- 入力
  machine role、case_kind、headless、host/container mode
- 出力
  `InfraTask` 群、実行プロファイル
- 移植元
  `run_manager.py` の `Task`
  `run_manager.py` の `INFRA_TASKS`
  `run_manager.py` の `AWSIM_CMD`, `AUTOWARE_CMD`

#### `runtime/container/launcher.py`
- 入力
  `InfraTask` 群、output_dir
- 出力
  起動済みプロセス群
- 移植元
  `redis_cluster/process_controller.py:start_process`
  `redis_cluster/process_controller.py:start_all_infra`
  `redis_cluster/process_controller.py:launch_scenario`

#### `runtime/container/supervisor.py`
- 入力
  実行中プロセス、timeout 条件
- 出力
  success/timeout 判定
- 移植元
  `run_manager.py` の監視ループ

#### `runtime/container/cleanup.py`
- 入力
  実行中プロセス
- 出力
  cleanup 完了
- 移植元
  `redis_cluster/process_controller.py:kill_client`
  `redis_cluster/process_controller.py:kill_all_processes`
  `redis_cluster/process_controller.py:kill_infra`
  `redis_cluster/process_controller.py:_force_cleanup_os`
  `redis_cluster/process_controller.py:cleanup_all`

#### `runtime/container/xvfb.py`
- 入力
  headless flag
- 出力
  DISPLAY 準備/停止
- 移植元
  `redis_cluster/process_controller.py:setup_xvfb`
- 実装メモ
  `XvfbController` は `targets/awsim/backend.py` から呼び出す。
  ここでは `start()`, `apply_environment()`, `stop()` だけを持ち、
  「いつ起動し、いつ止めるか」の寿命管理は target backend 側で行う。

### 6.7 runtime/repository

#### `runtime/repository/dataset_csv.py`
- 入力
  CSV path、row、case_kind
- 出力
  DataFrame、CSV 追記、max loop
- 移植元
  `dataset_repo.py`
  `redis_cluster/shared_store.py:_write_to_dataset`
  `awchecker.py` のローカル CSV 書き込み

#### `runtime/repository/dataset_restore.py`
- 入力
  `resume_from`、case_kind
- 出力
  `_base.csv` 復元
- 移植元
  `dataset_repo.py:restore_base_dataset`

#### `runtime/repository/shared_store.py`
- 入力
  input row、output row、timeout row
- 出力
  マージ済み dataset row
- 移植元
  `redis_cluster/shared_store.py:SharedStoreActor`

#### `runtime/repository/parameter_buffer.py`
- 入力
  `loop_num`、input、reason
- 出力
  バッファ保存、入力付き row
- 移植元
  `param_logger.py:log_parameters`
  `redis_cluster/shared_store.py:param_buffer`

#### `runtime/repository/local_history.py`
- 入力
  history path、loop_num
- 出力
  処理済み履歴
- 移植元
  `awchecker.py` の `processed_loops_history.csv` 処理

### 6.8 targets/awsim

#### `targets/awsim/backend.py`
- 入力
  `TestCase`、runtime profile、`case_kinds/<name>.py` の定義
- 出力
  `RawRunResult`
- 移植元
  `run_manager.py` の `dynamic_cmd` 生成
  `run_scenario.py:main`
  `run_manager.py` の JSON rename
  `run_manager.py` の timeout marker 書き込み

#### `targets/awsim/scenario_runner.py`
- 入力
  scenario type、動的パラメータ、`case_kinds/<name>.py` の定義
- 出力
  実行対象 scenario、または実行完了
- 役割
  scenario type を見て適切な scenario builder を呼ぶ司令役。
  `run_scenario.py` の中へシナリオ固有ロジックを書き足し続けず、
  `scenario_builders/<name>_builder.py` へ処理を振り分ける。
- 命名規約
  `case_kinds/uturn.py` と名前が衝突しないように、
  builder 側は `uturn.py` ではなく `uturn_builder.py` のように
  `<case_kind>_builder.py` で統一する。
- 将来拡張の置き場
  Uターンは `scenario_builders/uturn_builder.py`
  cutin を追加する場合は `scenario_builders/cutin_builder.py`
  のように増やす。
- 移植元
  `run_scenario.py:main`

#### `targets/awsim/scenario_builders/uturn_builder.py`
- 入力
  network、動的パラメータ、固定パラメータ、lane offset factory
- 出力
  Uターン scenario object
- 役割
  Uターン固有の組み立てロジックだけを保持する。
  速度帯ごとの `ego_init` オフセット判定、
  `make_uturn_scenario(...)` への引数構成をここへ閉じ込める。
- 移植元
  `run_scenario.py` の Uターン分岐

#### `targets/awsim/result_interpreter.py`
- 入力
  `RawRunResult`、rule spec、verifiers
- 出力
  `EvaluationRecord`
- 移植元
  `awchecker.py` の解析本体
  `RawRunResult.evidence` から `EvaluationRecord.output` / `EvaluationRecord.meta` を構成する処理

#### `targets/awsim/kinematics_bridge.py`
- 入力
  JSON path、mode、target NPCs
- 出力
  `output` に格納する運動学系の値
- 移植元
  `awchecker.py` の `AWKinematicsPipeline` 呼び出し部

#### `targets/awsim/dataset_adapter.py`
- 入力
  CSV path または DataFrame
- 出力
  正規化 DataFrame、sample id 集合
- 移植元
  `adapters/awsim/dataset_adapter.py:AWSIMDatasetAdapter`

#### `targets/awsim/verification_input.py`
- 入力
  DataFrame または `EvaluationRecord` 群、`case_kinds/<name>.py` の event 定義
- 出力
  `VerificationInput`
- 移植元
  `adapters/awsim/event_builder.py:_resolve_condition`
  `adapters/awsim/event_builder.py:AWSIMEventSetBuilder`

#### `targets/awsim/case_kinds/uturn.py`
- 入力
  なし
- 出力
  Uターン用の target 設定一式
- 含むもの
  探索範囲
  重要パラメータ
  rule spec
  result label 定義
  focus point 定義
  event 定義
- 移植元
  `configs/uturn.py`

#### scenario builder 拡張ルール

新しい scenario type を足すときは、次の 3 か所を基本単位とする。

1. `targets/awsim/case_kinds/<name>.py`
   定義データを置く
2. `targets/awsim/scenario_builders/<name>_builder.py`
   その scenario 固有の組み立て処理を置く
3. `targets/awsim/scenario_runner.py`
   `<name>` を受けたときに、その builder へ振り分ける

このとき、`scenario_runner.py` に個別ロジックを溜め込まない。
個別処理は必ず builder 側へ寄せ、
`scenario_runner.py` は「選ぶ」「呼ぶ」「実行する」だけに保つ。

#### `cutin` を追加するときの手順

`cutin` を足すときは、次の順で進める。

1. `targets/awsim/case_kinds/cutin.py`
   `PARAM_RANGES`, `FIXED_PARAMS`, `RESULT_LABELS`, `FORMULAS`,
   `EVENT_DEFINITIONS`, `TIMEOUT_SEC` など、
   cutin 固有の定義データを置く。
2. `targets/awsim/scenario_builders/cutin_builder.py`
   `build_cutin_scenario(...)` を作り、
   lane offset 計算、速度変換、scenario object 構築など
   cutin 固有の組み立て処理をここへ閉じ込める。
3. `targets/awsim/scenario_runner.py`
   `scenario_type == "cutin"` のとき
   `cutin_builder.py` を呼ぶ分岐だけ追加する。
   ここでは個別計算を書かない。
4. `run_scenario.py`
   原則として変更しない。
   互換 CLI として `scenario_runner.py` を呼ぶだけに保つ。
5. `targets/awsim/result_interpreter.py`
   cutin 用に rule spec や result label が変わるなら、
   `case_kinds/cutin.py` 経由で自然に切り替わることを確認する。
6. `targets/awsim/verification_input.py`
   cutin 用 event 定義が `case_kinds/cutin.py` から読まれることを確認する。
7. テスト
   最低でも次を追加する。
   `tests/unit/targets/test_awsim_cutin_builder.py`
   `tests/unit/targets/test_awsim_scenario_runner.py` の cutin 分岐
   `tests/smoke/test_run_scenario_cli.py` または cutin 専用 smoke 1 本

判断基準は単純である。

- 定義データなら `case_kinds/cutin.py`
- 組み立て処理なら `scenario_builders/cutin_builder.py`
- 振り分けなら `scenario_runner.py`

この 3 層を崩さないことで、
新しい scenario type を増やしても
「どこに何を書くか」がぶれないようにする。

### 6.9 targets/bbsl

#### `targets/bbsl/backend.py`
- 入力
  `TestCase`、`profile.py` の設定
- 出力
  `RawRunResult`
- 移植元
  `run_external_verifier.py` の要求生成意図
  `adapters/bbsl/runner.py`
- 補足
  `targets/bbsl/backend.py` は BBSL target の実行入口を 1 か所に固定する。
  経路は 2 つだけにする。

  - fixture 再利用:
    `TestCase.input["fixture_path"]` または `["raw_result_json"]` を受け取り、
    既存の raw result JSON をそのまま `RawRunResult.evidence["raw_result_json"]` へ載せる
  - 実験実行:
    `TestCase.input` に実行パラメータを受け取り、
    `targets/bbsl/runner.py:run_bbsl_experiment` を呼んで raw result JSON を生成する

  実験実行で最初に受ける標準キーは以下に固定する。

  - `target_repo`
    実験対象の BBSL リポジトリ
  - `mini`
    mini 実行かどうか
  - `max_images`
    画像数制限
  - `tree` または `tree_mode`
    フォルトツリー選択
  - `sigma_pf_source`
  - `sigma_pb_mode`
  - `and_rule`
  - `detect_timeout`
  - `reuse_existing_output`

  判断ルールは以下で固定する。

  - すでに出力済み JSON を再利用したいときは `reuse_existing_output=True`
  - 実際に BBSL 実験を走らせたいときは `target_repo` を渡して通常実行する
  - `fixture_path/raw_result_json` があるときは fixture 経路を優先する
  - どちらの情報もない空入力はエラーにする

  これにより、BBSL を拡張するときに触る場所は
  `targets/bbsl/backend.py`
  だけで済む。

#### `targets/bbsl/result_interpreter.py`
- 入力
  BBSL raw output
- 出力
  `EvaluationRecord`
- 移植元
  `estimator.py` の BBSL raw result 解釈部
  BBSL raw result を `EvaluationRecord.output` / `EvaluationRecord.meta` に正規化する処理

#### `targets/bbsl/dataset_adapter.py`
- 入力
  output JSON path または dict
- 出力
  正規化 BBSL データ
- 移植元
  `targets/bbsl/dataset_adapter.py:BBSLExperimentAdapter`

#### `targets/bbsl/verification_input.py`
- 入力
  `EvaluationRecord` または BBSL output dict
- 出力
  `VerificationInput`
- 移植元
  `targets/bbsl/event_builder.py:BBSLEventSetBuilder`

#### `targets/bbsl/event_builder.py`
- 入力
  BBSL output dict、clean baseline、noisy batch outputs
- 出力
  FT4D 用 event 集合
- 移植元
  `adapters/bbsl/event_builder.py:BBSLEventSetBuilder`

#### `targets/bbsl/runner.py`
- 入力
  BBSL 実行パラメータ
- 出力
  raw result JSON path、batch cleanup 結果
- 移植元
  `adapters/bbsl/runner.py`

#### `targets/bbsl/profile.py`
- 入力
  tree、sigma_pf_source、sigma_pb_mode、and_rule
- 出力
  BBSL 実験プロファイル
- 移植元
  `run_bbsl_local_ft4d.py`

### 6.10 verifiers

#### `verifiers/maude/backend.py`
- 入力
  JSON path、formulas file、env
- 出力
  stdout/stderr
- 移植元
  `awchecker.py` の `subprocess.run(aw_checkerpy.py)` 部分

#### `verifiers/maude/evaluator.py`
- 入力
  JSON path、rule spec、Maude 実行結果
- 出力
  `EvaluationRecord.output` に入る判定値群
- 移植元
  `awchecker.py` の formulas 生成部
  `awchecker.py` の regex 抽出
  `awchecker.py` の出力値構築処理

#### BBSL legacy 互換入口
- 入力
  `ExternalVerificationRequest`
- 出力
  `ExternalVerificationResult`
- 移植元
  `external_verifiers/bbsl_ft4d.py`
  旧 `run_external_verifier.py` から呼んでいた互換入口
- 補足
  現在の実装上の実体は `verifiers/compatibility/legacy_bbsl_ft4d_adapter.py` である。
  `external_verifiers/bbsl_ft4d.py` は旧 import 互換の薄い wrapper とする。
  `run_bbsl_local_ft4d.py` を subprocess で呼ぶのではなく、
  legacy な request/response 形だけを保って
  `targets/bbsl/backend.py -> targets/bbsl/result_interpreter.py -> targets/bbsl/verification_input.py -> evaluation/ft4d_service.py`
  を in-process で直接束ねる。

  つまり legacy adapter は「別系統の実行本体」ではなく、
  新経路への薄い互換入口としてだけ残す。
  BBSL 系の新経路への完全移植が完了するまでだけ残す

### 6.11 evaluation

#### `evaluation/gp_boundary.py`
- 入力
  `list[EvaluationRecord]`、target metric
- 出力
  GP model、mean/std
- 正式 API
  `prepare_training_data_frame(...)`
  `fit_gp_boundary_model(...)`
  `predict_uncertainty(...)`
- 移植元
  `estimator.py:train`
  `estimator.py:predict_uncertainty`
  `estimator.py:load_training_data`
- 補足
  新実装の正本は `evaluation/gp_boundary.py` とし、
  `orchestration/strategy.py` と `estimator.py` はこの API へ委譲する。
  `GPBoundaryService` は互換 wrapper としてのみ残す。

#### `evaluation/dkw.py`
- 入力
  `list[EvaluationRecord]`、request
- 出力
  DKW report
- 移植元
  `estimator.py:calculate_dkw_bounds`
  `estimator.py:calculate_quantile_with_dkw`
  `estimator.py:evaluate_and_summarize_dkw`
  `estimator.py:evaluate_and_summarize_dkw_multiple`

#### `evaluation/binomial_ci.py`
- 入力
  `list[EvaluationRecord]`、request
- 出力
  CI report
- 移植元
  `estimator.py:calculate_binomial_confidence_interval`
  `estimator.py:evaluate_and_summarize_binomial_ci`

#### `evaluation/ft4d_service.py`
- 入力
  `VerificationInput`
- 出力
  `FT4DResult`
- 移植元
  `run_ft4d_smoke.py`
  `run_bbsl_local_ft4d.py`
  `verification_core/ft4d/*` を呼ぶ既存 glue 部分

#### この段階で `evaluation/` に入れないもの

- `point_extractors.py` 系の探索候補抽出
  まずは `orchestration/strategy.py` に寄せる。探索文脈への依存が強いため。
- stop/continue 判定
  まずは `orchestration/strategy.py` に残す。全体制御の文脈依存が強いため。
- filter や weighting の helper
  まだ複数箇所で再利用していない限り、使う側のモジュール内部に置く。
  2回以上同じ処理を書く段階で helper 化を検討する。

### 6.12 verification_core/ft4d

#### `verification_core/ft4d/*`
- 入力
  `VerificationInput`
- 出力
  `FT4DResult`
- 移植元
  現行 `verification_core/ft4d/*` を維持

## 7. 最初に作るべき新ファイル一式

### 優先度 0

まず設計と共通型を固定する。

- `docs/architecture.md`
- `docs/migration_plan.md`
- `contracts/execution.py`
- `contracts/evaluation.py`
- `contracts/statistics.py`
- `contracts/verification.py`

理由:

- 他ファイルの入出力がぶれなくなる
- 新旧比較の基準ができる

### 優先度 1

保存と復元の流れを先に共通化する。

- `runtime/repository/dataset_csv.py`
- `runtime/repository/dataset_restore.py`
- `runtime/repository/shared_store.py`
- `runtime/repository/parameter_buffer.py`
- `runtime/repository/local_history.py`

理由:

- dataset の流れが現行システムの中心だから
- 実行部より先に保存面を固定したほうが安全

### 優先度 2

AWSIM の結果解釈を分離する。

- `targets/awsim/result_interpreter.py`
- `targets/awsim/kinematics_bridge.py`
- `verifiers/maude/backend.py`
- `verifiers/maude/evaluator.py`

理由:

- `awchecker.py` の責務分離効果が最も大きい
- `EvaluationRecord` への正規化が始められる

### 優先度 3

評価処理の共通核を分離する。

- `evaluation/dkw.py`
- `evaluation/binomial_ci.py`
- `evaluation/gp_boundary.py`
- `evaluation/ft4d_service.py`

理由:

- `estimator.py` の再利用核を分離できる
- 共通化しすぎず、再利用の中心だけを先に固定できる

### 優先度 4

実行基盤と AWSIM 実行部を切り分ける。

- `runtime/container/profile.py`
- `runtime/container/launcher.py`
- `runtime/container/supervisor.py`
- `runtime/container/cleanup.py`
- `runtime/container/xvfb.py`
- `targets/awsim/backend.py`
- `targets/awsim/case_kinds/uturn.py`

理由:

- `run_manager.py` の責務を分離できる
- コンテナ部を共通基盤として扱える

### 優先度 5

司令塔と worker loop を新構造へ寄せる。

- `run_orchestrator_v2.py`
- `run_worker_v2.py`
- `runtime/cluster/cluster_config.py`
- `runtime/cluster/cluster_manager.py`
- `runtime/cluster/ray_queue.py`
- `runtime/cluster/host_worker.py`
- `orchestration/orchestrator.py`
- `orchestration/worker_loop.py`
- `orchestration/strategy.py`
- `orchestration/resume.py`

理由:

- 実行基盤と統計評価の双方が揃ってから移行したほうが安全
- 旧 `master_orchestrator.py` / `run_manager.py` を直接壊さずに新運用本体を横に育てられる

#### `run_worker_v2.py` を中心にした最初の最小構成

最初の `run_worker_v2.py` は、まず AWSIM 1 target 専用の最小 worker として作る。

最初のゴールは、

- `TestCase`
- `RawRunResult`
- `EvaluationRecord`

の 3 段を 1 ケースぶんだけ通し、保存まで確認すること。

最初から cluster 全体や container 共通化を広げすぎない。

##### 最小 tree

```text
AWSIM_launch/
├── run_worker_v2.py
├── apps/
│   └── cli/
│       └── worker_main.py
├── orchestration/
│   └── worker_loop.py
├── runtime/
│   ├── cluster/
│   │   ├── task_queue_gateway.py
│   │   └── result_sink.py
│   └── repository/
│       ├── shared_store.py
│       └── local_history.py
├── targets/
│   └── awsim/
│       ├── backend.py
│       ├── result_interpreter.py
│       └── verification_input.py
├── contracts/
│   ├── execution.py
│   └── evaluation.py
└── tests/
    ├── unit/
    │   ├── orchestration/test_worker_loop.py
    │   ├── runtime/test_task_queue_gateway.py
    │   ├── runtime/test_result_sink.py
    │   └── targets/test_awsim_backend.py
    └── smoke/
        └── test_run_worker_v2_local.py
```

##### 各ファイルの役割

###### `run_worker_v2.py`
- 入力
  CLI引数、環境変数
- 出力
  終了コード
- 役割
  新 worker 本体の入口。
  中身は薄くして `apps/cli/worker_main.py` を呼ぶだけにする。
  `--headless` を受けた場合も、この層では解釈しすぎず、
  `apps/cli/worker_main.py` へ素通しするだけにする。
- 移植元
  `run_manager.py:main`

###### `apps/cli/worker_main.py`
- 入力
  CLI引数、環境変数
- 出力
  `WorkerLoop` の起動
- 役割
  設定読込、target backend 選択、queue/sink 接続。
  `--headless` を `AWSIMBackendConfig.runtime_profile.headless` へ変換する。
- 移植元
  `run_manager.py` の引数処理、初期化部

###### `orchestration/worker_loop.py`
- 入力
  `TaskQueueGateway`、target backend、result interpreter、result sink
- 出力
  `EvaluationRecord` を sink へ流す
- 役割
  worker の本体ロジック。
  `TestCase -> RawRunResult -> EvaluationRecord -> 保存` の流れを調停する。
- 移植元
  `run_manager.py` の監視ループ、timeout/success/error 分岐

###### `runtime/cluster/task_queue_gateway.py`
- 入力
  queue actor 名、接続設定
- 出力
  `TestCase`
- 役割
  旧 Ray queue を新 worker 側から隠す薄い gateway。
- 移植元
  `redis_cluster/task_queue.py` 利用部
  `run_manager.py` のタスク取得部

###### `runtime/cluster/result_sink.py`
- 入力
  `EvaluationRecord`
- 出力
  保存完了
- 役割
  shared store または local fallback へ保存する境界。
  `CompositeResultSink` / `OptionalResultSink` を使い、
  JSONL を主保存先にしつつ shared store / dataset CSV 側は縮退可能にする。
- 移植元
  `awchecker.py` / `run_manager.py` の CSV 保存意図
  shared store 接続意図

###### `targets/awsim/backend.py`
- 入力
  `TestCase`
- 出力
  `RawRunResult`
- 役割
  AWSIM 実行だけを担当する。
  実行コマンド生成、timeout marker、raw result path 返却を閉じ込める。
  さらに resident infra と case 単位 client の寿命差を吸収し、
  `refresh_infra()` 経由の再起動ポリシーを受ける。
- 移植元
  `run_manager.py` の `run_scenario.py` 実行
  `run_manager.py` の timeout marker 書き込み
  `run_manager.py` の JSON rename 周辺

##### 最初の実装順

1. `run_worker_v2.py`
2. `apps/cli/worker_main.py`
3. `orchestration/worker_loop.py`
4. `targets/awsim/backend.py`
5. `runtime/cluster/result_sink.py`
6. `runtime/cluster/task_queue_gateway.py`
7. `tests/smoke/test_run_worker_v2_local.py`

理由:

- 先に worker の骨格を作る
- 次に 1 件実行の本体を作る
- queue 接続は最後でよい
- 最初から Ray や cluster 全体を抱え込まない

##### 最初の完成条件

最初の完成条件は次で十分とする。

- queue なしで `TestCase` を 1 件直接与えられる
- `targets/awsim/backend.py` が `RawRunResult` を返す
- `targets/awsim/result_interpreter.py` が `EvaluationRecord` を返す
- `runtime/cluster/result_sink.py` が保存できる
- `tests/smoke/test_run_worker_v2_local.py` が通る

この段階では、まだ分散 worker 全体ではなく、単発 worker として完成すればよい。

headless 実行を使うときの新経路:

1. `run_orchestrator_v2.py --headless` または `run_worker_v2.py --headless` を受ける
2. `apps/cli/orchestrator_main.py` / `apps/cli/worker_main.py` が `headless=True` を構成する
3. `targets/awsim/backend.py` が `runtime_profile.headless` を見て `XvfbController` を使う
4. `runtime/container/xvfb.py` が `DISPLAY` と `VK_ICD_FILENAMES` を設定する
5. trace 監視完了後に `XvfbController.stop()` を呼ぶ

新 CLI の headless 実行例:

```bash
python3 run_orchestrator_v2.py \
  --param dx0=15.0 \
  --param ego_speed=35.0 \
  --param npc_speed=14.0 \
  --output /tmp/orchestrator_records.jsonl \
  --simulation-output-dir /tmp/awsim_traces \
  --headless
```

##### その次に足すもの

最小 worker が通ったあとで、次の順に足す。

1. `task_queue_gateway.py` を有効化して queue から取る
2. `shared_store` への保存を標準化する
3. timeout / retry / local history を追加する
4. そのあとで `run_orchestrator_v2.py` を作る

##### `run_manager.py` -> v2 worker 移行タスク表

旧 `run_manager.py` は、v2 worker 完成までは直接いじらない。
先に新経路だけで旧責務を代替できる状態を作り、
最後に薄い互換 wrapper へ縮退させる。

| 旧 `run_manager.py` に残っている責務 | 新受け先候補 | 優先順位 | 完了条件 |
| --- | --- | --- | --- |
| Ray 接続初期化と actor 探索待ち | `runtime/cluster/task_queue_gateway.py` または `runtime/cluster/ray_client.py` 的な新 helper | 高 | v2 worker 単体で queue actor 名だけ渡せば接続・再試行・失敗終了まで完結し、`run_manager.py` 側の接続処理が不要になる |
| shared store 不在時の縮退運用 | `runtime/cluster/result_sink.py` | 高 | shared store あり/なしの両方で `EvaluationRecord` 保存経路が定まり、警告と local fallback の挙動が v2 worker 内で閉じる |
| infra 常駐プロセス管理 (`AWSIM` / `Autoware` / `Runtime Monitor` / `AW Checker`) | `targets/awsim/backend.py` + `runtime/container/process_manager.py` + `runtime/container/infra_tasks.py` | 高 | v2 worker が旧 worker と同等の infra 起動構成を自前で作れ、1 回実行・連続実行・refresh 再起動の各経路で旧 `ProcessManager` なしに回る |
| 初回長待機などの起動ポリシー | `targets/awsim/backend.py` または `runtime/container/supervisor.py` | 中 | 初回起動時と通常 cycle 時の待機差分が v2 設定として表現され、旧 worker の手書き sleep に依存しない |
| OS レベルの強制 cleanup | `runtime/container/cleanup.py` | 中 | timeout / error / refresh / 正常終了の各ケースで cleanup 対象と順序が v2 側に集約され、旧 kill ロジック参照が不要になる |
| timeout / success / error の詳細 print 文言 | `apps/cli/worker_main.py` または `orchestration/worker_loop.py` | 低 | 必要な文言差分を戻すか捨てるか判断が済み、旧 worker の print を参照しなくてよい |
| 旧 CLI 互換引数の受理 | `apps/cli/worker_main.py` | 中 | 運用で必要な旧オプションを v2 worker が受けられ、旧 `run_manager.py` を呼ぶ理由が「互換入力」でも残らない |

##### 2026-08-14 時点で no-sim 確認済みの範囲

- `scripts/check_v2_no_sim.sh` で、`run_worker_v2.py` の fixture 実行、`run_orchestrator_v2.py` の local queue 実行、主要 unit/regression をまとめて確認できる。
- `apps/cli/worker_main.py` は no-sim で、queue 実行時の `timeout` 保存、`refresh_requested` summary、history 重複スキップ、`no_task` 終了、shared store actor / dataset CSV sink 障害時の警告付き継続を確認済み。
- `runtime/cluster/result_sink.py` は no-sim で、JSONL 主保存を維持しながら shared store / dataset CSV 側だけが失敗しても worker 全体を落とさない縮退を確認済み。
- `targets/awsim/backend.py` と `runtime/container/process_manager.py` は no-sim で、`stop_case_scoped_processes()`, `refresh_non_resident_infra()`, `shutdown_all()` の resident/case 分離、timeout marker、artifact rename を確認済み。
- ここで確認したのは「v2 単体の責務境界が崩れていないこと」であり、旧入口との完全一致や実シミュレータ実行結果の一致確認まではまだ含めない。

##### `master_orchestrator.py` -> v2 orchestrator 移行タスク表

旧 `master_orchestrator.py` も、v2 orchestrator 完成までは直接いじらない。
cluster と queue の運用責務を新経路へ移し切ったあとで、
最後に薄い互換 wrapper へ縮退させる。

| 旧 `master_orchestrator.py` に残っている責務 | 新受け先候補 | 優先順位 | 完了条件 |
| --- | --- | --- | --- |
| cluster 自動起動 (`ClusterManager.start_cluster`) | `runtime/cluster/cluster_manager.py` を使う v2 起動層。必要なら `apps/cli/orchestrator_cluster_main.py` 的な専用入口 | 高 | v2 入口だけで cluster 起動から worker 実行開始まで通り、旧 master を起動しなくても実運用を開始できる |
| detached actor 作成 / 再接続 / node affinity | `runtime/cluster/task_queue_gateway.py` とは別に `runtime/cluster/actor_runtime.py` 的な管理層 | 高 | queue actor / shared store actor の生成・再接続・停止が v2 側で完結し、旧 Ray actor 作成コードを参照しない |
| host worker 起動 | `runtime/cluster/host_worker.py` または `orchestration/orchestrator.py` から呼ぶ専用 runner | 中 | `with_host_worker` 相当の運用を v2 orchestrator から起動・停止できる |
| queue の high-water / low-water 制御 | `orchestration/orchestrator.py` | 高 | worker 数に応じた refill 戦略を v2 orchestrator が持ち、旧 `MAX_QUEUE_SIZE` / `REFILL_THRESHOLD` ロジック不要で同等運用できる |
| resume 時の start count 例外処理 (`dkw_fixed` を含む) | `orchestration/resume.py` + `orchestration/orchestrator.py` | 高 | 通常 resume と `dkw_fixed` resume の差分が v2 resume サービスだけで再現される |
| mode 別の進捗表示と stop 理由表示 | `apps/cli/orchestrator_main.py` または `orchestration/orchestrator.py` | 低 | 運用で必要な監視表示が v2 側で十分になり、旧 master の標準出力を参照しなくてよい |
| stop signal の遠隔伝播 | `runtime/cluster/ray_queue.py` まわりの v2 queue 制御 | 中 | strategist 停止、目標回数到達、KeyboardInterrupt の各ケースで v2 orchestrator から stop が伝わる |
| 旧 CLI / config 互換の受理 | `apps/cli/orchestrator_main.py` | 中 | 運用で必要な `resume_from`, `headless`, 統計オプション, focus 指定を v2 入口だけで受理できる |

##### 旧入口を「不要」と言える判定条件

`run_manager.py` と `master_orchestrator.py` を不要化してよいのは、
新経路が次を満たしたあとに限る。

1. `run_worker_v2.py` 経路だけで queue 実行、history 重複スキップ、refresh 再起動、timeout/error/success 保存が通る
2. `run_orchestrator_v2.py` 経路だけで cluster 起動、resume、queue refill、stop 伝播が通る
3. 旧入口と v2 入口で、少なくとも 1 本の代表シナリオについて `records.jsonl` / dataset CSV / 統計 CSV の更新結果が一致する
4. その確認が済むまで、旧 `run_manager.py` / `master_orchestrator.py` は原則直接編集しない

注記:

- 2026-08-14 時点では 1 のうち no-sim 範囲はかなり確認できている。
- ただし 3 の「旧入口との代表シナリオ一致」はまだ完了条件として残る。

##### 最初の段階でやらないこと

最初の段階では、次はまだ入れない。

- `master_orchestrator.py` の直接修正
- `run_manager.py` の直接修正
- container 共通化の全面実装
- BBSL 対応の同時着手
- strategy までの一括接続

### 優先度 6

BBSL を AWSIM と同列の target として整理する。

- `targets/bbsl/backend.py`
- `targets/bbsl/result_interpreter.py`
- `targets/bbsl/dataset_adapter.py`
- `targets/bbsl/verification_input.py`
- `targets/bbsl/profile.py`
- `verifiers/compatibility/legacy_bbsl_ft4d_adapter.py`

理由:

- AWSIM 側の枠組みが固まったあとに寄せたほうが実装しやすい

#### BBSL 拡張時の実装ルール

BBSL で新しい実験条件や新しい実行モードを足すときは、
まず `targets/bbsl/backend.py` だけを触る。

増やし方の基準は以下に固定する。

- 既存 raw result JSON を読むだけなら
  `fixture_path` / `raw_result_json` 経路へ寄せる
- BBSL 側のスクリプトを実行して JSON を作るなら
  `target_repo` を受ける実行経路へ寄せる
- すでに BBSL 側で生成済みの標準出力を再利用するなら
  `reuse_existing_output=True` で `default_output_path()` を使う

したがって、BBSL 拡張時の最初の確認項目は次の 2 つにする。

1. その変更は fixture 再利用なのか、実験実行なのか
2. 実行パラメータの追加で済むのか、`result_interpreter.py` / `verification_input.py` まで広がるのか

ルール:

- 実行方法の差分は `targets/bbsl/backend.py`
- raw output の解釈差分は `targets/bbsl/result_interpreter.py`
- FT4D へ渡す形の差分は `targets/bbsl/verification_input.py`
- BBSL raw output から FT4D を回す橋渡し差分は `targets/bbsl/ft4d_bridge.py`
- BBSL batch-loop / resume / clean baseline / noisy batch の差分は `targets/bbsl/batch_loop.py`

補足:

- `estimator.py` 内の旧 BBSL/FT4D 実装は、移行完了までは参照実装と回帰比較用として残す
- ただし新規修正はそこへ足さない
- 新しい修正先は `targets/bbsl/profile.py`, `targets/bbsl/ft4d_bridge.py`, `targets/bbsl/batch_loop.py` に固定する

この分け方を守ることで、
BBSL を拡張するときも
「どこを変えるべきか」が先に固定される。

## 8. 推奨する最初の実装順

1. `docs/*`
2. `contracts/*`
3. `runtime/repository/*`
4. `targets/awsim/result_interpreter.py` と `verifiers/maude/*`
5. `evaluation/*`
6. `runtime/container/*`
7. `orchestration/*`
8. `targets/bbsl/*`

## 9. 既存コードの扱い

移行完了までは既存コードを残す。

- 旧コードは参照実装
- 旧コードは回帰比較対象
- 新コードは新規ファイルとして追加
- 十分揃ってから旧 CLI をラッパー化

### 9.1 運用本体の扱い

運用本体については、さらに明確に次をルールとする。

- `master_orchestrator.py` は当面直接修正しない
- `run_manager.py` は当面直接修正しない
- `local_worker.py` も当面直接修正しない
- 新しい運用本体は `run_orchestrator_v2.py`, `run_worker_v2.py` として追加する
- 新しい本体ロジックは `apps/cli/*` と `orchestration/*` と `runtime/*` 側へ寄せる
- 旧本体は参照実装、回帰比較対象、緊急時の rollback 用として残す

意図:

- 運用本体は結合が強く、直接修正すると壊れやすい
- 新旧比較と rollback をしやすくする
- 新しい抽象化が安定するまで旧実装を安全に保持する

対象となる旧コードの中核:

- `master_orchestrator.py`
- `run_manager.py`
- `awchecker.py`
- `estimator.py`
- `strategist.py`
- `dataset_repo.py`
- `param_logger.py`
- `redis_cluster/*`
- `adapters/awsim/*`
- `adapters/bbsl/*`
- `external_verifiers/*`

## 10. 補足

- FT4D は `verification_core/ft4d/` に置く共通コアとして扱う
- コンテナ基盤は `runtime/container/` に集約する
- AWSIM と BBSL は `targets/` 配下で横並びに扱う
- Maude などの判定器は `verifiers/` に置く

この文書は、今後の分解・移植・新規実装の基準とする。

追加の実装ルールは [implementation_rules.md](/home/passd/AWSIM_launch/docs/implementation_rules.md) を参照する。
