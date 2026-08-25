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
- 旧コードは必要な範囲で設計経緯の参照に留め、最終受入は新フレーム単独で判断する
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

## 3. 目標ディレクトリ構成

この tree は移行完了後に正本として残したい目標構造を示す。移行途中は legacy 配置や互換 wrapper が一時的に併存してよい。

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
│       ├── orchestrator_cluster_main.py
│       ├── worker_main.py
│       ├── result_interpreter_main.py
│       ├── external_verifier_main.py
│       ├── ft4d_smoke_main.py
│       ├── bbsl_local_ft4d_main.py
│       └── bbsl_confidence_gap_main.py
├── contracts/
│   ├── execution.py
│   ├── evaluation.py
│   ├── statistics.py
│   └── verification.py
├── orchestration/
│   ├── orchestrator.py
│   ├── worker_loop.py
│   ├── strategy.py
│   ├── dkw_mode.py
│   ├── binomial_mode.py
│   ├── final_report.py
│   ├── cache_policy.py
│   └── resume.py
├── runtime/
│   ├── cluster/
│   │   ├── cluster_config.py
│   │   ├── cluster_manager.py
│   │   ├── ray_queue.py
│   │   ├── actor_runtime.py
│   │   ├── ray_client.py
│   │   ├── task_queue_gateway.py
│   │   ├── result_sink.py
│   │   └── host_worker.py
│   ├── container/
│   │   ├── profile.py
│   │   ├── launcher.py
│   │   ├── supervisor.py
│   │   ├── cleanup.py
│   │   ├── xvfb.py
│   │   ├── runner.py
│   │   ├── process_manager.py
│   │   └── artifact_watcher.py
│   └── repository/
│       ├── dataset_csv.py
│       ├── dataset_restore.py
│       ├── shared_store.py
│       ├── parameter_buffer.py
│       ├── local_history.py
│       ├── boundary_gap_progress.py
│       ├── consistency_classification.py
│       ├── consistency_dkw_summary.py
│       ├── statistical_history.py
│       └── statistical_samples.py
├── targets/
│   ├── registry.py
│   ├── awsim/
│   │   ├── backend.py
│   │   ├── result_interpreter.py
│   │   ├── verification_input.py
│   │   ├── kinematics_bridge.py
│   │   ├── dataset_adapter.py
│   │   ├── scenario_runner.py
│   │   ├── scenario_builders/
│   │   └── case_kinds/
│   └── bbsl/
│       ├── backend.py
│       ├── runner.py
│       ├── result_interpreter.py
│       ├── verification_input.py
│       ├── dataset_adapter.py
│       ├── event_builder.py
│       ├── ft4d_bridge.py
│       ├── ft4d_analysis.py
│       ├── batch_loop.py
│       ├── underconfident_loop.py
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
│   └── plot/
└── third_party/
    └── AW_Kinematics_Extractor/
