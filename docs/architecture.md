# アーキテクチャ

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
├── contracts/                     # target 間で共通のデータ型 (TestCase, RawRunResult, EvaluationRecord, StatisticalReport など)。
├── orchestration/                 # target に依存しない制御ロジック
│   ├── orchestrator.py            # 司令塔本体。戦略に従ってケースを発行し、停止条件を判定する。
│   ├── strategy.py                # 探索戦略 (explore / focus / boundary_gap など、GP 境界モデル)。
│   ├── binomial_mode.py / dkw_mode.py / sprt_mode.py / ebstop_mode.py # 統計モードごとの停止判定。
│   ├── replay.py                  # 既存 CSV のケースを再実行する replay モード。
│   ├── random_parameter_sampling.py / fixed_parameter_sampling.py # 入力サンプリング。
│   └── worker_loop.py             # ワーカー側のループ (取得 → 実行 → 解釈 → 保存 → 完了報告)。
├── evaluation/                    # 統計計算 (binomial_ci, dkw, sprt, ebstop, alpha_spending, gp_boundary) と FT4D サービス。
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
│   ├── prism/                     # PRISM: モデル実行、-simpath 解析、Maude 判定との統合
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
