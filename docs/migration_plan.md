# Run Manager Migration Plan

## 1. 目的

この文書は、旧 `run_manager.py` の責務を

- `apps/cli/worker_main.py`
- `orchestration/worker_loop.py`
- `targets/awsim/backend.py`
- `runtime/container/*`

へ安全に移すための実装順チェックリストをまとめる。

方針:

- 旧 `run_manager.py` は当面残す
- 直接大改造せず、v2 側へ責務を寄せる
- 先に worker を実運用可能にし、その後 orchestrator を寄せる
- 毎段階で smoke / regression を足して比較できる状態を保つ

## 2. 責務の置き場

### 2.1 `apps/cli/worker_main.py`

- CLI 引数の定義と検証
- `configs.<type>` の読込
- `focus` 時の `FOCUS_POINTS` 解決
- 実行モードの解釈
- target backend / result interpreter / sink / task source の組立
- `headless` を runtime profile へ変換
- worker 実行文脈の構成

### 2.2 `orchestration/worker_loop.py`

- queue から次タスクを取得
- worker status の更新
- stop signal の検知
- `TestCase -> RawRunResult -> EvaluationRecord -> save`
- success / timeout / analysis error 分岐
- completion 報告
- 定期リフレッシュ判断
- loop 全体の終了制御

### 2.3 `targets/awsim/backend.py`

- `run_scenario.py` コマンド生成
- scenario type / case kind に応じた AWSIM 実行内容決定
- timeout 秒数の最終決定
- 出力ディレクトリ決定
- trace JSON / 動画 / meta の命名規約
- 実行前の既存成果物削除
- 成功時の local id -> global id リネーム
- timeout marker 書込
- AWSIM / Autoware / Runtime Monitor の task 構成

### 2.4 `runtime/container/*`

- 共通 task 定義
- `bash -i -c` 起動
- setup.bash 読込付き command 組立
- ログ隔離
- resident / client / infra プロセス管理
- SIGINT / SIGKILL / process group kill
- `pkill`, `ros2 daemon stop`, `/dev/shm` cleanup
- Xvfb start / stop
- `DISPLAY`, `VK_ICD_FILENAMES` 注入
- artifact 生成待ち / timeout 監視

## 3. 実装順

### Phase 0: 比較基準を固定する

- [ ] `run_manager.py` の代表経路を 3 つ決める
  - queue 実行
  - direct simulation 実行
  - headless 実行
- [ ] 旧経路で比較対象にする主要成果物を固定する
  - status
  - `case_id`
  - `case_kind`
  - `c_collision`
  - `c_ttc_*`
  - `min_ttc`
  - evidence path
  - `loop_num`
- [ ] 既存 smoke テストの対象範囲を確認する
  - `tests/smoke/test_run_worker_v2_local.py`
  - `tests/smoke/test_run_orchestrator_v2_local.py`
  - `tests/smoke/test_orchestrator_param_pipeline.py`

完了条件:

- 何をもって「旧と同等」とみなすかが先に決まっている

### Phase 1: `worker_main.py` へ入口責務を寄せる

- [ ] `run_manager.py:load_config()` の責務を項目ごとに洗い出す
- [ ] 旧 CLI 引数と v2 CLI 引数の対応表を作る
- [ ] `--type` / `--mode` / `--ext_mode` の受け皿を v2 側で整理する
- [ ] `configs.<type>` の動的 import を v2 側へ寄せる
- [ ] `focus` 時の `FOCUS_POINTS` 解決を v2 側へ寄せる
- [ ] `ROS_DOMAIN_ID` / `EXEC_MODE` 由来の worker 文脈を v2 側 config に閉じ込める
- [ ] backend / interpreter / sink / task source の組立責務を `worker_main.py` に一本化する

完了条件:

- v2 worker の入口だけ見れば、何をどう起動するか追える

### Phase 2: `runtime/container/*` へ共通実行基盤を寄せる

- [ ] 旧 `Task` / `INFRA_TASKS` の構造を `runtime/container/profile.py` に集約する
- [ ] `source setup.bash` 付き command 組立を `launcher.py` へ寄せる
- [ ] `bash -i -c` 起動を `launcher.py` / `runner.py` へ寄せる
- [ ] AWSIM / Autoware / awchecker のログ隔離を共通化する
- [ ] resident / client / infra の寿命管理を `process_manager.py` に寄せる
- [ ] SIGINT / SIGKILL / process group kill を `cleanup.py` に寄せる
- [ ] OS レベルの残存プロセス掃除を `cleanup.py` に寄せる
- [ ] Xvfb 起動停止と環境変数注入を `xvfb.py` に寄せる
- [ ] artifact 生成待ち / timeout 監視を `supervisor.py` に寄せる

完了条件:

- 「プロセスをどう起動し、どう監視し、どう止めるか」が target 非依存の形で読める

### Phase 3: `targets/awsim/backend.py` へ AWSIM 固有責務を寄せる

- [ ] `run_scenario.py` 実行 command 生成を backend に閉じ込める
- [ ] `scenario_type` / `case_kind` の扱いを backend 内で統一する
- [ ] timeout 秒数の解決を backend へ寄せる
- [ ] `OUTPUT_DIR` 解決を backend / runtime profile 経由に統一する
- [ ] `*_test_simN.json` / `*_eval_simN.json` の命名規約を backend に閉じ込める
- [ ] 実行前の stale artifact 削除を backend に寄せる
- [ ] 成功時の trace / 動画 / meta リネームを backend に寄せる
- [ ] timeout marker 書込を backend に寄せる
- [ ] マスター機 / リモート機差分の待機時間を backend 側設定へ寄せる
- [ ] `awchecker.py` 前提の artifact 配置を backend で保証する