```

## 4. ディレクトリ責務

- `apps/cli/`
  ユーザーが直接叩く入口。引数を受け取り、内部サービスを呼ぶだけにする。

- `contracts/`
  モジュール間で共通に使う入出力型を定義する。ここを先に固める。

- `orchestration/`
  実験全体の流れをつなぐ。探索、実行、保存、停止判断を調停する。

- `runtime/cluster/`
  Ray クラスタ、ワーカー、ホストワーカー、キュー、result sink などの分散実行基盤。

- `runtime/container/`
  コンテナやプロセス実行の共通基盤。AWSIM と BBSL のどちらにも従属しない。

- `runtime/repository/`
  CSV、共有ストア、進捗 CSV、resume 用復元、統計履歴などの永続化。

- `targets/awsim/`
  AWSIM を検査対象として扱うための差分実装。
  `backend.py`, `result_interpreter.py`, `verification_input.py`, `case_kinds/*`, `scenario_builders/*` を主な変更点とする。

- `targets/bbsl/`
  BBSL を検査対象として扱うための差分実装。
  `backend.py`, `result_interpreter.py`, `verification_input.py`, `runner.py`, `event_builder.py` を主な変更点とする。

- `verifiers/`
  Maude などの判定器、または外部の検証ツール。
  互換用途は `compatibility/` に隔離する。

- `evaluation/`
  DKW、二項 CI、GP 境界推定、FT4D 呼び出しなどの共通評価処理。

- `verification_core/ft4d/`
  FT4D の共通コア。対象や verifier に属さない。

- `tools/analysis/`
  人が結果を確認するための summary 系 CLI を置く。

- `tools/plot/`
  人が図を確認するための plot 系 CLI を置く。

- `third_party/AW_Kinematics_Extractor/`
  既存外部コンポーネント。すぐに分解せず bridge 経由で使う。

### 4.1 `runtime/container/` と `targets/awsim/backend.py` の責務境界

`runtime/container/` と `targets/awsim/backend.py` の責務境界は、次の基準で判断する。

- AWSIM 以外でも使えるなら `runtime/container/`
- AWSIM だから必要なら `targets/awsim/backend.py`

具体例:

- `pkill`, `SIGINT`, `SIGKILL`, cleanup は `runtime/container/`
- `Xvfb`, `DISPLAY`, headless 実行は `runtime/container/`
- ログ出力先管理や process 監視は `runtime/container/`
- AWSIM Labs の起動コマンドは `targets/awsim/backend.py`
- Autoware の起動コマンドは `targets/awsim/backend.py`
- Runtime Monitor の起動コマンドは `targets/awsim/backend.py`
- scenario 実行コマンド生成は `targets/awsim/backend.py`
- AWSIM の JSON 出力命名規約は `targets/awsim/backend.py`

## 5. 共通データ契約

### 5.1 基本方針

`contracts/*` は「最初から全 target / verifier / 統計用途を完全に表現する」ことを目指さない。代わりに、

- どのケースでも必ず必要な最小共通核だけを固定する
- 将来増える target 固有値や verifier 固有値は拡張用の箱へ入れる

という方針を採る。

この方針により、

- target 追加のたびに contracts を壊しにくい
- 何も固定しない辞書の投げ合いも避けられる
- target 固有情報や verifier 固有情報を自然に追加できる

### 5.2 型方針

- `status` は内部では `Enum` として扱う
- 保存時や JSON 化時は文字列へ変換する
- `input`, `output`, `evidence`, `meta`, `options`, `diagnostics`, `assumptions` は拡張可能な辞書として扱う

### 5.3 `RunStatus` の標準語彙

`status` の標準語彙は当面以下で固定する。

- `success`
- `timeout`
- `execution_error`
- `analysis_error`
- `invalid`

`invalid` と `analysis_error` の境界は次の通りとする。

- 入力は読めたが、意味のある評価対象になっていないなら `invalid`
- 解析処理そのものが失敗したなら `analysis_error`

### 5.4 `EvaluationRecord.meta` の最小共通キー

`EvaluationRecord.meta` は拡張用の補助情報箱として扱う。ただし、完全な自由辞書にはせず、最小限の共通キーを持たせる。

共通必須:

- `schema_version`
- `source_module`

推奨:

- `created_at`

その他のキーは target / verifier / runtime / consumer ごとに追加してよい。必要なキーは、その値を使う consumer 側で要求を定義する。

理由:

- `schema_version` は保存・再読込・互換判定の基準になる
- `source_module` は生成元追跡と障害調査に使える
- `created_at` は有用だが、全経路の成立条件ではない

### 5.5 `evidence` のキー規約

`RawRunResult.evidence` と `EvaluationRecord.evidence` のキーは当面以下を標準語彙とする。

- `trace_json`
- `raw_result_json`
- `video`
- `log`

方針:

- AWSIM は主に `trace_json`
- BBSL は主に `raw_result_json`
- 共通ログは `log`
- 動画がある場合のみ `video`

内部では絶対パスを基本とし、外部保存時に必要なら `path_root` 基準で相対化してよい。

`path_root` の扱いは次の通りとする。

- 実行中の `EvaluationRecord.evidence` は絶対パスを基本とする
- JSONL や共有成果物へ保存するときは `path_root` 基準で相対化してよい
- 相対化した場合でも `meta["path_root"]` は残す

このルールにより、分散実行や container 実行で基準ディレクトリがずれても、生成物追跡を保ちやすくする。

必要なら生成物の path だけでなく、実行コマンドも `meta` に記録してよい。

推奨キー:

- `legacy_command`
- `normalized_command`
- `working_directory`
- `host_or_container`
- `container_name`

### 5.6 `TestCase`

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
- `meta`

### 5.7 `RawRunResult`

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

### 5.8 `EvaluationRecord`

- 入力元
  `targets/*/result_interpreter.py`
- 渡し先
  `evaluation/*`, `runtime/repository/*`, `targets/*/verification_input.py`

想定フィールド:

- `case_id`
- `target`
- `case_kind`
- `status`
- `input`
- `output`
- `evidence`
- `meta`

運用ルール:

- `input`
  実行前に与える値を入れる
- `output`
  測定値、判定値、ラベル、スコアなど実行後に得た値を入れる
- `evidence`
  JSON, CSV, 画像, raw result などの path/ID を入れる
- `meta`
  追跡、補助設定、運用上の文脈、互換管理を入れる

短い具体例:

```python
EvaluationRecord(
    case_id="loop_42",
    target="awsim",
    case_kind="uturn",
    status=RunStatus.SUCCESS,
    input={
        "dx0": 15.0,
        "ego_speed": 35.0,
        "npc_speed": 12.0,
    },
    output={
        "min_ttc": 1.23,
        "c_collision": 0,
    },
    evidence={
        "trace_json": "/path/to/uturn_eval_sim42.json",
    },
    meta={
        "schema_version": 1,
        "source_module": "targets.awsim.result_interpreter",
        "created_at": "2026-08-17T12:00:00+09:00",
        "verifier_name": "maude",
        "task_reason": "boundary_explore",
    },
)
```

### 5.9 `StatisticalRequest` / `StatisticalReport`

- `StatisticalRequest`
  `method`, `metric`, `bounds`, `confidence`, `target_width`, `options`
- `StatisticalReport`
  `method`, `metric`, `sample_count`, `estimate`, `interval`, `sufficient`, `next_action`, `diagnostics`

統計系契約は、実行系契約より少し固めに扱う。

### 5.10 `VerificationInput` / `FT4DResult`

- `VerificationInput`
  `tree_mode`, `universal_dataset`, `events`, `assumptions`, `meta`
- `FT4DResult`
  `tree_mode`, `top_sigma_pe`, `confidence`, `node_summaries`, `raw_result`

`VerificationInput` は現時点では FT4D 用の最小核として扱う。

## 6. target / verifier / evaluation の拡張ルール

### 6.1 新しい target を足すとき

最低限、次を追加する。

- `targets/<name>/backend.py`
- `targets/<name>/result_interpreter.py`
- `targets/<name>/verification_input.py`
- `targets/<name>/dataset_adapter.py`

必要なら target 固有の runner や event builder を追加してよい。

### 6.2 AWSIM の新しい case_kind を足すとき

最低限、次を追加または更新する。

- `targets/awsim/case_kinds/<case_kind>.py`
- `targets/awsim/scenario_builders/<case_kind>_builder.py`
- `targets/awsim/scenario_runner.py`

ルール:

- 定義データは `case_kinds/*`
- 組み立て処理は `scenario_builders/*`
- 振り分けは `scenario_runner.py`

`scenario_runner.py` に個別ロジックを溜め込まない。

### 6.3 新しい verifier を足すとき

最低限、次を追加する。

- `verifiers/<name>/backend.py`
- `verifiers/<name>/evaluator.py`

互換用途なら `verifiers/compatibility/` へ隔離する。

### 6.4 新しい評価法を足すとき

最低限、次を追加する。

- `evaluation/<method>.py`

探索文脈や stop/continue 判定に強く依存するものは、すぐに `evaluation/` へ入れず `orchestration/` に残してよい。

### 6.5 target 固有情報をどこへ置くか

方針:

- 実行前の値なら `TestCase.input`
- 実行後の測定値や判定値なら `EvaluationRecord.output`
- 生データや成果物参照なら `evidence`
- 補助情報、追跡情報、互換情報なら `meta`

target 固有情報は最初から共通契約へ昇格させない。複数 target / 複数 consumer で繰り返し使うと確認できた時点で、共通項目への昇格を検討する。

### 6.6 BBSL backend の標準入力契約

`targets/bbsl/backend.py` は、BBSL target の実行入口を 1 か所に固定する。

経路は 2 つを基本とする。

- fixture 再利用
  `TestCase.input["fixture_path"]` または `["raw_result_json"]` を受け取り、既存の raw result JSON を `RawRunResult.evidence["raw_result_json"]` へ載せる
- 実験実行
  `TestCase.input` に実行パラメータを受け取り、`targets/bbsl/runner.py` を呼んで raw result JSON を生成する

実験実行で最初に受ける標準キーは以下に固定する。

- `target_repo`
- `mini`
- `max_images`
- `tree` または `tree_mode`
- `sigma_pf_source`
- `sigma_pb_mode`
- `and_rule`
- `detect_timeout`
- `reuse_existing_output`

判断ルール:

- すでに出力済み JSON を再利用したいときは `reuse_existing_output=True`
- 実際に BBSL 実験を走らせたいときは `target_repo` を渡して通常実行する
- `fixture_path` または `raw_result_json` があるときは fixture 経路を優先する
- どちらの情報もない空入力はエラーにする

## 7. 2026-08-24 時点の実装状況

### 7.1 主要な移行済み領域

- 共通契約
  `contracts/execution.py`, `contracts/evaluation.py`, `contracts/statistics.py`, `contracts/verification.py`
- v2 入口
  `run_orchestrator_v2.py`, `run_worker_v2.py`, `apps/cli/*`
- orchestration 本体
  `orchestration/orchestrator.py`, `worker_loop.py`, `strategy.py`, `dkw_mode.py`, `binomial_mode.py`, `final_report.py`
- evaluation 本体
  `evaluation/gp_boundary.py`, `dkw.py`, `binomial_ci.py`, `ft4d_service.py`
- AWSIM target 本体
  `targets/awsim/backend.py`, `result_interpreter.py`, `verification_input.py`, `kinematics_bridge.py`, `case_kinds/uturn.py`
- BBSL target 本体
  `targets/bbsl/backend.py`, `result_interpreter.py`, `verification_input.py`, `runner.py`, `event_builder.py`, `ft4d_bridge.py`, `ft4d_analysis.py`
- repository 系
  `runtime/repository/dataset_csv.py`, `dataset_restore.py`, `shared_store.py`, `parameter_buffer.py`, `local_history.py`

### 7.2 実 run 確認済みまたは準完了まで移行済みの運用モード

- `boundary_gap`
  `2026-08-20` に `t05` 完了。v2 の real run と summary CLI 通過を確認済み
- `verify_consistency`
  `2026-08-24` に受入完了。real run で execution / 保存契約を確認し、consumer 側の CSV / DKW summary / summary CLI は targeted unit test で確認した
- `dkw`
  `2026-08-24` に seeded smoke で受入完了。resume dataset だけで `SMC Verification Complete` に到達し、history / samples CSV と stop 記録を確認した
- `jama_edge` / `ttc_edge` / `worst_ttc`
  v2 で運用し始められる準完了モード

### 7.3 移行済みの主要機能

- explore / margin / focus の主制御は概ね `orchestration/strategy.py` へ集約済み
- DKW / Binomial CI の実行フローは `orchestration/dkw_mode.py` / `orchestration/binomial_mode.py` へ集約済み
- FT4D confidence gap 集計 helper は `targets/bbsl/ft4d_analysis.py` へ分離済み
- BBSL confidence gap 実行入口は `apps/cli/bbsl_confidence_gap_main.py` へ分離済み
- plot 系 CLI の正本は `tools/plot/*` 側へ寄せ済み
- worker の refresh 継続は、inline 経路では `Orchestrator` 再実行、external / host / cluster 経路では `run_worker_v2.py --restart-on-refresh` で扱う形に整理済み

### 7.4 目標 tree と現状差分

- `docs/architecture.md` は目標 tree にはあるが、現時点では未作成
- `runtime/cluster/cluster_config.py` は目標 tree にはあるが、現時点では未実装
- `third_party/AW_Kinematics_Extractor/` は目標 tree 上の最終位置であり、現時点の実体は root 直下の `AW_Kinematics_Extractor/` にある
- `runtime/container/infra_tasks.py` は現状の補助実装として存在するが、目標 tree では `profile.py` / `launcher.py` 側へ整理対象とみなす
- `runtime/repository/strategy_dataset.py` は現状の補助実装として存在するが、目標 tree では repository の補助層として整理対象とみなす
- `evaluation/statistical_service.py` は現状の補助実装として存在するが、目標 tree では `evaluation/dkw.py` と `evaluation/binomial_ci.py` の正本性を優先する

### 7.5 legacy 互換入口の位置づけと削除条件

- `run_external_verifier.py`, `run_ft4d_smoke.py`, `run_bbsl_local_ft4d.py` は移行期の root wrapper として残してよい
- root 直下の `visualize_*.py`, `analyze_boundary_gap.py`, `analyze_ttc_consistency.py`, `extract_region_data.py` は互換入口として残してよい
- `external_verifiers/*` は旧 request/response 形の互換入口、`verifiers/compatibility/*` は新経路へ束ねる実体として扱う
- legacy 互換入口は、新経路で一部代替できた時点では削除しない
- 削除条件は、新経路への完全移植、README / 運用手順の更新、最低 1 本の v2 受入実 run が揃った時点とする

### 7.6 まだ未完または整理中の領域

- `docs/architecture.md` は未作成
- README / 運用手順への v2 反映はまだ残っている
- Binomial CI と BBSL の一部は実 run での CSV 更新確認が未実施
- 旧 CLI の表示文言をどこまで互換維持するかは未調整
- `verify_consistency` の `--max-samples` と実 stop 条件の整合は、受入後の改善候補として残る
- BBSL confidence gap の viewer 導線整理は継続中

### 7.7 2026-08-18 以降の追加確認

- `P2 AWSIM artifact 契約` は `2026-08-18` に `t03`, `t04` まで実 run で確認済み
- `t04` の受入 run は `/home/passd/verification_runs/20260818/t04_awsim_timeout_recovery_retry2` に保存している
- `t04` の timeout 側は `targets.awsim.case_kinds.uturn_timeout_smoke` を使い、`TIMEOUT_SEC=5` を強制する形で固定した
- 上記 run では `records.jsonl` に `timeout -> success` の 2 row、`interpreted_timeout.json`、`interpreted_recovery.json`、`uturn_eval_sim3_footage.mp4` がそろうことを確認した
- この確認の過程で、`run_worker_v2.py` が CLI 明示の `--config-module` を既定値へ戻してしまう不具合を修正済みとする
- `P4/P6 boundary_gap` は `2026-08-20` に `t05` を `/home/passd/verification_runs/20260820/t05_boundary_gap` で完了した
- `t05` では `records.jsonl` 30 row (`success=29`, `execution_error=1`)、`uturn_dataset.csv`、`uturn_boundary_gap_progress.csv`、`summary.log` を確認した
- `t05` の summary は `candidate_cells=0` で完走し、候補セルが 0 件のときも `uturn_boundary_gap_cells.csv` を空 CSV として残す挙動に統一した
- `P4/P6 verify_consistency` は `2026-08-24` に、real run と targeted unit test を組み合わせる形で受入完了とした
- real run 側は `/home/passd/verification_runs/20260824/t06_verify_consistency` に保存し、`records.jsonl` 29 row がすべて `success`、各 row に `meta.schema_version`, `meta.source_module`, `meta.created_at` が入ることを確認した
- real run は `[CONSISTENCY] Exact Point 1/10` から `Exact Point 3/10 (Repeat 9/10)` まで進み、抽出点の反復実行と v2 の保存経路が成立することを確認した
- consumer 側は `python3 -m pytest -q tests/unit/orchestration/test_strategy.py -k verify_consistency_classifies_and_saves_results`, `python3 -m pytest -q tests/unit/runtime/test_consistency_dkw_summary.py`, `python3 -m pytest -q tests/unit/tools/test_consistency_summary.py`, `python3 -m pytest -q tests/unit/apps/test_consistency_summary_main.py` がすべて通り、`uturn_consistent_risk.csv`, `uturn_stochastic_risk.csv`, `uturn_consistency_dkw_summary.csv`, summary CLI の成立を別証拠で確認した
- 現行実装では `--max-samples 40` が `verify_consistency` の stop 条件に直接は使われず、既定値 `WORST_TTC_CASES=10` と `FOCUS_EXACT_REPEATS=10` により 100 run 想定で進む差分が残るが、これは受入阻害ではなく改善候補として扱う
- `P4 dkw` は `2026-08-24` に `/home/passd/verification_runs/20260824/t07_dkw_smoke_seeded_v2` で seeded smoke 受入完了とした
- seeded resume dataset は `/home/passd/verification_runs/20260824/t07_dkw_seeded_snapshot/uturn_dataset.csv` を使い、enqueue 0 件のまま `SMC Verification Complete` に到達することを確認した
- `uturn_dkw_history.csv` は 1 row、`uturn_dkw_samples.csv` は生成済みで、history の最終値は `ess=171`, `estimate=0.02`, `lower_bound=0.0043`, `upper_bound=0.02`, `interval_width=0.0157`, `target_epsilon=0.15` だった
- seeded stop 時に `uturn_dkw_history.csv` が二重追記される不具合を `orchestration/orchestrator.py` で修正し、`python3 -m pytest -q tests/unit/orchestration/test_orchestrator.py` と `python3 -m pytest -q tests/unit/orchestration/test_dkw_mode.py` が通ることを確認した

### 7.8 `viewer / plot / summary` 方針

用語は次の意味で固定する。

- `summary`
  CSV / JSON / 標準出力で、人が mode の結果を確認できる要約
- `plot`
  dataset や summary を入力に、グラフや画像を生成する可視化
- `viewer`
  運用者が最初に叩く確認入口。実体は `summary` CLI でも `plot` CLI でもよい

方針:

- mode 本体は `orchestration/*` / `targets/*` / `runtime/repository/*` に残す
- 人向け確認のうち表・CSV・文章は `tools/analysis/*` に寄せる
- 人向け確認のうち図は `tools/plot/*` に寄せる
- root 直下の `visualize_*.py` は互換入口として残し、正本は `tools/plot/*` に固定する

具体運用:

- `boundary_gap` は、まず `tools/analysis/analyze_boundary_gap.py` で summary を見る
- `verify_consistency` は、人手確認の正本を `tools/analysis/*` 側へ寄せ、`apps/cli/consistency_summary_main.py` は orchestrator 直結用途として扱う
- BBSL confidence gap は、実行入口を `apps/cli/bbsl_confidence_gap_main.py` に置き、閲覧用 summary は `tools/analysis/*` 側で整理する
- 汎用 plot の正本は `tools/plot/*` に置き、root 直下 wrapper は互換入口としてだけ残す

## 8. 未完項目と次の優先順

### 8.1 現時点の主要残課題

1. v2 正本の必須証明テストのうち、残る統計 mode (`binomial_ci`) と BBSL 系の実 run を埋める
2. Binomial CI の実 run で、bounds 算出と棄却サンプリングのログ・CSV 挙動を確認
3. `boundary_gap` / `verify_consistency` / edge 系 / BBSL confidence gap の README / 運用手順反映
4. v2 CLI の最終表示文言と summary 導線をどこまで整えるか整理
5. BBSL confidence gap の実 dataset / 実 output を使った summary 妥当性確認
6. `verify_consistency` の `--max-samples` と実 stop 条件の整合は、受入後の改善候補として別管理する

### 8.2 機能単位の受入確認プラン

最終判断は、旧フレームとの一致ではなく、
v2 正本が現在の目的を安定して達成できるかで行う。
この章の後半に残している比較メモは移行時の記録として扱い、
受入の判定は v2 実 run の結果を正本にする。

受入結果の状態ラベルは当面次の 4 つで固定する。

- `達成`
  v2 正本で、その機能の目的と主要観測物が成立している
- `意図差分`
  旧設計との差はあるが、v2 の設計として残してよいと判断済み
- `差分あり`
  目的未達または観測差分があり、まだ設計判断または修正が必要
- `未確認`
  受入手順または実 run がまだ終わっていない

各機能単位では、少なくとも次の観点を同じ順で見る。

1. 入口
   CLI 引数、config、環境変数、queue 入力、`TestCase` / `RawRunResult` / `EvaluationRecord` の受け渡し
2. 正常系
   success 時の `records.jsonl`、dataset CSV、統計 CSV、summary 出力
3. 異常系
   `timeout`, `execution_error`, `analysis_error`, `invalid` の扱い
4. 制御
   resume、repeat、refresh、queue refill、stop 伝播、history 重複スキップ
5. 保存副作用
   `meta`, `evidence`, `path_root`, command 記録、artifact rename、processed history の残り方
6. 表示
   CLI の最終表示、warning、summary 文言

受入確認は次の 4 段階で進める。

1. v2 正本の入口と保存物を確定する
2. no-sim / fixture / unit test で既知差分を先に潰す
3. 代表 smoke を 1 本ずつ通し、生成物が成立することを確認する
4. 必須命題に対応する実 run を行い、最終的な CSV / JSONL / summary を証拠として残す

#### 8.2.1 受入で証明したい命題

ここでの「証明」は、旧実装との完全同値ではなく、
v2 正本が必要な成果物と制御を単独で成立させることを示す意味で使う。

- `P1 共通保存契約`
  1 件のケース実行で、`EvaluationRecord`, dataset CSV, processed history が崩れず残る
- `P2 AWSIM artifact 契約`
  success / timeout / timeout 後 recovery の各経路で、trace、optional artifact、interpreter 出力が成立する
- `P3 refresh / cluster 制御`
  external worker を含む queue 実行が refresh をまたいでも継続し、orchestrator が完走できる
- `P4 統計 mode 保存契約`
  `boundary_gap`, `verify_consistency`, `dkw`, `binomial_ci` が必要 CSV と summary 入力を残す
- `P5 BBSL / FT4D 実行契約`
  single-run と underconfident batch-run が result JSON と停止理由を残して完走する
- `P6 consumer 導線`
  summary / analysis CLI が v2 の生成物をそのまま読める

#### 8.2.2 命題ごとの最小証明セット

- `P1`: `t01`
- `P2`: `t03`, `t04`
- `P3`: `t02`
- `P4`: `t05`, `t06`, `t07`, `t08`
- `P5`: `t09`, `t10`
- `P6`: `t05`, `t06`, `t10`

この対応により、`t01` から `t10` までが通れば、
「worker / orchestrator / target / evaluation / summary / BBSL」の主要経路を
v2 正本だけで一通り証明できる構成になる。

#### 8.2.3 各命題の合格条件

- `P1`
  `records.jsonl` の各 row に `case_id`, `target`, `case_kind`, `status`, `input`, `output`, `evidence`, `meta` があり、`meta.schema_version`, `meta.source_module` が全 row で成立する。`--path-root` を渡した run では `meta.path_root` も残る
- `P1`
  `processed_loops_history.csv` が残り、実際に処理した loop 番号と矛盾しない
- `P2`
  success では `*_eval_simN.json` と、存在する optional artifact が昇格される
- `P2`
  timeout では `TIMEOUT` marker が残り、その直後の次ケース成功で trace 汚染がない
- `P3`
  refresh を挟んでも orchestrator 1 回の起動で所定件数まで完走し、`run.log` で refresh 継続が追える
- `P4`
  mode ごとの主 CSV が生成され、最終行に空ではない bound / confidence / stop 情報が残る
- `P5`
  BBSL / FT4D の result JSON に `tree_mode`, `confidence`, `stop_reason`, `source_batch_jsons` など主要キーが残る
- `P6`
  summary / analysis CLI が追加加工なしで v2 出力を読み、summary CSV または summary log を生成できる

### 8.3 比較単位と対応表

| 比較単位 | 新経路の正本 | 旧参照実装 | まず比較する観測物 |
| --- | --- | --- | --- |
| worker 実行系 | `run_worker_v2.py`, `apps/cli/worker_main.py`, `orchestration/worker_loop.py` | `run_manager.py` | `records.jsonl`, timeout / error / success 保存、history 重複スキップ、refresh |
| orchestrator / strategy 系 | `run_orchestrator_v2.py`, `apps/cli/orchestrator_main.py`, `orchestration/orchestrator.py`, `orchestration/strategy.py` | `master_orchestrator.py`, `strategist.py` | queue refill、resume、mode 別 target 選択、stop 理由、完了条件 |
| AWSIM target 実行系 | `targets/awsim/backend.py`, `runtime/container/*`, `targets/awsim/result_interpreter.py`, `targets/awsim/verification_input.py` | `run_scenario.py`, `awchecker.py`, `run_manager.py` | scenario 起動、timeout marker、JSON rename、`EvaluationRecord` 内容、`VerificationInput` 変換 |
| evaluation / 保存系 | `orchestration/dkw_mode.py`, `orchestration/binomial_mode.py`, `runtime/repository/*`, `runtime/cluster/result_sink.py` | `strategist.py`, `dataset_repo.py`, `awchecker.py` | dataset CSV、history CSV、`dkw_samples.csv`、`binomial_ci_samples.csv`、shared store 縮退 |
| BBSL / FT4D 系 | `targets/bbsl/*`, `evaluation/ft4d_service.py`, `apps/cli/bbsl_local_ft4d_main.py`, `apps/cli/bbsl_confidence_gap_main.py` | `estimator.py`, `run_bbsl_local_ft4d.py` | raw result 解釈、`VerificationInput`、FT4D 入出力、confidence gap summary |

比較表は 1 行 1 機能で埋めるのではなく、まず上の 5 単位ごとに「主要観測物」を揃える。
そのうえで必要なら単位の中を mode 別に分割する。

#### 8.3.1 worker 実行系の初回比較結果

`2026-08-17` 時点の初回比較では、worker 実行系は
「queue 実行、status / completion 通知、JSONL / dataset CSV 保存、history 重複スキップ、refresh 要求と継続」
までは no-sim と worker smoke で概ね確認できている。

一方で、AWSIM infra の実再起動と artifact rename の完全一致確認は
worker 単体ではなく `AWSIM target 実行系` の比較対象として残す。

| 観点 | 旧 `run_manager.py` | 新 worker 実行系 | 判定 | メモ |
| --- | --- | --- | --- | --- |
| 入口 | 設定読込と Ray 接続を `run_manager.py` が一体で持つ | `run_worker_v2.py` は薄い wrapper、`apps/cli/worker_main.py` が引数正規化・queue/sink 構築を担当 | `意図差分` | 責務分離として妥当。`build_task_source()` と `run_worker_with_summary()` に集約 |
| queue 取得 / stop 信号 | `get_next_task()` を直接呼び、`system_command == "stop"` をその場で判定 | `TaskQueueGateway.fetch_next()` が stop を `StopIteration` へ正規化し、`WorkerLoop` が停止理由付きで終了 | `一致` | stop の意味は維持しつつ境界だけ分離 |
| worker status / completion 通知 | queue actor へ `update_worker_status`, `report_completion` を直接送る | `WorkerLoop` と `TaskQueueGateway` 経由で同等の通知を行う | `一致` | `waiting -> running -> timeout/success` と completion 通知は no-sim で確認済み |
| success / timeout 保存 | timeout marker と shared store 更新を `run_manager.py` が直接処理 | `EvaluationRecord` を JSONL 主保存し、必要なら dataset CSV / shared store へ反映 | `意図差分` | 保存境界を `result_sink.py` へ寄せた。保存結果は no-sim で確認済み |
| shared store 障害時の縮退 | shared store 不在時は警告するが、保存経路が worker 内に強く結合 | JSONL を主保存に固定し、shared store / dataset CSV 側は `OptionalResultSink` で警告付き継続 | `意図差分` | 新設計の改善点。worker 全体を落とさない |
| refresh | `REFRESH_INTERVAL` 到達時に worker 自身が infra を kill して継続 | inline は `Orchestrator` が worker を再実行し、external / host / cluster は `run_worker_v2.py --restart-on-refresh` が次 batch を継続する | `意図差分` | worker 本体から infra 寿命管理を外した結果。worker 実行系としての refresh 継続は確認済み。AWSIM infra 実再起動は `AWSIM target 実行系` で比較する |
| history 重複スキップ | 旧 `run_manager.py` 単体の責務としては明確でない | `LocalHistory` と `WorkerLoop.prepare_test_case()` で重複 loop を skip する | `意図差分` | v2 側の追加安全策。`skipped_duplicate` 通知は確認済み |
| backend / interpreter 例外処理 | 旧 worker では process / file 監視中心で、統一 record 化は弱い | `execution_error` / `analysis_error` を `EvaluationRecord` として保存 | `意図差分` | 共通契約導入による改善。unit test で確認済み |
| `--headless` 受理 | 旧 worker が Xvfb を直接起動 | 新 worker は headless 指定を backend 構築へ forward する | `一致` | worker 実行系としては forwarding を維持。実際の Xvfb 起動は AWSIM target 実行系で比較する |
| 旧 CLI 互換 | `--type`, `--mode`, `--focus_points`, `--dkw_*` 前提 | v2 でも互換引数を受けつつ、fixture / direct param を追加 | `一致` | 既存運用互換は維持。fixture / direct param は拡張 |

この比較から、worker 実行系の残差は次の 2 点に絞られる。

1. 旧入口と v2 入口で、代表 queue 実行 1 本の生成物を実比較する
2. `run_manager.py` が握っていた artifact rename / timeout marker / cleanup の実機一致を、worker 単体ではなく AWSIM target 実行系で確認する

#### 8.3.2 worker 実行系の論点整理と追加確認結果

この節では、worker 実行系で論点になっていた項目を
コード比較と追加確認の観点で整理する。
ここでの目的は、
「どこまでコード比較で言い切れるか」と
「どこから先が実行比較待ちか」を切り分けることにある。

| 項目 | 旧コード | 新コード | コード比較で言えること | まだ実行確認が必要な点 |
| --- | --- | --- | --- | --- |
| 旧 `run_manager.py` と v2 worker の代表 queue 実行比較 | `run_manager.py` が `get_next_task`、status 更新、`run_scenario.py` 実行、JSON 監視、completion 報告まで 1 つの監視ループで持つ | `TaskQueueGateway` が queue payload を `TestCase` へ正規化し、`WorkerLoop` が `TestCase -> RawRunResult -> EvaluationRecord -> save` を調停する | queue 取得、stop 信号、status / completion 通知という制御骨格は概ね対応している | `records.jsonl` / dataset CSV / shared store の最終生成物が旧経路と同じ粒度・同じ値になるかは、実行して生成物を見ないと確定できない |
| refresh 後の再起動シーケンス | `REFRESH_INTERVAL` 到達時に `run_manager.py` 自身が `kill_infra()` を呼び、同じ worker プロセスの外側ループで infra を再起動する | inline worker では `WorkerLoop` が `refresh_requested` を返し、`Orchestrator` が worker を再実行する。external / host / cluster worker では `run_worker_v2.py --restart-on-refresh` が同一 worker プロセス内で次 batch を継続する | 新経路の refresh は「worker 本体が infra を直接 kill する」設計ではなく、`refresh_requested` を境に worker 実行単位を切り直す設計である。worker 実行系としての継続保証は unit / smoke で確認済み | AWSIM infra の実再起動タイミングと cleanup 一致は、worker 実行系ではなく `AWSIM target 実行系` の実行比較で確認する |
| artifact rename / timeout marker / cleanup の実機一致 | `run_manager.py` が local JSON を global 名へ rename し、mp4 / meta も rename し、timeout では `TIMEOUT` を書き、cleanup も直接呼ぶ | `ArtifactWatcher`, `ContainerSupervisor`, `AWSIMBackend`, `ContainerProcessManager`, `ContainerCleanup` に責務分離されている | trace JSON rename、mp4 / meta rename、timeout marker 書き込み、case scoped / resident の cleanup 分離はコード上ほぼ 1 対 1 に対応している | 実際のファイル出力順、rename タイミング、残存プロセス掃除、optional artifact の取りこぼしがないかは、実ファイル・実プロセスで見ないと確定できない |

コード比較と追加確認まで含めると、上の 3 項目は次のように分けられる。

- 代表 queue 実行比較
  制御フローは概ね対応、保存生成物の一致は実行待ち
- refresh 再起動
  worker 実行系としては確認完了。残るのは AWSIM infra 実再起動の target 側比較
- artifact rename / timeout marker / cleanup
  責務対応はかなり明確、実機一致だけが実行待ち

refresh 項目について補足する。

- 旧 worker は 1 プロセスの中で `kill_infra()` を呼び、そのまま次の outer loop で再起動する
- 新 worker は inline 経路では `refresh_requested` を summary として返し、`Orchestrator` が次の worker 実行を始める
- 新 worker は external / host / cluster 経路では `--restart-on-refresh` により `refresh_requested` を同一 worker プロセス内で吸収し、次 batch を開始する
- したがって、新経路の refresh は「同じ worker の中の refresh」ではなく、「worker 実行単位の切り直し」に近い
- この差分自体は設計変更として理解できる
- external worker 経路で、その切り直しをどこで保証するかという論点は、`run_worker_v2.py` 側の継続ループ追加で確認完了とする
- 以後の残論点は、worker 実行系ではなく `AWSIM target 実行系` の infra 実再起動と cleanup 一致に移る

#### 8.3.3 orchestrator / strategy 系の初回比較結果

`2026-08-17` 時点の初回比較では、orchestrator / strategy 系は
「local / cluster 入口、resume、queue refill、水位制御、host worker 起動停止、
mode 別 strategist 呼び出し、final report 受け渡し」
までは no-sim と unit test で概ね確認できている。

この比較中に 1 点、旧 `master_orchestrator.py` との差分が見つかった。
v2 `Orchestrator` は一時的に `dkw` / `verify_consistency` でも `REPEAT_COUNT` 停止へ入る形になっていたが、
旧実装ではこの 2 mode は strategist 側の `stop` を正本にしていたため、比較に合わせて修正した。

| 観点 | 旧 `master_orchestrator.py` / `strategist.py` | 新 orchestrator / strategy 系 | 判定 | メモ |
| --- | --- | --- | --- | --- |
| 入口と責務分離 | `master_orchestrator.py` が config 読込、cluster 起動、Ray 接続、actor 作成、queue refill、progress 表示まで一体で持つ | `apps/cli/orchestrator_main.py`, `apps/cli/orchestrator_cluster_main.py`, `Orchestrator`, `ActorRuntime`, `ClusterManager` に責務分離 | `意図差分` | 一体型ループを分割した。cluster bootstrap と local 入口は unit で確認済み |
| resume と loop 番号復元 | `DatasetRepository.restore_base_dataset()` と `get_last_loop_num()`、`dkw_fixed` 時だけ current dataset 優先 | `ResumeService.prepare()` が base/current を復元し、`resume_current_only` または `run_mode == "dkw_fixed"` で current 優先 | `一致` | `dkw_fixed` 特例も含めて意味を維持 |
| queue refill と水位制御 | `MAX_QUEUE_SIZE = worker_count * 4`, `REFILL_THRESHOLD = worker_count * 2` で strategist に補充要求 | `_resolve_queue_high_water()` / `_resolve_queue_low_water()` の既定値が同じで、override も可能 | `一致` | external worker 経路の queue refill は unit で確認済み |
| stop 条件 | 通常 mode は `REPEAT_COUNT` 到達で stop。`dkw` / `verify_consistency` / `binomial_ci` は strategist 側の stop を正本にする | `target_total` と `Strategist Stop` を併用。`dkw` / `verify_consistency` は比較に合わせて `target_total=None` へ修正済み | `一致` | `binomial_ci` / `dkw_fixed` は mode runner 側または `max_samples` で止まる |
| host worker の起動停止 | `with_host_worker` 時に orchestrator が host worker を起動し、finally で停止 | `Orchestrator.run()` が `HostWorkerManager` を start/stop し、cluster 入口は config をそこで組み立てる | `一致` | lifecycle は維持 |
| strategy mode の振り分け | `strategist.py` が 1 ファイル内で focus / edge / boundary_gap / verify_consistency / dkw / binomial を抱える | `ActiveLearningStrategist` は正本だが、`dkw_mode.py` と `binomial_mode.py` へ mode 固有ロジックを分離し、`boundary_gap` / `verify_consistency` は repository へ保存責務を移した | `意図差分` | stop reason と payload 形式は概ね維持しつつ責務だけ分割 |
| final report と表示 | strategist が直接コンソールへ最終レポートを印字する | strategy が `latest_final_report` を保持し、`Orchestrator` summary に載せる。legacy 形式の整形は `final_report.py` で維持 | `意図差分` | 表示経路は変えたが、最終レポート文面の整形ルール自体は test で維持 |

この比較から、orchestrator / strategy 系の残差は次の 3 点に絞られる。

1. real Ray / detached actor / host worker を使った代表 cluster 実行 1 本で、queue actor と shared store actor の実接続を比較する
2. 旧 CLI の progress 表示と v2 summary / final report の見え方を、運用上どこまで合わせるか整理する
3. `boundary_gap_progress.csv`, consistency 分類 CSV, `dkw_samples.csv`, `binomial_ci_samples.csv` の生成物一致を、実 run で比較する

#### 8.3.4 orchestrator / strategy 系の補足

補足として、今回の比較で確認できた設計上の着地点を整理する。

- local 入口と cluster 入口は 1 つにまとめず、CLI で分ける
- cluster 固有の責務は `orchestrator_cluster_main.py`, `ActorRuntime`, `ClusterManager` 側へ寄せる
- stop 判断は `Orchestrator` と strategy の二層で持つが、統計 mode の終了条件は mode runner / strategist 側を正本にする
- final report は「その場で print する値」ではなく、summary に載せて後段でも再利用できる値として持つ
- progress 表示や最終文言の完全互換は、機能一致確認とは切り分けて後段で調整する

#### 8.3.5 AWSIM target 実行系の初回比較結果

`2026-08-17` 時点のコード比較と no-sim / unit test では、AWSIM target 実行系は
「scenario 起動、trace rename、timeout marker、result interpreter、`VerificationInput` 変換」
までは概ね確認できている。

今回の比較では 1 点、旧 `run_manager.py` と新 `targets/awsim/backend.py` の差分が見つかった。
旧実装は successful case のたびに `kill_client()` だけを呼び、
AWSIM Labs / Autoware / Runtime Monitor は refresh または timeout まで残していた。
新実装は一時的に successful case ごとに non-resident infra まで止めていたため、
reuse mode の寿命管理が崩れていた。これは比較結果に合わせて修正済みとする。

| 観点 | 旧 `run_manager.py` / `awchecker.py` | 新 AWSIM target 実行系 | 判定 | メモ |
| --- | --- | --- | --- | --- |
| scenario 起動コマンド | `python3 run_scenario.py --type <case_kind> ...` を worker 内で組み立てる | `AWSIMBackend._build_command()` が同等のコマンドを組み立てる | `一致` | `ext_mode` など backend 制御用の予約キーは scenario へ渡さない |
| trace JSON 命名規約 | `*_test_simN.json` を待ち、成功後に `*_eval_simGLOBAL.json` へ rename | `ArtifactWatcher` と `ContainerSupervisor` が local trace を待ち、`AWSIMBackend` が eval 名へ昇格させる | `一致` | local / global loop 番号の使い分けも維持 |
| video / meta artifact rename | `*_footage.mp4`, `*_footage.meta.json` も eval 名へ rename | `AWSIMBackend._promote_related_artifacts()` が同等の rename を行う | `一致` | optional artifact があれば一緒に昇格させる |
| timeout marker | timeout 時に `*_eval_simGLOBAL.json` へ `TIMEOUT` を書く | `ContainerSupervisor.wait_for_completion()` が `TIMEOUT` marker を生成する | `一致` | backend はその path を `RawRunResult.evidence["trace_json"]` に返す |
| successful case 後の cleanup | success では `kill_client()` のみ。infra は refresh / timeout まで再利用 | reuse mode の success では `stop_case_client()` のみ。refresh / timeout で non-resident infra を掃除 | `一致` | `2026-08-17` に修正。以前は success ごとに case scoped infra まで止めていた |
| refresh / timeout 時の infra 再起動前 cleanup | `kill_infra()` で AWSIM Labs / Autoware / Runtime Monitor を止め、外側ループで再起動 | `refresh_non_resident_infra()` が non-resident infra を止め、次 run で再起動する | `一致` | resident process は残す設計 |
| AW Checker の位置づけ | resident sidecar として worker 側で起動される | 現在の正本は `result_interpreter.py` を使うため、registry 既定では `include_awchecker=False` | `意図差分` | 結果解釈責務を resident sidecar から明示的 interpreter 段へ移した |
| `EvaluationRecord` 生成 | `awchecker.py` が Safety Evaluator の出力をその場で解釈する | `targets/awsim/result_interpreter.py` が trace JSON / `TIMEOUT` を解釈し、共通契約の `EvaluationRecord` を返す | `意図差分` | `schema_version`, `source_module`, `analysis_pipeline` などを共通形式で保持 |
| `VerificationInput` 変換 | worker / checker 側の中間ロジックに散っている | `targets/awsim/verification_input.py` が successful record から `VerificationInput` を構築する | `意図差分` | event 定義、required meta、除外 status の扱いを target 側で明示化 |

この比較から、AWSIM target 実行系は
「コード比較と no-sim / unit test での確認」はいったん完了とする。

一方で、次の 3 点はまだ実 run 比較が必要である。

1. 実 AWSIM Labs / Autoware / Runtime Monitor を起動したとき、trace / mp4 / meta の生成順と rename 完了順が旧経路と実機で一致するか
2. external / host / cluster worker 経路で refresh が入ったとき、target backend の infra refresh と worker 再実行の境界が実運用で安定しているか
3. 実 trace JSON を使った `result_interpreter.py` の出力と、旧 `awchecker.py` 経由の評価結果が代表 case で一致するか

#### 8.3.6 evaluation / 保存系の初回比較結果

`2026-08-17` 時点のコード比較と unit test では、evaluation / 保存系は
「`EvaluationRecord` の JSONL 主保存、dataset CSV / shared store への縮約保存、
`dkw_history.csv` / `binomial_ci_history.csv`、`dkw_samples.csv` / `binomial_ci_samples.csv`、
consistency / boundary_gap 系 CSV、resume 用 dataset 読込」
までは概ね確認できている。

保存境界の整理は、旧 `awchecker.py` / `dataset_repo.py` / `strategist.py` に散っていた直書きを、
新経路で次のように分離したと理解してよい。

- `JsonlResultSink`: `EvaluationRecord` をそのまま append する主保存
- `SharedStoreResultSink` / `RaySharedStoreResultSink`: dataset CSV または shared store actor へ縮約保存する従保存
- `StrategyDatasetRepository` / `restore_base_dataset()`: base/current dataset の読込と resume 復元
- `StatisticalHistoryRepository`: DKW / Binomial CI の収束履歴 CSV
- `StatisticalSamplesRepository`: stop 時点の filtered sample CSV
- `ConsistencyClassificationRepository`, `ConsistencyDkwSummaryRepository`, `BoundaryGapProgressRepository`: mode 固有の派生 CSV

| 観点 | 旧 `awchecker.py` / `dataset_repo.py` / `strategist.py` | 新 evaluation / 保存系 | 判定 | メモ |
| --- | --- | --- | --- | --- |
| 主保存 | 旧経路には `EvaluationRecord` の共通保存はなく、dataset CSV が事実上の正本 | `JsonlResultSink` が `records.jsonl` を主保存にし、`validate_evaluation_meta()` を通して append する | `意図差分` | v2 では JSONL を正本に固定し、CSV は派生物とみなす |
| dataset CSV 追記 | `awchecker.py` が `parsed_row` を直接 CSV へ append し、shared store 不在時も同じ列集合で保存 | `SharedStoreResultSink` が `EvaluationRecord` から input/output/evidence/meta を縮約し、`DatasetCsvRepository` へ append する | `一致` | timeout 時の `[ERROR: TIMEOUT]` reason 付与、header 拡張時の再書込も維持 |
| shared store 縮退 | detached actor が不在なら local CSV へ直接書く | JSONL を必ず書いたうえで、shared store / dataset CSV は `OptionalResultSink` として警告付き縮退 | `意図差分` | 従保存が落ちても worker 本体は継続する |
| resume 用 dataset 復元 | `DatasetRepository.restore_base_dataset()` が `<scenario>_dataset.csv` を `<scenario>_dataset_base.csv` へ復元 | `restore_base_dataset()` と `StrategyDatasetRepository` が同じ命名規約で base/current を扱う | `一致` | `dkw_fixed` の current 優先は orchestrator 側で保持 |
| DKW / Binomial 履歴 CSV | `strategist.py` が `*_dkw_history.csv`, `*_binomial_ci_history.csv` を直接 append | `StatisticalHistoryRepository` が ordered field で append する | `一致` | legacy と同じ既定 path を使う |
| DKW / Binomial sample CSV | stop 時に `strategist.py` が `filtered_df` を直接保存 | `DKWModeRunner` / `BinomialModeRunner` が `StatisticalSamplesRepository` 経由で保存 | `一致` | stop 時だけ保存する責務を mode runner へ分離 |
| consistency / boundary_gap 派生 CSV | `strategist.py` が分類 CSV と progress CSV を直接保存 | repository 経由で保存し、strategy 本体は生成判断だけを持つ | `意図差分` | 出力物の種類と既定 path は維持 |
| saved dataset からの loop 同期 | `strategist.py` は通常は dataset 全体、`dkw_fixed + pure_smc` では `reason` に `SMC` を含む行だけを同期対象にする | `ActiveLearningStrategist._sync_dispatched_task_count()` を legacy 比較に合わせて修正済み | `一致` | `2026-08-17` 修正。以前は SMC 以外の row まで数えていた |
| 壊れた `loop_num` への耐性 | `dataset_repo.py` は数値化できない `loop_num` を無視する | `ActiveLearningStrategist` の初期復元と task count 同期でも NaN を安全に無視するよう修正済み | `一致` | `2026-08-17` 修正。以前は一部経路で未初期化の恐れがあった |

この比較から、evaluation / 保存系は
「保存責務の配置」と「unit test での列生成・既定 path・縮退挙動」は確認完了とする。

一方で、次の 3 点はまだ実 run 比較が必要である。

1. 同じ代表 run を旧経路と新経路で 1 本ずつ流し、`records.jsonl`, `<scenario>_dataset.csv`, `processed_loops_history.csv` の最終内容を比較すること
2. detached shared store actor を実際に使った cluster run で、local CSV 縮退なしの merge 結果が旧運用と一致するかを確認すること
3. `dkw_samples.csv`, `binomial_ci_samples.csv`, `consistency_dkw_summary.csv`, `boundary_gap_progress.csv` の実生成物を mode ごとに 1 本ずつ比較すること

#### 8.3.7 BBSL / FT4D 系の初回比較結果

`2026-08-17` 時点のコード比較、fixture 比較、unit test、smoke test では、
BBSL / FT4D 系は
「raw result の取得、`EvaluationRecord` 化、`VerificationInput` 化、
FT4D 実行、batch 集約、confidence gap summary、旧 CLI / verifier 互換入口」
までは概ね確認できている。

この系統では、旧 `estimator.py` と `run_bbsl_local_ft4d.py` に集まっていた責務を、
新経路で次のように分離したと理解してよい。

- `targets/bbsl/backend.py`: raw output JSON の取得
- `targets/bbsl/result_interpreter.py`: raw output から `EvaluationRecord` への変換
- `targets/bbsl/verification_input.py`: `EvaluationRecord` から `VerificationInput` への変換
- `evaluation/ft4d_service.py`: FT4D 計算の共通実行
- `targets/bbsl/ft4d_bridge.py`: single-run / batch-run の橋渡し
- `targets/bbsl/batch_loop.py`, `targets/bbsl/underconfident_loop.py`: batch loop と confidence gap ベース制御
- `apps/cli/bbsl_local_ft4d_main.py`, `apps/cli/bbsl_confidence_gap_main.py`: 正本 CLI

一方で、旧名の入口は現時点でも残っているが、その多くは新経路への互換 wrapper になっている。

- `run_bbsl_local_ft4d.py`: `apps/cli/bbsl_local_ft4d_main.py` への薄い再公開
- `external_verifiers/bbsl_ft4d.py`: compatibility verifier の再公開
- `estimator.py`, `strategist.py`: BBSL / FT4D 系メソッドの多くが `targets/bbsl/*` へ委譲

| 観点 | 旧 `estimator.py` / `run_bbsl_local_ft4d.py` / `strategist.py` | 新 BBSL / FT4D 系 | 判定 | メモ |
| --- | --- | --- | --- | --- |
| 入口と責務分離 | 旧 `estimator.py` が FT4D tree 計算、batch 集約、resume、completion 判定まで広く持つ | `backend` / `result_interpreter` / `verification_input` / `ft4d_service` / `batch_loop` に分離 | `意図差分` | 分割後も legacy 入口は wrapper として残している |
| raw result 解釈 | BBSL JSON をその場で解釈し、失敗時の構造化が弱い | `ResultInterpreter` が success / invalid / analysis_error を `EvaluationRecord` に正規化する | `意図差分` | `schema_version`, `source_module`, `created_at`, `verifier_name` などを揃える |
| `VerificationInput` 変換 | 旧 `estimator.py` の FT4D 計算ロジック内で暗黙に組み立てる | `verification_input.py` が target 固有変換を担当し、required meta も検証する | `意図差分` | consumer 側の前提を明示化した |
| FT4D 実行 | `_calculate_ft4d_tree()` が tree 読込、event 設定、認識試験設定を一体で持つ | `FT4DService` が tree path 解決、`sigma_pf` / `sigma_pb` 決定、認識試験適用を共通化する | `一致` | fixture smoke と unit test で basic / combined の主要経路を確認済み |
| batch output 集約 | adapter / event builder が batch fixture を集約する | `targets/bbsl/event_builder.py` が同等の event set を返す | `一致` | batch fixture 比較 test で legacy builder と `events` 一致を確認済み |
| batch loop / resume | `estimator.py` が clean baseline 確保、resume、confidence 到達判定を持つ | `batch_loop.py` が clean baseline、resume state、停止理由を分離して実装する | `一致` | unit test で `confidence-satisfied` 停止と batch metadata を確認済み |
| underconfident policy | `strategist.py` が BBSL / FT4D confidence gap 実行を抱える | `underconfident_loop.py` が batch 実行を担当し、`strategist.py` は要約関数の提供へ寄せた | `意図差分` | 制御責務の位置を整理した。停止条件と summary 受け渡しは unit test で確認済み |
| `tree=all` の扱い | 旧 `estimator.py` 側で分岐しながら処理する | `ft4d_bridge.py` と compatibility verifier が `basic` / `combined` を明示的に 2 回実行する | `一致` | `local_runs` を明示するので出力の見通しはむしろ改善した |
| CLI / verifier 互換 | `run_bbsl_local_ft4d.py` と external verifier が実ロジックを多く持つ | 正本 CLI は `apps/cli/*`、旧入口は再公開または compatibility adapter とする | `一致` | CLI test、verifier test、legacy wrapper test で互換入口を確認済み |

この比較では、少なくとも次のテスト群が通っている。

- BBSL target 単体: `test_bbsl_backend.py`, `test_bbsl_result_interpreter.py`, `test_bbsl_verification_input.py`
- FT4D bridge / batch loop: `test_bbsl_ft4d_bridge.py`, `test_bbsl_batch_loop.py`, `test_bbsl_underconfident_loop.py`, `test_bbsl_ft4d_analysis.py`, `test_bbsl_event_builder.py`
- CLI / strategist / verifier 互換: `test_bbsl_local_ft4d_main.py`, `test_bbsl_confidence_gap_main.py`, `test_run_bbsl_local_ft4d.py`, `test_strategist_bbsl.py`, `test_bbsl_ft4d.py`, `test_legacy_bbsl_ft4d_adapter.py`
- smoke: `test_bbsl_ft4d_pipeline.py`

したがって、BBSL / FT4D 系は
「コード比較と fixture / unit / smoke による初回確認」は完了とする。

一方で、次の 3 点はまだ実 run 比較が必要である。

1. 実 BBSL repo に対して `execution_mode=legacy` と `execution_mode=batch-loop` を代表 1 本ずつ流し、出力 JSON、rendered tree、`stop_reason`、resume 後の batch 数が旧運用と一致するか確認すること
2. `condition_policy=underconfident` かつ `tree=combined` または `tree=all` の実 run で、confidence gap から次 batch 条件を選ぶ流れが運用意図どおりか確認すること
3. `reuse_existing_output`, clean baseline 再利用、noisy batch resume を含む既存 artifact 再利用が、実ディレクトリ状態でも旧運用どおり安定するか確認すること

### 8.4 実施順

優先順は次の通りとする。

1. `t01`
   まず共通保存契約を 1 件で固め、JSONL / dataset / history の観測方法を固定する
2. `t02`
   次に external worker を含む refresh 継続を確認し、制御系の大きな不安を先に消す
3. `t03`, `t04`
   `2026-08-18` に完了。AWSIM success / timeout / recovery を確認し、artifact と cleanup の実機証拠を残した
4. `t05`
   `2026-08-20` に完了。`boundary_gap` 30 件実 run と summary CLI、progress CSV、cells CSV を確認した
5. `t06`
   `2026-08-24` に完了。real run で execution / 保存契約を確認し、consumer 側の CSV / DKW summary / summary CLI は targeted unit test で確認した
6. `t07`
   `2026-08-24` に完了。seeded smoke で `SMC Verification Complete` と `uturn_dkw_history.csv` / `uturn_dkw_samples.csv` を確認し、初回 stop 時の history 二重追記も修正した
7. `t08`
   残る統計 mode は `binomial_ci` を流し、CSV と summary を採取する
8. `t09`, `t10`
   最後に BBSL / FT4D 系を流し、独立系統の result JSON と summary を確認する

### 8.5 比較結果の記録ルール

比較結果は少なくとも次を 1 か所に残す。

- 証明対象の命題 ID (`P1` など)
- 比較対象の機能単位
- 旧入口と新入口
- 比較に使った入力条件
- 比較した生成物
- 判定結果
- `意図差分` または `差分あり` の理由

差分が見つかったときは、まず次のどれかに分類する。

- 設計上残してよい差分
- 実装漏れ
- テスト不足
- 旧経路側の偶然の振る舞いで、互換維持不要なもの

### 8.6 完了条件の目安

- 必須命題 `P1` から `P6` までに、すべて `達成` が付く
- `t01` から `t10` までの必須保存物がそろい、欠番がない
- `t01`, `t03`, `t04`, `t05`, `t06` の `records.jsonl` 全 row で `meta.schema_version` と `meta.source_module` が成立する
- `t02` で refresh をまたぐ external worker 継続が確認済みである
- `t06` では real run の `records.jsonl` / dataset 証拠と、consistency classification / DKW summary / summary CLI の targeted test 証拠がそろっている
- `t07` では seeded smoke の `run.log`, `uturn_dkw_history.csv`, `uturn_dkw_samples.csv` がそろい、初回 stop 時の history 二重追記に対する回帰 test が通っている
- `worker 実行系`, `orchestrator / strategy 系`, `evaluation / 保存系` に `未確認` が残っていない
- 各主要 mode が少なくとも 1 本の `summary` 入口を持つ
- 汎用 plot CLI の本体が root 直下ではなく `tools/plot/*` にある
- README に「まず何を見るか」が mode ごとに書かれている
- 旧 root CLI を消さなくても、新しい正本の位置が一目で分かる

### 8.7 最小証明テスト実行表

次の共通変数を前提にしておくと、v2 受入結果を 1 か所へ集約しやすい。

```bash
RUN_ROOT=~/verification_runs/20260818
SCENARIO=uturn
CASE_KIND=uturn
CONFIG=targets.awsim.case_kinds.${CASE_KIND}
TIMEOUT_CONFIG=targets.awsim.case_kinds.uturn_timeout_smoke
SNAPSHOT_DIR=/home/passd/simulation_traces_shared_20260724_172135
BBSL_REPO=/home/passd/BBSL-test
RAY_ADDRESS=150.65.227.21:6379

# 代表 success case。uturn の実パラメータ名に合わせて固定する。
AWSIM_PARAMS_V2=(--param dx0=10.09 --param ego_speed=37.98 --param npc_speed=14.20)

# timeout 側は代表 success case と同じ動的パラメータを使い、
# TIMEOUT_CONFIG 側で TIMEOUT_SEC=5 を強制する。
AWSIM_TIMEOUT_PARAMS_V2=(--param dx0=10.09 --param ego_speed=37.98 --param npc_speed=14.20)
```

補足:

- 各テストは `mkdir -p "$RUN_ROOT/tNN_<name>"` を先に実行してから流す
- v2 側の `traces_dir` は `--dataset-csv` の親ディレクトリと一致する
- `t04` の timeout 再現は動的パラメータ差ではなく `TIMEOUT_CONFIG` による `TIMEOUT_SEC` 短縮で固定する
- BBSL の `execution-mode=legacy` は旧フレーム起動ではなく、v2 CLI 内の single-run 互換モードとして扱う

| ID | 証明対象 | テスト | 実行コマンド | 保存物 | 合格条件 |
| --- | --- | --- | --- | --- | --- |
| `t01` | `P1` | local queue 1 件で共通保存契約を確認 | `mkdir -p "$RUN_ROOT/t01_worker_contract"`<br>`python3 run_orchestrator_v2.py --output "$RUN_ROOT/t01_worker_contract/records.jsonl" --dataset-csv "$RUN_ROOT/t01_worker_contract/${SCENARIO}_dataset.csv" --history-path "$RUN_ROOT/t01_worker_contract/processed_loops_history.csv" --path-root "$RUN_ROOT/t01_worker_contract" --case-kind "$CASE_KIND" --mode explore --config-module "$CONFIG" --worker-count 1 --max-strategy-cases 1 --headless \| tee "$RUN_ROOT/t01_worker_contract/run.log"` | `records.jsonl`, `uturn_dataset.csv`, `processed_loops_history.csv`, `run.log` | `records.jsonl` 1 row に必須外側欄、`meta.schema_version`, `meta.source_module`, `meta.path_root` があり、dataset と history が 1 件分そろう |
| `t02` | `P3` | real cluster + refresh 継続を確認 | `mkdir -p "$RUN_ROOT/t02_cluster_refresh"`<br>`python3 run_orchestrator_cluster_v2.py --output "$RUN_ROOT/t02_cluster_refresh/records.jsonl" --dataset-csv "$RUN_ROOT/t02_cluster_refresh/${SCENARIO}_dataset.csv" --history-path "$RUN_ROOT/t02_cluster_refresh/processed_loops_history.csv" --path-root "$RUN_ROOT/t02_cluster_refresh" --case-kind "$CASE_KIND" --mode explore --config-module "$CONFIG" --worker-count 2 --with-host-worker --refresh-interval 1 --max-strategy-cases 3 --headless \| tee "$RUN_ROOT/t02_cluster_refresh/run.log"` | `records.jsonl`, `uturn_dataset.csv`, `processed_loops_history.csv`, `run.log` | `run.log` に refresh 継続が残り、orchestrator 1 回の起動で 3 件まで完走し、records / dataset / history の件数が矛盾しない |
| `t03` | `P2` | AWSIM success artifact / interpreter | `mkdir -p "$RUN_ROOT/t03_awsim_success/traces"`<br>`python3 run_worker_v2.py --output "$RUN_ROOT/t03_awsim_success/records.jsonl" --dataset-csv "$RUN_ROOT/t03_awsim_success/${SCENARIO}_dataset.csv" --path-root "$RUN_ROOT/t03_awsim_success" --target awsim --case-kind "$CASE_KIND" --mode explore --config-module "$CONFIG" --scenario-type "$SCENARIO" --simulation-output-dir "$RUN_ROOT/t03_awsim_success/traces" --expected-trace-path "$RUN_ROOT/t03_awsim_success/traces/${SCENARIO}_eval_sim1.json" --local-loop-num 1 --headless "${AWSIM_PARAMS_V2[@]}" \| tee "$RUN_ROOT/t03_awsim_success/run.log"`<br>`python3 run_result_interpreter.py --input "$RUN_ROOT/t03_awsim_success/traces/${SCENARIO}_eval_sim1.json" --target awsim --case-kind "$CASE_KIND" --config-module "$CONFIG" --output-json "$RUN_ROOT/t03_awsim_success/interpreted_record.json" \| tee "$RUN_ROOT/t03_awsim_success/interpreter.log"` | `records.jsonl`, `uturn_dataset.csv`, `traces/*eval_sim1.json`, `traces/*footage.mp4`, `traces/*footage.meta.json`, `interpreted_record.json`, `run.log`, `interpreter.log` | success row が保存され、trace と存在する optional artifact が昇格し、interpreter 出力にも共通 meta が入る |
| `t04` | `P2` | AWSIM timeout / cleanup / recovery | `mkdir -p "$RUN_ROOT/t04_awsim_timeout_recovery/traces"`<br>`python3 run_worker_v2.py --output "$RUN_ROOT/t04_awsim_timeout_recovery/records.jsonl" --dataset-csv "$RUN_ROOT/t04_awsim_timeout_recovery/${SCENARIO}_dataset.csv" --path-root "$RUN_ROOT/t04_awsim_timeout_recovery" --target awsim --case-kind "$CASE_KIND" --mode explore --config-module "$TIMEOUT_CONFIG" --scenario-type "$SCENARIO" --simulation-output-dir "$RUN_ROOT/t04_awsim_timeout_recovery/traces" --expected-trace-path "$RUN_ROOT/t04_awsim_timeout_recovery/traces/${SCENARIO}_eval_sim2.json" --local-loop-num 2 --headless "${AWSIM_TIMEOUT_PARAMS_V2[@]}" \| tee "$RUN_ROOT/t04_awsim_timeout_recovery/run.log"`<br>`python3 run_result_interpreter.py --input "$RUN_ROOT/t04_awsim_timeout_recovery/traces/${SCENARIO}_eval_sim2.json" --target awsim --case-kind "$CASE_KIND" --config-module "$TIMEOUT_CONFIG" --output-json "$RUN_ROOT/t04_awsim_timeout_recovery/interpreted_timeout.json" \| tee "$RUN_ROOT/t04_awsim_timeout_recovery/interpreter_timeout.log"`<br>`python3 run_worker_v2.py --output "$RUN_ROOT/t04_awsim_timeout_recovery/records.jsonl" --dataset-csv "$RUN_ROOT/t04_awsim_timeout_recovery/${SCENARIO}_dataset.csv" --path-root "$RUN_ROOT/t04_awsim_timeout_recovery" --target awsim --case-kind "$CASE_KIND" --mode explore --config-module "$CONFIG" --scenario-type "$SCENARIO" --simulation-output-dir "$RUN_ROOT/t04_awsim_timeout_recovery/traces" --expected-trace-path "$RUN_ROOT/t04_awsim_timeout_recovery/traces/${SCENARIO}_eval_sim3.json" --local-loop-num 3 --headless "${AWSIM_PARAMS_V2[@]}" \| tee -a "$RUN_ROOT/t04_awsim_timeout_recovery/run.log"`<br>`python3 run_result_interpreter.py --input "$RUN_ROOT/t04_awsim_timeout_recovery/traces/${SCENARIO}_eval_sim3.json" --target awsim --case-kind "$CASE_KIND" --config-module "$CONFIG" --output-json "$RUN_ROOT/t04_awsim_timeout_recovery/interpreted_recovery.json" \| tee "$RUN_ROOT/t04_awsim_timeout_recovery/interpreter_recovery.log"` | `records.jsonl`, `uturn_dataset.csv`, `traces/*eval_sim2.json`, `traces/*eval_sim3.json`, `interpreted_timeout.json`, `interpreted_recovery.json`, `run.log`, `interpreter_timeout.log`, `interpreter_recovery.log` | `eval_sim2.json` が `TIMEOUT` marker で、直後の `eval_sim3.json` が正常生成され、`records.jsonl` 2 row が timeout -> recovery の順で残る |
| `t05` | `P4`, `P6` | `boundary_gap` 複数 cycle 実 run (`2026-08-20` 完了) | `mkdir -p "$RUN_ROOT/t05_boundary_gap"`<br>`python3 run_orchestrator_v2.py --output "$RUN_ROOT/t05_boundary_gap/records.jsonl" --dataset-csv "$RUN_ROOT/t05_boundary_gap/${SCENARIO}_dataset.csv" --path-root "$RUN_ROOT/t05_boundary_gap" --case-kind "$CASE_KIND" --mode boundary_gap --config-module "$CONFIG" --resume-from "$SNAPSHOT_DIR" --worker-count 1 --headless --max-strategy-cases 30 \| tee "$RUN_ROOT/t05_boundary_gap/run.log"`<br>`python3 tools/analysis/analyze_boundary_gap.py "$RUN_ROOT/t05_boundary_gap" --type "$SCENARIO" --output "$RUN_ROOT/t05_boundary_gap/${SCENARIO}_boundary_gap_cells.csv" \| tee "$RUN_ROOT/t05_boundary_gap/summary.log"` | `records.jsonl`, `uturn_dataset.csv`, `uturn_boundary_gap_progress.csv`, `uturn_boundary_gap_cells.csv`, `run.log`, `summary.log` | progress CSV と cells CSV が生成され、summary CLI が追加加工なしで通る。`2026-08-20` の実施では `/home/passd/verification_runs/20260820/t05_boundary_gap` に 30 row を保存し、`candidate_cells=0` の空 cells CSV まで確認済み |
| `t06` | `P4`, `P6` | `verify_consistency` real run と consumer 側 targeted test の分離証明 (`2026-08-24` 完了) | `mkdir -p "$RUN_ROOT/t06_verify_consistency"`<br>`python3 run_orchestrator_v2.py --output "$RUN_ROOT/t06_verify_consistency/records.jsonl" --dataset-csv "$RUN_ROOT/t06_verify_consistency/${SCENARIO}_dataset.csv" --path-root "$RUN_ROOT/t06_verify_consistency" --case-kind "$CASE_KIND" --mode verify_consistency --config-module "$CONFIG" --resume-from "$SNAPSHOT_DIR" --worker-count 1 --headless --max-samples 40 \| tee "$RUN_ROOT/t06_verify_consistency/run.log"`<br>`python3 -m pytest -q tests/unit/orchestration/test_strategy.py -k verify_consistency_classifies_and_saves_results`<br>`python3 -m pytest -q tests/unit/runtime/test_consistency_dkw_summary.py`<br>`python3 -m pytest -q tests/unit/tools/test_consistency_summary.py`<br>`python3 -m pytest -q tests/unit/apps/test_consistency_summary_main.py` | `records.jsonl`, `uturn_dataset.csv`, `run.log` | `2026-08-24` の real run では `/home/passd/verification_runs/20260824/t06_verify_consistency` に `records.jsonl` 29 row (`success=29`) を保存し、`[CONSISTENCY] Exact Point 3/10 (Repeat 9/10)` まで進むことを確認した。各 row で `meta.schema_version` と `meta.source_module` も成立した。さらに targeted test で `uturn_consistent_risk.csv`, `uturn_stochastic_risk.csv`, `uturn_consistency_dkw_summary.csv`, summary CLI の成立を確認済みとし、`--max-samples 40` と stop 条件の差分は改善候補として切り離す |
| `t07` | `P4` | DKW seeded smoke (`2026-08-24` 完了) | `mkdir -p "$RUN_ROOT/t07_dkw_smoke"`<br>`python3 run_orchestrator_v2.py --output "$RUN_ROOT/t07_dkw_smoke/records.jsonl" --dataset-csv "$RUN_ROOT/t07_dkw_smoke/${SCENARIO}_dataset.csv" --path-root "$RUN_ROOT/t07_dkw_smoke" --case-kind "$CASE_KIND" --mode dkw --config-module "$CONFIG" --resume-from "$RUN_ROOT/t07_dkw_seeded_snapshot" --dkw-region emp_safe --worker-count 1 --headless --max-samples 60 \| tee "$RUN_ROOT/t07_dkw_smoke/run.log"` | `uturn_dkw_history.csv`, `uturn_dkw_samples.csv`, `run.log` | `2026-08-24` の実施では `/home/passd/verification_runs/20260824/t07_dkw_smoke_seeded_v2` を保存先に使い、enqueue 0 件のまま `SMC Verification Complete` に到達した。`uturn_dkw_history.csv` 1 row と `uturn_dkw_samples.csv` が生成され、history 最終行に `ess=171`, `estimate=0.02`, `lower_bound=0.0043`, `upper_bound=0.02`, `interval_width=0.0157`, `target_epsilon=0.15` が残ることを確認した。さらに初回 stop 時の history 二重追記を修正し、関連 unit test を通した |
| `t08` | `P4` | Binomial CI 実 run | `mkdir -p "$RUN_ROOT/t08_binomial"`<br>`python3 run_orchestrator_v2.py --output "$RUN_ROOT/t08_binomial/records.jsonl" --dataset-csv "$RUN_ROOT/t08_binomial/${SCENARIO}_dataset.csv" --path-root "$RUN_ROOT/t08_binomial" --case-kind "$CASE_KIND" --mode binomial_ci --config-module "$CONFIG" --resume-from "$SNAPSHOT_DIR" --dkw-region emp_safe --worker-count 1 --headless --max-samples 80 --binomial-target c_collision --binomial-target-width 0.05 \| tee "$RUN_ROOT/t08_binomial/run.log"` | `records.jsonl`, `uturn_dataset.csv`, `uturn_binomial_ci_history.csv`, `uturn_binomial_ci_samples.csv`, `run.log` | history / samples CSV が生成され、最終行に confidence 幅と停止理由が残る |
| `t09` | `P5` | BBSL / FT4D single-run v2 path | `mkdir -p "$RUN_ROOT/t09_bbsl_ft4d"`<br>`python3 run_bbsl_local_ft4d.py --target-repo "$BBSL_REPO" --execution-mode legacy --condition-policy all --run-mode resume --tree basic --sigma-pf-source dataset --sigma-pb-mode delta-clean --and-rule min --reuse-existing-output --output-json "$RUN_ROOT/t09_bbsl_ft4d/result.json" \| tee "$RUN_ROOT/t09_bbsl_ft4d/run.log"` | `result.json`, `run.log` | `result.json` に `tree_mode`, `confidence`, `stop_reason`, `source_batch_jsons` が入る |
| `t10` | `P5`, `P6` | BBSL confidence gap `underconfident + tree=all` | `mkdir -p "$RUN_ROOT/t10_bbsl_conf_gap"`<br>`python3 apps/cli/bbsl_confidence_gap_main.py --target-repo "$BBSL_REPO" --execution-mode batch-loop --condition-policy underconfident --run-mode resume --tree all --sigma-pf-source dataset --sigma-pb-mode delta-clean --and-rule min --min-confidence 0.95 --output-json "$RUN_ROOT/t10_bbsl_conf_gap/result.json" \| tee "$RUN_ROOT/t10_bbsl_conf_gap/run.log"`<br>`python3 tools/analysis/bbsl_confidence_gap_summary.py "$RUN_ROOT/t10_bbsl_conf_gap/result.json" \| tee "$RUN_ROOT/t10_bbsl_conf_gap/summary.log"` | `result.json`, `run.log`, `summary.log` | confidence gap result と summary が生成され、batch-loop の停止理由が読める |

受入確認時に最低限そろえて見るもの:

- `records.jsonl` の row 数、status、`meta.source_module`、`meta.schema_version`、`meta.path_root`
- dataset CSV の row 数、`loop_num`、`reason`、主要指標列
- `processed_loops_history.csv` の件数と最終 loop 番号
- `run.log` の refresh / stop / summary 表示
- `*_dkw_history.csv`、`*_binomial_ci_history.csv`、`*_boundary_gap_progress.csv`、`*_consistency_dkw_summary.csv` の最終行
- BBSL / FT4D の result JSON に入る `tree_mode`, `top_sigma_pe`, `confidence`, `stop_reason`, `source_batch_jsons`

## 9. 旧文書の位置づけ

以前の長い棚卸し、移行途中メモ、file-by-file inventory、当時の判断経緯は [docs/refactor_design_legacy_20260817.md](/home/passd/AWSIM_launch/docs/refactor_design_legacy_20260817.md:1) に残す。この文書では、長く残したい設計原則と、2026-08-24 時点で有効な進捗要約だけを扱う。
