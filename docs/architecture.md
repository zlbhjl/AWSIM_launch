# アーキテクチャ

Architecture of the framework. **[English version below ↓](#english)**

## ファイル・ディレクトリ構成

現在の正本は v2 フレーム (`run_*_v2.py` → `apps/cli/` → `orchestration/` / `runtime/` / `targets/`) です。
root 直下の旧スクリプト (`master_orchestrator.py`、`run_manager.py` など) は互換用に残しているもので、
新しい機能は v2 側にだけ追加しています。

```text
AWSIM_launch/
├── run_orchestrator_cluster_v2.py # 【司令塔・クラスター】21号機で実行。Ray head・worker container の起動から停止判断まで行う正本の入口。
├── run_orchestrator_v2.py         # 【司令塔・ローカル】クラスターを使わず1プロセスで回す入口 (dynamics の統計実行など)。
├── run_worker_v2.py               # 【ワーカー】各 container 内で動き、Ray Client で司令塔のキューからケースを取って実行する。
├── run_dynamics_awsim_compare_v2.py # 同じ入力を ODE と AWSIM の両方で実行して比べる入口。
├── run_ft4d_smoke.py / run_prism_ft4d_smoke.py # FT4D の疎通確認 (AWSIM / PRISM)。
├── run_bbsl_local_ft4d.py         # BBSL raw result を AWSIM_launch 側で FT4D 再計算する入口。
├── run_external_verifier.py       # 外部検証器の legacy 互換入口。
├── apps/cli/                      # 各 run_*.py の本体 (引数解析と組み立て)。
│   ├── prism_importance_main.py    # PRISM固定P・固定Qの重点標本比較CLI。
│   ├── dynamics_importance_main.py # ODE入力の固定P・固定Q重点標本比較CLI。
│   └── awsim_importance_main.py    # AWSIM入力の固定P・固定Q重点標本CLI。
├── contracts/                     # target 間で共通のデータ型 (TestCase, RawRunResult, EvaluationRecord, StatisticalReport など)。
├── orchestration/                 # target に依存しない制御ロジック
│   ├── orchestrator.py            # 司令塔本体。戦略に従ってケースを発行し、停止条件を判定する。
│   ├── strategy.py                # 探索戦略 (explore / focus / boundary_gap など、GP 境界モデル)。
│   ├── binomial_mode.py / dkw_mode.py / sprt_mode.py / ebstop_mode.py # 統計モードごとの停止判定。
│   ├── replay.py                  # 既存 CSV のケースを再実行する replay モード。
│   ├── random_parameter_sampling.py / fixed_parameter_sampling.py # 入力サンプリング。
│   └── worker_loop.py             # ワーカー側のループ (取得 → 実行 → 解釈 → 保存 → 完了報告)。
├── evaluation/                    # 統計計算 (binomial_ci, dkw, sprt, ebstop, importance_sampling, alpha_spending, gp_boundary) と FT4D サービス。
├── runtime/
│   ├── cluster/                   # Ray 接続、TaskQueue / SharedStore actor、各号機の container 起動、worker watchdog、結果 sink。
│   ├── container/                 # container 内のプロセス管理
│   │   ├── profiles/              # container_profile 定義 (autoware171 / 180* / 190* / prism_maude など)。
│   │   ├── supervised_process/    # PID 1 の process supervisor。
│   │   └── launcher.py, supervisor.py, xvfb.py, gpu_health.py など
│   └── repository/                # dataset CSV、パラメータバッファ、SharedStore、履歴・再開用データ。
├── targets/                       # 検証対象ごとの実装
│   ├── registry.py                # --target から backend / result_interpreter を組み立てる。
│   ├── awsim/                     # AWSIM: case_kinds/, scenario_builders/, scenario_runner.py, backend.py, result_interpreter.py, theory_specs/
│   ├── prism/                     # PRISM: モデル実行、-simpath解析、Maude判定、固定Q重点サンプリング
│   ├── dynamics/                  # ODE/SDE: models/, runners/, calibrations/
│   └── bbsl/                      # BBSL raw result の解釈と FT4D 入力化
├── verifiers/                     # Maude 実行境界と判定 (verifiers/maude/)、BBSL legacy 互換 adapter。
├── verification_core/ft4d/        # AWSIM_launch 内に保持する共通 FT4D コア。
├── scenario_specs/                # dynamics 用の入力範囲定義 (uturn.py)。
├── configs/                       # 旧互換のシナリオ設定入口。実体は targets/awsim/case_kinds/ を再 export する。
├── redis_cluster/cluster_config.py # 号機の IP・ユーザー・container 名・ROS_DOMAIN_ID (v2 の cluster_manager もここを読む)。
├── docker/                        # 各 Autoware / PRISM image の Dockerfile、cyclonedds 設定、image ごとの README。
├── tools/
│   ├── analysis/                  # 集計・校正・検証用 CLI (boundary_gap, version 比較, dynamics 校正・照合, FT4D AND-rule 検証など)。
│   ├── maintenance/               # 運用補助 (clean_trash, filter_success_csv, reconcile_late_traces)。
│   └── plot/                      # 可視化の本体 CLI。root 直下の visualize_*.py はここを呼ぶ wrapper。
├── tests/                         # unit / smoke テスト。
└── (旧フレーム・互換用)
    ├── master_orchestrator.py, run_manager.py, local_worker.py, run_scenario.py
    ├── strategist.py, estimator.py, awchecker.py, param_logger.py, theoretical_calculator.py, point_extractors.py
    ├── redis_cluster/{cluster_manager,process_controller,task_queue,shared_store}.py
    ├── fix_dataset_labels.py, dataset_repo.py, archive_results.py, stop_containers.py, compare_ttc_modes.py
    └── visualize_*.py, extract_region_data.py, analyze_*.py # tools/ への薄い wrapper
```

## 分散実行アーキテクチャとデータフロー

現在のフレームワークは、21号機の司令塔と各号機の worker container を Ray で結んだ分散構成で動いています。
以下は v2 (`run_orchestrator_cluster_v2.py`) の流れです。

### 処理フローの概要

1.  **インフラ構築**: 司令塔 (`apps/cli/orchestrator_cluster_main.py`) が Ray head を起動し、
    `TaskQueueActor` (`runtime/cluster/ray_queue.py`) と `SharedStoreActor` (`runtime/repository/shared_store.py`) を detached actor として作ります。
    続いて `runtime/cluster/cluster_manager.py` が `redis_cluster/cluster_config.py` の各号機へ SSH し、
    コード同期・GPU preflight の後、`container_profile` に従って worker container を起動します。
2.  **タスク生成**: `orchestration/orchestrator.py` が、モードに応じた戦略 (`orchestration/strategy.py` の探索戦略、
    `binomial_mode.py` / `dkw_mode.py` / `sprt_mode.py` / `ebstop_mode.py`、`replay.py` など) から次のケースを決め、キューに積みます。
3.  **タスク実行**: 各 container では process supervisor が PID 1 で動き、その子 process の `run_worker_v2.py` が
    Ray Client (`ray://<master-ip>:10001`) でキューからケースを取ります。`orchestration/worker_loop.py` が
    `targets/registry.py` で選んだ target の backend (AWSIM なら `targets/awsim/backend.py`) を呼び、
    AWSIM・Autoware・Runtime Monitor・scenario を supervisor 経由で起動します。
4.  **結果解析**: trace JSON を target の `result_interpreter.py` が Maude checker と kinematics 抽出にかけ、
    共通形式の `EvaluationRecord` (`contracts/evaluation.py`) にします。
5.  **データ結合**: worker の `RaySharedStoreResultSink` (`runtime/cluster/result_sink.py`) が、実行前に入力パラメータを、
    実行後に結果を `SharedStoreActor` へ送ります。`SharedStore` は `loop_num` で両者を結合し、
    `--dataset-csv` (1行1標本) と `--output` (`EvaluationRecord` の JSONL) に追記します。
6.  **完了報告と次の判断**: worker が `TaskQueueActor` に完了を報告すると、司令塔は更新された dataset を読み直し、
    統計の停止条件の判定や次のケース生成を行います。停止条件を満たすと stop signal を出します。
    その後もキューに残ったケースが一定時間取られない場合は、放棄されたものとして取り消し、司令塔を終了します。

### 堅牢なエラー＆タイムアウト処理
分散システム特有の「フリーズ」や「通信エラー」から自己復旧し、統計や探索への悪影響を遮断する仕組みが備わっています。
- **タイムアウト**: trace JSON が scenario timeout と10秒の grace を過ぎても届かない場合は `status=timeout` とし、
  `SharedStore.flush_timeout` で入力パラメータと合わせて記録します (詳細は「AWSIM trace timeout と late JSON の扱い」)。
- **解析エラー**: JSON の破損・formula 欠落・車両が動いていない試行は `status=analysis_error` とし、判定値を `-1` にします。
- **ガベージコレクション**: container のクラッシュなどで結果が届かないパラメータは、
  `ParameterBuffer` から一定時間 (600秒) で自動破棄されます。
- **統計・探索への影響遮断**: 統計モードと探索戦略は `status=success` かつ判定値が有効な行だけを使うため、
  `timeout` / `execution_error` / `analysis_error` の行は標本に入りません。
- **worker の異常**: Ray 接続断は `ray_control_plane_lost`、GPU 異常は `gpu_unavailable` として worker を止め、
  司令塔側の watchdog が設定に応じて container を再作成します (「`container_profile` を切り替えるときの扱い」を参照)。

---

<a id="english"></a>

# English: Architecture

## Files and directories

The current framework is v2 (`run_*_v2.py` → `apps/cli/` → `orchestration/` / `runtime/` / `targets/`).
The legacy scripts at the repository root (`master_orchestrator.py`, `run_manager.py`, and others) are kept for compatibility;
new features are added only to v2.

```text
AWSIM_launch/
├── run_orchestrator_cluster_v2.py # [Orchestrator, cluster] Runs on the head node. Main entry point: starts the Ray head and worker containers and decides when to stop.
├── run_orchestrator_v2.py         # [Orchestrator, local] Runs in a single process without a cluster (for example, statistics on dynamics).
├── run_worker_v2.py               # [Worker] Runs in each container and takes cases from the orchestrator's queue over Ray Client.
├── run_dynamics_awsim_compare_v2.py # Runs the same input on both the ODE and AWSIM and compares them.
├── run_ft4d_smoke.py / run_prism_ft4d_smoke.py # FT4D smoke tests (AWSIM / PRISM).
├── run_bbsl_local_ft4d.py         # Recomputes FT4D from BBSL raw results in AWSIM_launch.
├── run_external_verifier.py       # Legacy-compatible entry point for external verifiers.
├── apps/cli/                      # The bodies of the run_*.py scripts (argument parsing and assembly).
│   ├── prism_importance_main.py    # CLI comparing PRISM importance samples with fixed P and fixed Q.
│   ├── dynamics_importance_main.py # CLI comparing ODE-input importance samples with fixed P and fixed Q.
│   └── awsim_importance_main.py    # CLI for AWSIM-input importance samples with fixed P and fixed Q.
├── contracts/                     # Data types shared by targets (TestCase, RawRunResult, EvaluationRecord, StatisticalReport, ...).
├── orchestration/                 # Target-independent control logic
│   ├── orchestrator.py            # The orchestrator. Issues cases according to the strategy and checks stopping conditions.
│   ├── strategy.py                # Exploration strategies (explore / focus / boundary_gap, ..., GP boundary model).
│   ├── binomial_mode.py / dkw_mode.py / sprt_mode.py / ebstop_mode.py # Stopping decisions per statistical mode.
│   ├── replay.py                  # Replay mode that re-runs the cases of an existing CSV.
│   ├── random_parameter_sampling.py / fixed_parameter_sampling.py # Input sampling.
│   └── worker_loop.py             # The worker loop (take → run → interpret → store → report completion).
├── evaluation/                    # Statistics (binomial_ci, dkw, sprt, ebstop, importance_sampling, alpha_spending, gp_boundary) and the FT4D service.
├── runtime/
│   ├── cluster/                   # Ray connection, TaskQueue / SharedStore actors, starting containers on each node, worker watchdog, result sink.
│   ├── container/                 # Process management inside containers
│   │   ├── profiles/              # container_profile definitions (autoware171 / 180* / 190* / prism_maude, ...).
│   │   ├── supervised_process/    # The process supervisor that runs as PID 1.
│   │   └── launcher.py, supervisor.py, xvfb.py, gpu_health.py, ...
│   └── repository/                # Dataset CSV, parameter buffer, SharedStore, history and resume data.
├── targets/                       # Implementation per target
│   ├── registry.py                # Builds the backend / result_interpreter from --target.
│   ├── awsim/                     # AWSIM: case_kinds/, scenario_builders/, scenario_runner.py, backend.py, result_interpreter.py, theory_specs/
│   ├── prism/                     # PRISM: model execution, -simpath parsing, Maude judgment, fixed-Q importance sampling
│   ├── dynamics/                  # ODE/SDE: models/, runners/, calibrations/
│   └── bbsl/                      # Interpreting BBSL raw results and converting them to FT4D input
├── verifiers/                     # Maude execution boundary and judgments (verifiers/maude/), legacy BBSL adapter.
├── verification_core/ft4d/        # The shared FT4D core kept in AWSIM_launch.
├── scenario_specs/                # Input ranges for dynamics (uturn.py).
├── configs/                       # Legacy entry points for scenario settings; they re-export targets/awsim/case_kinds/.
├── redis_cluster/cluster_config.py # Node IPs, users, container names, ROS_DOMAIN_ID (also read by the v2 cluster_manager).
├── docker/                        # Dockerfiles for the Autoware / PRISM images, cyclonedds settings, a README per image.
├── tools/
│   ├── analysis/                  # CLIs for aggregation, calibration, and validation (boundary_gap, version comparison, dynamics calibration and checks, FT4D AND-rule validation, ...).
│   ├── maintenance/               # Operations helpers (clean_trash, filter_success_csv, reconcile_late_traces).
│   └── plot/                      # Plotting CLIs. The visualize_*.py scripts at the root are wrappers around them.
├── tests/                         # Unit and smoke tests.
└── (legacy framework, kept for compatibility)
    ├── master_orchestrator.py, run_manager.py, local_worker.py, run_scenario.py
    ├── strategist.py, estimator.py, awchecker.py, param_logger.py, theoretical_calculator.py, point_extractors.py
    ├── redis_cluster/{cluster_manager,process_controller,task_queue,shared_store}.py
    ├── fix_dataset_labels.py, dataset_repo.py, archive_results.py, stop_containers.py, compare_ttc_modes.py
    └── visualize_*.py, extract_region_data.py, analyze_*.py # thin wrappers around tools/
```

## Distributed execution and data flow

The framework runs as a distributed system: an orchestrator on the head node and worker containers on each node, connected by Ray.
The following is the v2 flow (`run_orchestrator_cluster_v2.py`).

### Overview

1.  **Infrastructure setup**: the orchestrator (`apps/cli/orchestrator_cluster_main.py`) starts the Ray head and creates
    `TaskQueueActor` (`runtime/cluster/ray_queue.py`) and `SharedStoreActor` (`runtime/repository/shared_store.py`) as detached actors.
    Then `runtime/cluster/cluster_manager.py` connects to each node in `redis_cluster/cluster_config.py` over SSH, syncs the code,
    runs a GPU preflight check, and starts worker containers according to the `container_profile`.
2.  **Task generation**: `orchestration/orchestrator.py` decides the next case from the strategy for the mode (exploration strategies in
    `orchestration/strategy.py`, `binomial_mode.py` / `dkw_mode.py` / `sprt_mode.py` / `ebstop_mode.py`, `replay.py`, ...) and puts it
    on the queue.
3.  **Task execution**: in each container the process supervisor runs as PID 1, and its child process `run_worker_v2.py` takes cases
    from the queue over Ray Client (`ray://<master-ip>:10001`). `orchestration/worker_loop.py` calls the backend of the target chosen by
    `targets/registry.py` (for AWSIM, `targets/awsim/backend.py`), which starts AWSIM, Autoware, the Runtime Monitor, and the scenario
    through the supervisor.
4.  **Result analysis**: the target's `result_interpreter.py` runs the trace JSON through the Maude checker and kinematics extraction
    and produces an `EvaluationRecord` in the common format (`contracts/evaluation.py`).
5.  **Joining data**: the worker's `RaySharedStoreResultSink` (`runtime/cluster/result_sink.py`) sends the input parameters before the run
    and the result after it to `SharedStoreActor`. `SharedStore` joins them by `loop_num` and appends them to `--dataset-csv` (one row
    per sample) and `--output` (JSONL of `EvaluationRecord`).
6.  **Completion and the next decision**: when a worker reports completion to `TaskQueueActor`, the orchestrator re-reads the updated
    dataset, checks the statistical stopping condition, and generates the next case. When the stopping condition is met, it sends a stop
    signal. If cases remain on the queue untaken for a certain time after that, they are treated as abandoned and cancelled, and the
    orchestrator exits.

### Error and timeout handling

The system recovers from freezes and communication errors typical of distributed systems and keeps them from affecting statistics
and exploration.
- **Timeouts**: if the trace JSON has not arrived after the scenario timeout plus a 10-second grace period, the run is recorded as
  `status=timeout` together with its input parameters through `SharedStore.flush_timeout` (see "AWSIM trace timeout and late JSON" in
  [cluster_operations.md](cluster_operations.md)).
- **Analysis errors**: runs with a broken JSON, missing formulas, or vehicles that did not move are recorded as `status=analysis_error`
  with judgment values of `-1`.
- **Garbage collection**: parameters whose results never arrive (for example, because a container crashed) are discarded from
  `ParameterBuffer` after a fixed time (600 seconds).
- **Isolation from statistics and exploration**: statistical modes and exploration strategies only use rows with `status=success` and
  valid judgment values, so `timeout` / `execution_error` / `analysis_error` rows are not counted as samples.
- **Worker failures**: a lost Ray connection stops the worker as `ray_control_plane_lost` and a GPU failure as `gpu_unavailable`; the
  orchestrator's watchdog recreates the container according to the settings (see "Switching `container_profile`" in
  [cluster_operations.md](cluster_operations.md)).
