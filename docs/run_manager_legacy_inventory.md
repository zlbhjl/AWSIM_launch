# Run Manager Legacy Inventory

## 1. 目的

この文書は、旧 [`run_manager.py`](/home/passd/AWSIM_launch/run_manager.py:1) にまだ残っている責務を、

- `fallback 専用`
- `もう削除可能`

の 2 つに分けて整理するための現状メモである。

前提:

- `run_manager.py` の通常経路はすでに `run_v2_compat_worker()` 優先
- 旧 `ProcessManager` 系は `RUN_MANAGER_FORCE_LEGACY=1` または v2 失敗時の fallback のみで使う
- 判定基準は「2026-08-11 時点の実装で、通常経路から参照されるかどうか」

## 2. 現在の入口

### 2.1 まだ本線で使うもの

| 項目 | 役割 | 判定 |
| --- | --- | --- |
| `load_config()` | `--type` / `--mode` / `--focus_points` / `--headless` / `--ext_mode` を解釈してグローバル値を組み立てる | 残す |
| `SCENARIO_NAME`, `RUN_MODE`, `FOCUS_POINTS`, `HEADLESS_MODE`, `EXT_MODE` | `build_v2_compat_argv()` が v2 worker に渡す互換引数の元データ | 残す |
| `WORKER_NAME`, `OUTPUT_DIR` | v2 wrapper の `--worker-id` / `--path-root` / `--dataset-csv` の元データ | 残す |
| `build_v2_compat_argv()` | 旧 CLI / 環境変数を v2 worker 引数へ変換する薄い互換層 | 残す |
| `run_v2_compat_worker()` | 現在の通常経路 | 残す |
| `main()` | `v2 -> fallback` の最終分岐 | 残す |

### 2.2 fallback 専用の入口

| 項目 | 役割 | v2 側の置き換え先 | 判定 |
| --- | --- | --- | --- |
| `run_legacy_worker()` | 旧 worker を直接起動 | `run_v2_compat_worker()` | fallback 専用 |
| `ProcessManager` | 旧 queue worker 本体 | `apps/cli/worker_main.py` + `orchestration/worker_loop.py` + `targets/awsim/backend.py` + `runtime/container/*` | fallback 専用 |

## 3. fallback 専用で残っているもの

### 3.1 設定・定数群

| 項目 | 現在の役割 | v2 側の受け皿 |
| --- | --- | --- |
| `HOME`, `SETUP_BASH` | legacy プロセス起動時のパス解決 | `runtime/container/profile.py`, `runtime/container/process_manager.py` |
| `ROS_DOMAIN_ID`, `IS_MASTER`, `IS_HOST_MODE` | legacy runtime 差分の分岐 | `targets/registry.py`, `runtime/container/profile.py` |
| `TIMEOUT_SEC` | legacy 監視ループの timeout 秒数 | `targets/awsim/backend.py`, `runtime/container/supervisor.py` |
| `INTERVAL_SEC`, `REFRESH_INTERVAL` | legacy loop の待機・再起動ポリシー | `orchestration/worker_loop.py` 側へ今後寄せる候補 |
| `AWSIM_CMD`, `AUTOWARE_CMD`, `INFRA_TASKS`, `Task` | legacy infra task 定義 | `runtime/container/infra_tasks.py`, `runtime/container/profile.py` |

### 3.2 `ProcessManager` の責務

| legacy メソッド | 現在の役割 | v2 側の置き換え先 |
| --- | --- | --- |
| `__init__()` | Ray 接続、SharedStore 接続、Xvfb 起動 | `runtime/cluster/task_queue_gateway.py`, `runtime/repository/shared_store.py`, `runtime/container/xvfb.py` |
| `_build_command()` | `source setup.bash` 付き command 組立 | `runtime/container/process_manager.py` |
| `_start_process()` | infra process 起動とログ隔離 | `runtime/container/process_manager.py` |
| `_run_trigger_once()` | `run_scenario.py` の 1 回起動 | `runtime/container/process_manager.py`, `targets/awsim/backend.py` |
| `_send_signal()` | process group 単位で停止 | `runtime/container/cleanup.py` |
| `kill_all_processes()` | client / infra / resident / Xvfb の停止 | `runtime/container/process_manager.py`, `runtime/container/xvfb.py` |
| `kill_client()` | scenario client の停止 | `runtime/container/process_manager.py` |
| `_force_cleanup_os()` | `pkill`, `ros2 daemon stop`, `/dev/shm` 掃除 | `runtime/container/cleanup.py` |
| `kill_infra()` | infra 停止 + OS cleanup | `runtime/container/process_manager.py` |
| `execute()` | queue fetch, stop 検知, success/timeout 分岐, completion 報告 | `orchestration/worker_loop.py`, `runtime/cluster/task_queue_gateway.py`, `runtime/cluster/result_sink.py`, `targets/awsim/backend.py` |
| `cleanup_all()` | legacy worker 完全終了 | `runtime/container/process_manager.py` |

## 4. 2026-08-11 に削除済みのもの

通常経路にも fallback 経路にも影響せず、未使用だったため削除したもの。

| 項目 | 理由 |
| --- | --- |
| `get_last_processed_loop()` | このファイル内から参照されていなかった。dataset resume 役割は `runtime/repository/dataset_csv.py` と `runtime/repository/dataset_restore.py` 側へ移っている |
| `ProcessManager.count_target_files()` | このファイル内から参照されていなかった。旧ローカル連番ベースの補助で、現在の queue / global loop 運用では未使用 |
| `REPEAT_COUNT` | 代入だけで参照されていなかった |
| `csv` import | `get_last_processed_loop()` と一緒に不要になった |
| `glob` import | `count_target_files()` と一緒に不要になった |
| `FILE_PATTERN` | `count_target_files()` 専用だったため不要になった |

## 5. 削除順

壊しにくい順で並べると次の通り。

1. fallback を外せる段階で `run_legacy_worker()` と `ProcessManager` 一式を削除
2. 最後に `main()` の fallback 分岐を外し、`run_manager.py` 自体を v2 wrapper だけに縮退

## 6. 次に見るポイント

削除前に確認したいのは次の 2 点。

1. `run_v2_compat_worker()` 経路で実運用の queue timeout / success の dataset が十分に旧互換か
2. `REFRESH_INTERVAL` 相当の「一定回数ごとの infra 再起動」を v2 側で本当に必要とするか

この 2 点が固まれば、旧 `ProcessManager.execute()` に残す意味はかなり薄くなる。