完了条件:

- `run_manager.py` を見なくても、AWSIM の 1 ケース実行と成果物命名が理解できる

### Phase 4: `worker_loop.py` へ loop 制御を寄せる

- [ ] queue からの task 取得を gateway 経由に統一する
- [ ] worker status 更新を loop 内へ統一する
- [ ] stop signal 検知を loop 側へ寄せる
- [ ] task から `reason`, `global_loop_num` を抜く責務を loop 側へ寄せる
- [ ] success / timeout / analysis error を `EvaluationRecord` ベースで扱う
- [ ] completion 報告を loop に集約する
- [ ] 定期リフレッシュ条件を loop の policy として持たせる
- [ ] `KeyboardInterrupt` / graceful stop の流れを loop と caller の境界で整理する

完了条件:

- worker の主ループを読むだけで、「待機 -> 実行 -> 保存 -> 報告」の流れが追える

### Phase 5: 保存系を v2 本線に合わせる

- [ ] `log_parameters(...)` 相当の役割を `result_sink` / `shared_store` へ寄せる
- [x] timeout 時の保存経路を `SharedStoreResultSink` と整合させる
- [ ] `loop_num` / `global_loop_num` / `local_loop_num` の扱いを文書化する
- [ ] dataset CSV の主要列が旧経路と比較可能であることを確認する
- [ ] evidence path の相対化 / 絶対化ルールを確認する

完了条件:

- 保存の正規経路が `EvaluationRecord -> sink -> repository` に揃う

2026-08-14 更新:

- `worker_main` の queue timeout 経路と `SharedStoreResultSink` の timeout merge は no-sim regression で確認済み。
- `shared_store_actor` / `dataset_csv` 側は optional sink として扱い、主保存先 JSONL を落とさない縮退を確認済み。

### Phase 6: worker 比較 smoke を通す

- [x] fixture 実行 smoke を通す
- [x] param 実行 smoke を通す
- [x] headless 実行 smoke を通す
- [x] queue 経由 smoke を追加または強化する
- [x] timeout ケースの smoke または regression を追加する
- [ ] 旧 `run_manager.py` と v2 worker の主要 output 差分を比較する

完了条件:

- 代表経路で v2 worker を旧経路の代替として扱える

2026-08-14 更新:

- `scripts/check_v2_no_sim.sh` で、fixture worker、local queue orchestrator、`worker_main` / `result_sink` / `awsim_backend` / `awsim_backend_infra` の no-sim regression をまとめて流せる。
- `scripts/check_v2_no_sim.sh` に、`tests/smoke/test_run_worker_v2_local.py::test_run_worker_v2_param_headless_subprocess_smoke` と `tests/smoke/test_orchestrator_param_pipeline.py` の param/headless smoke を組み込み済み。
- 実シミュレータを回す比較はまだここに含めない。
- `strategist.py` の周辺残差 3 本のうち、
  `_print_final_report` は `orchestration/final_report.py`、
  `worker_count -> CACHE_SIZE` は `orchestration/cache_policy.py`、
  BBSL confidence gap 入口は `apps/cli/bbsl_confidence_gap_main.py`
  へ受け先を作成済み。

注記:

- したがって Phase 6 の主な未完は
  `param 実行 smoke`, `headless 実行 smoke`,
  および旧入口との代表 output 差分確認である。

### Phase 7: orchestrator へ接続する前提を固める

- [ ] `worker_main.py` / `worker_loop.py` / `targets/awsim/backend.py` の責務境界を再確認する
- [ ] `run_orchestrator_v2.py` が呼ぶ worker 引数を確定する
- [ ] resume / queue / status / completion の I/O を文書化する
- [ ] 旧 `master_orchestrator.py` から必要な情報だけを抽出する準備をする

完了条件:

- worker 側が安定しており、次の作業が orchestrator 移行に限定される

## 4. この段階ではまだやらないこと

- `master_orchestrator.py` の直接大改造
- `run_manager.py` の即時削除
- BBSL と AWSIM の同時全面移行
- strategy / orchestrator / worker の一括同時置換
- README の全面更新を先行すること

## 5. 次の 1 タスク

最初の着手点としては、次の順を推奨する。

1. `run_manager.py:load_config()` の責務を `apps/cli/worker_main.py` と比較して差分を書く
2. `Task` / `INFRA_TASKS` / `AWSIM_CMD` / `AUTOWARE_CMD` を `runtime/container/profile.py` へ寄せる
3. `dynamic_cmd` 生成と trace 命名規約を `targets/awsim/backend.py` へ寄せる
4. queue / status / completion を `orchestration/worker_loop.py` に集める

この 4 つが揃うと、`run_manager.py` の主要責務はほぼ v2 へ移る。

## 6. 補助資料

- 旧 `run_manager.py` の棚卸しと `fallback 専用` / `削除可能` の分類:
  [`docs/run_manager_legacy_inventory.md`](/home/passd/AWSIM_launch/docs/run_manager_legacy_inventory.md:1)
