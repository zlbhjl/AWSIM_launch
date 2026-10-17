# AWSIM クラスターの運用

Operating an AWSIM cluster (container profiles, automatic recovery, timeouts, monitoring, plotting). **[English version below ↓](#english)**

複数台の GPU マシンで AWSIM / Autoware の worker container を動かし、v2 の司令塔
(`run_orchestrator_cluster_v2.py`) から実験する際の運用手順です。開発元の研究室では、
4台構成 (21〜24号機、司令塔は21号機) で運用しています。号機番号やパスは環境に合わせて読み替えてください。
ノードの IP・ユーザー・container 名は [redis_cluster/cluster_config.py](../redis_cluster/cluster_config.py) に設定します。

## `container_profile` を切り替えるときの扱い

- 現在サポートしている `container_profile` は `legacy`、`autoware171`、`autoware180`、`autoware180_ekfdiagfix`、`autoware190`、`autoware190_ekfdiagfix` です。
- `legacy` は旧来寄りのコンテナ起動条件で、`image=autoware_internal:2026`、`network_mode=host`、`privileged=true` を使います。
- `autoware171` は `1.7.1` 系の現在の運用条件で、`image=autoware_internal:2026-1.7.1-x11-verified-20260808`、`network_mode=bridge`、`privileged=false` を使います。
- `autoware180` は公式 `1.8.0` 比較用、`autoware180_ekfdiagfix` は AWSIM 運用向けの EKF 診断 overlay 版です。どちらも `autoware180_runtime/maps` と `autoware180_runtime/ml_models` を mount します。
- `autoware190` は公式 `1.9.0` image に AWSIM 互換層だけを重ねた比較用、`autoware190_ekfdiagfix` は 1.8.0 と同じ EKF 診断 overlay を当てた実験用です。network / privileged / user / mount は `autoware180_ekfdiagfix` と同一で、`autoware190_runtime/maps` と `autoware190_runtime/ml_models` を mount します。公式 1.9.0 image の既定 `CYCLONEDDS_URI` は multicast を許可する `/home/aw/cyclonedds.xml` なので、profile env で `/home/passd/cyclonedds.xml` を明示しています。詳細は [docker/README_autoware_1.9.0.md](../docker/README_autoware_1.9.0.md) を参照してください。
- `autoware180` / `autoware190` 系 image の `/docker-entrypoint.sh` は root 権限で初期化した後、`gosu passd` で実プロセスを起動します。そのため、この2 profileだけ Docker の開始userを`root`に固定します。`docker run --user passd`を指定するとentrypoint内のuser切替が`operation not permitted`で失敗します。非`privileged`運用で表示されるloopback multicastやsysctlのWARNは非致命であり、このuser切替エラーとは別です。
- `autoware180` 系workerのRay Client、checker、統計処理は、全号機で固定した`awsim_python_deps/py310`を`/opt/awsim_python_deps/py310`へread-only mountして使用します。image内のuser-local packageだけに依存するとentrypointの`gosu passd`後に`grpc`が見えず、workerが`ray_control_plane_lost`で終了するためです。
- `autoware171` / `autoware180*` では `cyclonedds.xml`、`AWSIMScriptPy`、`autoware_map`、`AW-Runtime-Monitor` などの mount を profile 側でまとめて管理しています。`docker run` を都度手で増減させるのではなく、まず profile 定義を直してください。
- CLI では `--container-profile` だけを指定した場合、`--scenario-profile` を省略すると同じ名前が自動で入ります。
- ただし現行の scenario 実装は新フレーム 1 系統なので、`scenario_profile=legacy` になっても scenario の中身は旧実装へ戻りません。変わるのは主にコンテナ起動条件です。
- つまり「コンテナだけ切り替えたい」場合でも、まず `container_profile` を基準に考えてください。scenario の違いより、`docker run` の network / privileged / env / mounts の違いのほうが実運用では重要です。
- `container_profile` は image / network / privileged / mount / Docker env を決める入口です。画面あり・headless の最終切替は `--headless` で行います。
- `--headless` を付けた場合、Xvfb の仮想画面を使い、Autoware launch には `rviz:=false` を自動で追加します。`DISPLAY=:99` だけでは RViz は止まりません。
- 画面ありで目視確認したい場合は `--headless` を付けず、X11 表示用の手動コマンドまたは画面ありコンテナ手順を使ってください。
- `bridge` profile で cluster worker を起動する場合、コンテナ内では Ray worker node を起動しません。`run_worker_v2.py` は Ray の外部 driver として、21号機の `TaskQueueActor` / `SharedStoreActor` に接続します。Docker bridge 内部IPやホスト側物理IPを、コンテナ内 Ray node として登録しないでください。
- `bridge` profile では Ray Client 経由で detached actor を呼びます。この経路では actor method の位置引数 signature が崩れることがあるため、`TaskQueueActor` / `SharedStoreActor` への複数引数呼び出しは keyword 引数で行います。`too many positional arguments` が出た場合はこの層を確認してください。
- `bridge` profile の cluster worker は、Ray Client 接続をデフォルトで 6 回、15 秒間隔で retry します。simulation 中は 60 秒ごとに `running` heartbeat を更新し、長い case 実行中に stale と誤判定されるのを避けます。
- Ray/GCS 接続が途中で切れた場合、worker は `ray_control_plane_lost` として停止し、その case の結果を無理に保存し続けません。司令塔側も Python 例外として検知できる範囲では `run_failed_ray_gcs_lost` を summary に残して停止します。
- 登録前に `Exited(1)` した worker container を自動再起動したい場合だけ、`--auto-restart-missing-workers` を明示してください。再起動は node ごと最大 2 回、cooldown 600 秒、再起動前に `docker logs --tail 100` を summary の `maintenance_events` に残す方針です。
- 長期実験で稼働中 container から GPU / NVML が見えなくなる問題へ自動対応する場合は、`--auto-restart-gpu-workers` を明示してください。worker は各ケースを queue から取る前に `nvidia-smi` を確認し、異常時は `gpu_unavailable` を通知して終了するため、新しいケースを消費しません。
- GPU watchdog は数ループごとに各稼働中 container の `nvidia-smi` も確認します。実行中ケースを強制終了するとqueue上の未完了taskを失うため、`running`中は隔離待ちにし、ケース終了後の取得前checkで停止させます。その後、ログ末尾を `maintenance_events` に保存し、worker container を `docker rm -f` して同じ profile から再作成します。simulation trace はホストの `simulation_traces_sim_worker_*` を bind mount しているため、container 再作成では削除されません。
- 再作成後はホストと新しい container の両方で GPU を確認し、成功時だけ一時隔離を解除します。失敗時は既定で600秒待って再試行し、連続3回失敗または24時間内の再作成5回到達でその node を終端隔離します。他nodeの実験は継続します。
- GPU自動復旧を有効にする標準指定は `--auto-restart-gpu-workers --gpu-worker-max-restarts-per-24h 5 --gpu-worker-max-consecutive-failures 3 --stale-worker-restart-cooldown-sec 600` です。上限値を増やす前に、host側driverやNVIDIA Container Toolkitの恒常障害でないことを確認してください。
- コンテナを切り替えるときは、次の順でそろえるのを基本にしてください。
  - 既存コンテナを停止して消す
  - 使いたい `container_profile` を 1 つ決める
  - その profile で worker / orchestrator を起動する
  - mount 済みの `cyclonedds.xml`、`VK_ICD_FILENAMES`、`DISPLAY`、map path がその profile で成立しているかを確認する
- 画面表示や学校ネットワーク対策 (下記の storm-control 対策) を含む `1.7.1` 系運用は、原則として `autoware171` profile を正本にしてください。`legacy` は互換用に残っている profile です。

## storm-control と bridge 化の理由

- `network_mode=host` で複数台の Autoware を動かすと、ROS 2 / Cyclone DDS のマルチキャスト通信が学内 LAN へ流れ、スイッチの storm-control によってポートが error-down されることがあります。実際にこの構成で研究室のポートが停止し、ネットワーク管理者に解除を依頼する必要がありました。
- 主因と推定しているのは、`legacy` profile (`network_mode=host`、`privileged=true`) で動かしていた ROS 2 / Cyclone DDS の通信です。host モードではコンテナが物理 NIC (`enp10s0`) を直接使うため、Autoware の数百ノード分の discovery マルチキャスト (`239.255.0.1`) やトピック通信が、複数号機から学内 LAN へ流れていたと考えています。
- 対策として、`autoware171` / `autoware180*` / `autoware190*` profile では、DDS の通信を物理 NIC へ出さないために次の 3 つを重ねています (`prism_maude` も bridge・非 privileged ですが、ROS を使わないため `cyclonedds.xml` は mount しません)。
  - `network_mode=bridge`、`privileged=false`: コンテナからは docker0 と `lo` しか見えず、docker0 上のマルチキャストは物理 NIC へ転送されません。
  - `cyclonedds.xml` の mount: `NetworkInterface=lo`、`AllowMulticast=false`、`Peer=127.0.0.1` で、DDS を loopback だけに閉じ込めます。
  - 手動実行時の `iptables -I DOCKER-USER 1 -i docker0 -o enp10s0 -j REJECT`: コンテナから学内 LAN への通信を止めます。
- 号機間の通信は、Ray Client (`ray://<master-ip>:10001`) の TCP ユニキャストだけにしています。そのため、コンテナを Ray node としてクラスタに参加させないでください。
- cluster worker を `network_mode=host` に戻したり、`cyclonedds.xml` を外したり、DDS のマルチキャストを有効にしたりしないでください。新しい profile を追加するときも、DDS の通信が物理 NIC へ出ないことを必ず確認してください。
- スイッチやハブのループ接続でも同じ error-down が起きます。物理的なループが原因の場合は、コンテナ側の設定では防げません。研究室内のハブの配線も確認してください。

## Autoware 1.7.1 クラスター標準実行コマンド

次のコマンドを司令塔の21号機で実行します。これは `bridge`、headless、
`AWSIMScriptPy` 同期、stale/missing worker復旧、GPU異常時の隔離・再作成を含む
1.7.1長期実験用の正本です。

```bash
cd /home/passd/AWSIM_launch

python3 run_orchestrator_cluster_v2.py \
  --output /home/passd/simulation_traces/uturn_records.jsonl \
  --dataset-csv /home/passd/simulation_traces/uturn_dataset.csv \
  --case-kind uturn \
  --mode binomial_ci \
  --binomial-target c_collision \
  --binomial-method wilson \
  --binomial-confidence 0.95 \
  --binomial-target-width 0.02 \
  --container-profile autoware171 \
  --scenario-profile autoware171 \
  --headless \
  --sync-awsim-script-py \
  --auto-restart-stale-workers \
  --auto-restart-missing-workers \
  --auto-restart-gpu-workers \
  --worker-queue-connect-retries 6 \
  --worker-queue-connect-retry-interval-sec 15 \
  --stale-worker-restart-cooldown-sec 600 \
  --gpu-worker-max-restarts-per-24h 5 \
  --gpu-worker-max-consecutive-failures 3 \
  --gpu-worker-stable-reset-sec 1800 \
  --gpu-worker-probe-timeout-sec 5
```

`--auto-restart-gpu-workers` は起動済みorchestratorへ後から反映できません。
このオプションを付けずに開始した実験では、現在の実験を安全に停止してから上記コマンドで
再開してください。既存CSVと各ホストの `simulation_traces_sim_worker_*` は削除しません。

上記コマンドの `--binomial-method wilson` は近似に基づく手法で、真の確率が0や1に近いときや
標本数が少ないときは、実際のカバレッジが名目の信頼水準を下回ることがあります。
`c_collision` の最終的な安全性判断には `--binomial-method clopper-pearson` を使ってください
(手法の選び方は [statistical_method_selection.md](statistical_method_selection.md))。

**`binomial_ci`の"repeated peeking"問題と`--binomial-anytime-valid`**: `--mode binomial_ci`は
1標本増えるたびに同じ`--binomial-confidence`で区間を計算し直す設計のため、理論上は「何度も
覗き見ること」自体が実効的な誤り率を悪化させうるという弱点があります(DKWのステージ制
alpha-spendingや、SPRT・EBStopの本質的にvalidな構成とは異なり、binomial_ciにはこれに対する
保護が組み込まれていません)。`--binomial-anytime-valid`を付けると、固定confidenceの代わりに
EBStopと同じ`d_t = c/t^1.1`型のunion bound(`evaluation/alpha_spending.py`、Mnih, Szepesvári,
Audibert 2008スタイルのpeeling schedule)で毎回の信頼水準を計算し直し、「何度チェックしても
全体の誤り率が`1-confidence`を超えない」ことを数学的に保証します。

ただし**このコストは大きく無視できません**: 同じtarget-widthに到達するのに必要なサンプル数は、
実測でおよそ**5〜8倍**に増えます(例: p=0.1、target-width=0.02、confidence=95%の場合、通常は
約3,600サンプルで収束するところ、`--binomial-anytime-valid`では約28,000サンプル必要)。
spending exponentの調整やチェック頻度を落とす等の工夫を試しても、この倍率は大きくは改善しません
(anytime-valid confidence sequence全般に共通する本質的なコストです)。そのため既定では無効
(オプトイン)にしており、AWSIM/PRISMのシミュレーション1回のコストを度外視できる、安全性の
理論的保証を最優先したい実験でのみ使うことを想定しています。

## 1.7.1 bridge worker の Python 依存 bundle

`autoware171` の verified image は Autoware / AWSIM の運用状態を固定するための image ですが、現在の `AWSIM_launch` worker は起動時に `ray` と `scikit-learn` を import します。そのため、`binomial_ci` や `dkw` では `sklearn` が無いと worker がキュー取得前に落ちます。

この依存はホストの `~/.local/lib/python3.10/site-packages` を丸ごと mount して解決しないでください。実測では host 側の `numpy==1.26.4` と container 側の `scipy==1.8.0` が混ざり、`SciPy requires NumPy <1.25.0` の警告が出ます。短期的に import は通っても、検証運用の再現性が落ちます。

`1.7.1` bridge worker では、各ホストに専用の Python deps bundle を置き、それだけを read-only mount する方針にします。

```text
host:      ${HOME}/awsim_python_deps/py310
container: /opt/awsim_python_deps/py310
env:       PYTHONPATH=/opt/awsim_python_deps/py310
```

bundle に入れる依存は、少なくとも次を固定します。

```text
ray[client]==2.55.0
scikit-learn==1.7.2
numpy==1.24.4
joblib
threadpoolctl
```

`ray` / `scikit-learn` を毎回 `pip install` する方式は、起動時間が長くなり、ネットワーク状態にも依存します。最終的にはこの bundle を image に焼き込んだ snapshot を作るのが本命ですが、まずは `autoware171` profile の mount/env でこの bundle を読む形を正本にします。

`2026-09-02` 時点で、21/22/23/24号機にこの bundle を配置済みです。

```text
各号機: ~/awsim_python_deps/py310
```

22/23/24号機では、`autoware_internal:2026-1.7.1-x11-verified-20260808` に mount した状態で `ray[client]==2.55.0`、`grpcio`、`sklearn==1.7.2`、`numpy==1.24.4`、container 標準の `scipy==1.8.0`、`StandardScaler` の import が通ることを確認済みです。bridge worker は `ray://<master-ip>:10001` で Ray Client 接続します。

## Maude checker の Python 環境

`AWSIM_launch` worker の Python 依存 bundle と、AW-CheckerPy の `maude` Python module は別物です。
`maude` は次の venv に入っているため、検証器は通常の `python3` ではなくこの Python を優先して使います。

```text
/home/passd/aw-cheaker/Maude-3.5.1/AW-CheckerPy/.venv/bin/python
```

`ModuleNotFoundError: No module named 'maude'` が出た場合は、`aw-cheaker` mount の中に
`AW-CheckerPy/.venv/lib/python3.10/site-packages/maude` が存在するかを確認してください。
`PYTHONPATH=/opt/awsim_python_deps/py310` だけを増やしても Maude は見えません。

## クラスター各ノードの Trash 掃除

Ray の `/tmp/ray ... is over 95% full` 警告が出た場合でも、実際には `/tmp/ray` ではなく
各ホストの root filesystem が詰まっていることがあります。まず `~/.local/share/Trash` を確認してください。

Trash 掃除は実験データ、Docker image、map、model には触れず、次の tool に固定します。
実行場所は司令塔の 21号機です。21号機から `redis_cluster/cluster_config.py` の
`enabled=True` ノードへ SSH し、各ノードの Trash だけを処理します。

```bash
cd /home/passd/AWSIM_launch

# 確認だけ。削除はしない。
python3 tools/maintenance/clean_trash.py

# 21/22/23/24 など enabled=True のノードで Trash を空にする。
python3 tools/maintenance/clean_trash.py --apply
```

デフォルトは `redis_cluster/cluster_config.py` で `enabled=True` のノードだけです。特定ノードだけ見る場合は次のように指定します。

```bash
# 22/23 だけ確認する。
python3 tools/maintenance/clean_trash.py --nodes 22,23

# cluster_config に登録されている全ノードで Trash を空にする。
python3 tools/maintenance/clean_trash.py --nodes all --apply
```

削除対象は各ユーザーの `~/.local/share/Trash/files/*` と `~/.local/share/Trash/info/*` のみです。
`simulation_traces*`、Docker image/container、`autoware_map`、`autoware180_runtime`、
`AWSIMScriptPy`、`AWSIM_launch` は削除対象にしません。

実験中に `/tmp/ray ... over 95% full` が出た場合は、まず削除なしで確認します。

```bash
python3 tools/maintenance/clean_trash.py
```

Trash が大きく、root filesystem の空きが危険な場合だけ `--apply` を付けます。
この tool は Ray や worker container を停止しません。

## AWSIM trace timeout と late JSON の扱い

クラスタ実行では、司令塔が割り当てるglobal loop番号と、各workerが実行する順番は一致しません。
Runtime Monitorにはworker内のローカル連番 (`1, 2, 3, ...`) を渡し、backendは
`{scenario}_test_sim{local}.json`を待ちます。取得後にJSON、動画、動画メタデータを
`{scenario}_eval_sim{global}.json`へ移動します。Runtime Monitorを再利用してもglobal番号を
直接渡してはいけません。

AWSIM は scenario 終了処理の最後に trace JSON を書くため、監視時刻ちょうどに
`uturn_test_sim<loop>.json` が出ていない場合でも、数秒後に JSON が届くことがあります。
`uturn` は旧 worker と同じく outer watcher で `300秒` 待ち、scenario 内部には別の
goal timeout を入れません。このため AWSIM backend は通常の scenario timeout の後、`10秒` の artifact grace を取ります。
grace 内に JSON を見つけた場合は `eval` 側へ昇格して解析し、`TIMEOUT` marker は書きません。

JSON がある試行は、scenario process の終了コードではなく checker の検証結果で最終判定します。
raw process の状態は `meta.raw_run_status`、timeout の内訳は `meta.raw_timeout_reason` に保存し、
検証結果を上書きしません。

| trace / checker の状態 | 最終 `status` | 衝突率への採用 |
| --- | --- | --- |
| JSON あり、checker が全式を評価し、EGO/NPC ともに移動 | `success` | 採用 (`c_collision` が `0` / `1`) |
| JSON あり、EGO または NPC が動いていない | `analysis_error` | 除外 (`c_collision=-1`) |
| JSON あり、formula 欠落・Maude 失敗 | `analysis_error` | 除外 |
| JSON なし、10秒 grace 後も未到着 | `timeout` | 除外 (`meta.timeout_reason=artifact_timeout`) |

連続値の `min_ttc`、`min_distance`、`min_ttb`、`z_margin` は、旧 `awchecker.py` と同じ
`AWKinematicsPipeline` を `targets/awsim/kinematics_bridge.py` 経由で呼んで trace JSON から算出します
(NPCは case kind の `TARGET_NPCS`、方式は `--ext_mode`)。抽出に失敗しても Maude の判定は残し、
値を空欄にして `meta.kinematics_error` に理由を残します。Maude 解析エラー時は4値とも `-1` です。
`2026-10-05` より前の v2 実行 (1.7.1 / 1.8.0 / 1.9.0 比較実験を含む) ではこの抽出が呼ばれておらず、
dataset CSV のこれらの列は空です。`--mode dkw` / `dkw_fixed` / `ebstop` / `worst_ttc` / `ttc_edge` は
これらの列を使うため、過去データで使う場合は trace JSON から再抽出してください。

trace JSON の必須項目 (`planning_trajectory`、`control_cmds` など) が空の場合は、判定は続けたうえで
`meta.empty_trace_keys` (dataset CSV では `meta_empty_trace_keys`) に項目名を残します。Runtime Monitor は
存在しない topic を購読してもエラーにならず空配列を書くため、Autoware の更新で topic 名が変わったときは
この列で検出してください。実例として、Autoware 1.9.0 では `/planning/scenario_planning/trajectory` の中継が
削除され、`2026-10-05` 以前の 1.9.0 trace の `planning_trajectory` は空です。Runtime Monitor は現在
`/planning/trajectory` を記録しており、1.7.1 / 1.8.0 でも同じ内容が記録されることを実機で確認しています。

たとえば scenario 側が exit code `124` を返しても、その後に回収した JSON が checker を通り、
EGO/NPC の移動と衝突判定が確認できれば `success` として採用します。逆に JSON があっても
車両が動いていない試行は衝突なしには数えません。

`binomial_ci` の有効標本は `status=success` かつ対象値が `0` / `1` の行だけです。
ここで `status` はchecker解析後の最終判定を表し、scenario実行時の状態は
`meta.raw_run_status`（dataset CSVでは `meta_raw_run_status`）へ分離して保存します。
したがって `meta.raw_run_status=timeout` であっても、trace JSONを回収してcheckerの解析に
成功した試行は最終的に `status=success` となり、統計検証に使用します。最終 `status=timeout` は、
有効なtraceまたは判定結果を取得できなかった試行として統計検証から除外します。

既存CSVを変更せず、timeout marker と同じ worker の local trace を再照合するには次を使います。
実行場所は21号機です。

```bash
cd /home/passd/AWSIM_launch

# 読み取りだけ。CSV、trace、container、Ray 状態は変更しない。
python3 tools/maintenance/reconcile_late_traces.py \
  --dataset-csv /home/passd/simulation_traces/uturn_dataset.csv \
  --case-kind uturn

# JSON report を別ファイルへ残す場合だけ明示する。
python3 tools/maintenance/reconcile_late_traces.py \
  --dataset-csv /home/passd/simulation_traces/uturn_dataset.csv \
  --case-kind uturn \
  --output /home/passd/simulation_traces/uturn_late_trace_reconciliation.json
```

このtimeout処理の変更はworker process起動時に読み込まれます。すでに実行中のworkerへは
遡及しないため、次のcluster runから有効です。

## success結果だけのCSVを作成する

元のdataset CSVを変更せず、`status`列が`success`の行だけを同じ列構成・行順で
別CSVへ保存する場合は次を実行します。出力先を省略すると、入力ファイルと同じ場所に
`<入力名>_success.csv`を作成します。

```bash
cd /home/passd/AWSIM_launch

python3 tools/maintenance/filter_success_csv.py \
  --dataset-csv /home/passd/simulation_traces/uturn_dataset.csv
```

既存のsuccess CSVを現在のdatasetから作り直す場合だけ`--overwrite`を付けます。

```bash
python3 tools/maintenance/filter_success_csv.py \
  --dataset-csv /home/passd/simulation_traces/uturn_dataset.csv \
  --output /home/passd/simulation_traces/uturn_dataset_success.csv \
  --overwrite
```

このツールは`timeout`、`execution_error`、`analysis_error`を除外します。
ただし、focusやerror recovery由来の成功行も保持します。`binomial_ci`が使用する
一様ランダム標本だけへ限定したCSVではありません。

## クラスター同期で追加更新するもの

クラスター起動時、リモートノードにはデフォルトで `~/AWSIM_launch` と
`~/AW-Runtime-Monitor` を同期します。前者は通常の worker / scenario / supervisor コード、
後者は trace JSON の保存処理に必要なため、両方を実験コードの正本として毎回揃えます。

`1.7.1` 系で `AWSIMScriptPy` 本体を更新したい場合だけ、次を追加します。

```bash
python3 run_orchestrator_cluster_v2.py ... \
  --container-profile autoware171 \
  --scenario-profile autoware171 \
  --headless \
  --sync-awsim-script-py \
  --auto-restart-gpu-workers
```

`AW-Runtime-Monitor` はデフォルトでディレクトリ全体を同期します。`main.py` だけを個別に
配布すると recorder 側と版がずれて起動時に失敗するため、部分同期は行いません。
`--sync-aw-runtime-monitor` は明示指定との互換のため残しますが、通常は入力不要です。

```bash
python3 run_orchestrator_cluster_v2.py ... \
  --sync-aw-runtime-monitor
```

この同期では `main.py`、`recorder/Recorder.py`、
`recorder/AWSIMClientOpStateTracker.py` の SHA-256 と構文を worker 起動前に検証します。
さらに container 内で lifecycle tracker を import できなければ worker は起動しません。

`1.8.0` 系で map を更新したい場合だけ、次を追加します。`1.9.0` 系では同じ用途で `--sync-autoware190-map` を使います。

```bash
python3 run_orchestrator_cluster_v2.py ... \
  --container-profile autoware180_ekfdiagfix \
  --scenario-profile autoware171 \
  --headless \
  --sync-autoware180-map
```

複数の追加更新が必要な作業では `--sync-awsim-script-py` と
`--sync-autoware180-map` を同時に指定できます。
デフォルト同期を止める特殊検証だけは `--no-sync-awsim-launch` または
`--no-sync-aw-runtime-monitor` を使います。通常の実験では指定しません。

cluster 起動前には各 image を使った `nvidia-smi` preflight も実行します。GPU を認識できない
ノードはタスクを取得させず、`cluster_preflight_failures` に理由を残して他ノードだけを起動します。
AWSIM、Autoware、Runtime Monitor が待機時間内に終了した場合も、trace timeout を待たず
`execution_error` とし、起動ログ末尾をエラー情報へ含めます。

## cluster worker の process supervisor

cluster container では `run_worker_v2.py` を PID 1 として直接起動しません。
PID 1 は `runtime.container.supervised_process.server` で、Ray Client / gRPC を import する前に
Unix socket `/tmp/awsim-process-supervisor.sock` を作り、その後 worker を子 process として起動します。

worker は socket 経由で supervisor に依頼し、次の process を起動・停止します。

- Xvfb
- AWSIM
- Autoware
- AW-Runtime-Monitor
- scenario client
- readiness probe
- AW Checker / Maude / BBSL の外部 command

これにより、gRPC thread が動作中の worker 自身から `fork()` して
`cygrpc` や `librclcpp` が不定期に segfault する経路を避けます。AWSIM、Autoware、scenario の
command、待機時間、parameter は変更しません。手動実行など
`AWSIM_PROCESS_SUPERVISOR_SOCKET` がない環境では、従来のローカル subprocess 経路を使います。

supervisor が終了した場合は管理下の process group を停止します。worker が終了した場合も
AWSIM/Autoware を残したまま container を存続させず、同じ container を再利用しません。

## 技術的な工夫・トラブルシューティング (分散自動化に関する解決策)

本システムは、マスター機からリモート機を制御し、バックグラウンドでシミュレーションを完全自動化しています。その際、手動でターミナルから実行した時と異なり「車が動かない」「レーダー（点群）が消える」「通信が詰まる」といった特有の問題に対処するため、以下の実装が組み込まれています。

1. **対話型シェル (`bash -i`) による完全なROS/DDS環境ロード (`cluster_manager.py` / `run_manager.py`)**
   コンテナを起動してバックグラウンドでコマンドを実行する際、通常の `bash -c` ではUbuntuの仕様により `~/.bashrc` の読み込みが途中でキャンセルされます。これによりCycloneDDS等の大容量通信向けのチューニング設定がAutowareに適用されず、通信詰まりやレーダーデータが消失する問題がありました。これを `bash -i -c` を用いて対話モードを偽装することで、手動ログイン時と全く同じROS通信環境を確立しています。
   - **重要**: コンテナ内で `docker exec` を使って手動デバッグするときも、この原則を崩さないでください。`docker exec ... bash -c ...` や `docker exec ... bash -lc ...` で直接起動すると、`run_manager.py` / `cluster_manager.py` と同じ環境にならず、`README` 通りの手順でも「車が動かない」「route が入らない」「点群やDDS通信が不安定になる」といった切り分けしづらい差分が入ります。手動実行時も `bash -i -c` を使い、必要ならその中で `source /home/passd/autoware/install/setup.bash` まで明示してください。
2. **マスター・リモート間のROS通信の分離 (`cluster_manager.py`)**
   複数台のコンピュータで同時にシミュレーションを実行する際、ROS 2の通信がネットワーク上で混線しないよう、コンテナ起動時に `ROS_DOMAIN_ID` を号機ごとに割り当て、完全に独立した通信環境を構築しています。
3. **リモート環境 (ヘッドレス) での仮想ディスプレイ(Xvfb)とGPU連携 (`cluster_manager.py`)**
   物理ディスプレイが接続されていないリモートPC (22, 23号機) では、画面を描画できないためにRVizが無限クラッシュしたり、AWSIMのLiDAR点群が生成されなくなる問題が発生します。これを以下の3つの連携で解決しています。
   - **Xvfbの自動起動**: まずコンテナ内で `Xvfb :99` を立ち上げ、その上で `DISPLAY=:99` を指定してGUIアプリケーションの描画先を仮想モニターへ向けます。`DISPLAY=:99` は描画先の指定だけであり、Xvfb 本体を起動するわけではありません。
   - **NVIDIA GPUの強制認識**: 通常、Xvfb環境ではGPUが使われませんが、AWSIMのLiDAR計算はGPU(Vulkan)に依存しています。そこで環境変数 `VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.json` を注入し、仮想画面下でも強制的にGPUを認識させて点群データを正常に生成させています。
   - **AWSIMの通常起動**: AWSIMの `-batchmode` (画面なしモード) はセンサーデータ欠損を引き起こすため使用せず、Xvfbに向かって「通常起動」させることで正常なシミュレーションを実現しています。
4. **ログの隔離とハングアップの防止 (`run_manager.py`)**
   AWSIMとAutowareの膨大な標準出力がパイプを詰まらせてプロセスをフリーズさせないよう、各出力は `awsim.log` および `autoware.log` の専用ファイルに隔離しています。現場監督の進行状況は、Pythonのアンバッファード出力（`-u` オプション）を利用してリアルタイムに監視できるようにしています。

## 実行結果の3D可視化

可視化系の正本は現在 `tools/plot/*` です。
root 直下の `visualize_*.py` は legacy wrapper として残してあり、内部では `tools/plot/*` を呼びます。

```bash
# 最新の実験結果を可視化する場合
python3 tools/plot/visualize_traces.py ~/simulation_traces

# 過去に退避させた特定のデータを可視化する場合
python3 tools/plot/visualize_traces.py ~/simulation_traces_shared_20260512_144346

# MIN_TTCの連続値に基づく深刻度の3D可視化 (Maudeのフラグではなく抽出器の数値を優先)
python3 tools/plot/visualize_min_ttc_3d.py ~/simulation_traces

# リスク評価マトリックスの3D可視化
python3 tools/plot/visualize_risk_matrix.py ~/simulation_traces

# 衝突セル/非衝突セルをボクセル領域として可視化
python3 tools/plot/visualize_collision_regions.py ~/simulation_traces

# 実データの衝突外縁とAI学習境界を滑らかな面として可視化
python3 tools/plot/visualize_collision_surfaces.py ~/simulation_traces

# 1.7.1の元ケースと1.8.0 replayをケース番号で対応付けて差分を可視化
python3 -m tools.plot.visualize_version_comparison \
  ~/simulation_traces_shared_20260911_125415/uturn_dataset_success.csv \
  ~/simulation_traces_autoware180_replay_20260911/uturn_dataset.csv \
  --output ~/simulation_traces_autoware180_replay_20260911/autoware171_vs_180_comparison.png
```

## リアルタイム監視・トラブルシューティング
マスターPC（21号機）の別のターミナルから、ワーカーPC（例: 23号機）の内部で動いているシミュレーションの状況をリアルタイム監視するためのコマンドです。

### 1. 現場監督の進捗ログ (ワーカーメインログ)
現在何ループ目のテストをしているか、エラーで再起動していないかを確認できます。
```bash
# 例: 23号機の場合
ssh worker2@192.0.2.23 "docker exec sim_worker_23 tail -f /home/passd/simulation_traces/worker_log_sim_worker_23.txt"
```

### 2. インフラの内部ログ (詳細エラー調査)
シミュレータ本体やAutowareが正常に起動しているか確認したい場合に使います。
```bash
ssh worker2@192.0.2.23 "docker exec sim_worker_23 tail -f /home/passd/simulation_traces/awsim.log"
ssh worker2@192.0.2.23 "docker exec sim_worker_23 tail -f /home/passd/simulation_traces/autoware.log"
```

### コンテナ内での手動デバッグ
もし特定の号機でシミュレーションがうまく動かない場合、以下の手順でシステムと全く同じ環境設定のコンテナに手動で入り、どこでエラーが起きているか検証することができます。

`bridge` コンテナを使う手動起動の考え方は [docs/network_storm_control_guide.md](network_storm_control_guide.md) を参照してください。

```bash
docker exec -it autoware171-x11-map bash -i -c 'cd /home/passd/awsim_labs && ./awsim_labs.x86_64 -noise false'
docker exec -it autoware171-x11-map bash -i -c 'source /home/passd/autoware/install/setup.bash && cd /home/passd/autoware && ros2 launch autoware_launch e2e_simulator.launch.xml vehicle_model:=awsim_labs_vehicle sensor_model:=awsim_labs_sensor_kit map_path:=/home/passd/autoware_map/nishishinjuku_autoware_map launch_vehicle_interface:=true rviz:=false'
```

`bash -c` や `bash -lc` で十分そうに見えても、コンテナ運用では `bash -i -c` を正本にしてください。

---

<a id="english"></a>

# English: Operating an AWSIM cluster

Operating procedures for running AWSIM / Autoware worker containers on several GPU machines and running experiments from the v2
orchestrator (`run_orchestrator_cluster_v2.py`). The developers' lab runs a four-node cluster (nodes 21–24, with node 21 as the head
node). Replace the node numbers and paths with those of your environment. Set the node IP addresses, users, and container names in
[redis_cluster/cluster_config.py](../redis_cluster/cluster_config.py).

## Switching `container_profile`

- The supported `container_profile` values are `legacy`, `autoware171`, `autoware180`, `autoware180_ekfdiagfix`, `autoware190`, and
  `autoware190_ekfdiagfix`.
- `legacy` uses the older container settings: `image=autoware_internal:2026`, `network_mode=host`, `privileged=true`.
- `autoware171` is the current setup for the `1.7.1` line: `image=autoware_internal:2026-1.7.1-x11-verified-20260808`,
  `network_mode=bridge`, `privileged=false`.
- `autoware180` is for comparison with the official `1.8.0`, and `autoware180_ekfdiagfix` is the EKF diagnostic overlay version for
  use with AWSIM. Both mount `autoware180_runtime/maps` and `autoware180_runtime/ml_models`.
- `autoware190` is for comparison: the official `1.9.0` image with only an AWSIM compatibility layer on top. `autoware190_ekfdiagfix`
  is for experiments and applies the same EKF diagnostic overlay as 1.8.0. Network / privileged / user / mounts are the same as
  `autoware180_ekfdiagfix`, and it mounts `autoware190_runtime/maps` and `autoware190_runtime/ml_models`. The default `CYCLONEDDS_URI`
  of the official 1.9.0 image is `/home/aw/cyclonedds.xml`, which allows multicast, so the profile sets `/home/passd/cyclonedds.xml`
  explicitly in its environment. See [docker/README_autoware_1.9.0.md](../docker/README_autoware_1.9.0.md) for details.
- The `/docker-entrypoint.sh` of the `autoware180` / `autoware190` images initializes with root privileges and then starts the actual
  processes with `gosu passd`. So only these two profiles fix the Docker start user to `root`. With `docker run --user passd`, the user
  switch in the entrypoint fails with `operation not permitted`. The loopback multicast and sysctl WARNs shown without `privileged` are
  not fatal and are unrelated to this user-switch error.
- The Ray Client, checker, and statistics of `autoware180` workers use the `awsim_python_deps/py310` bundle, fixed across all nodes and
  mounted read-only at `/opt/awsim_python_deps/py310`. Relying only on user-local packages in the image makes `grpc` invisible after
  `gosu passd` in the entrypoint, and the worker exits with `ray_control_plane_lost`.
- For `autoware171` / `autoware180*`, mounts such as `cyclonedds.xml`, `AWSIMScriptPy`, `autoware_map`, and `AW-Runtime-Monitor` are
  managed together in the profile. Fix the profile definition first instead of adding or removing `docker run` options by hand each time.
- If only `--container-profile` is given on the CLI, `--scenario-profile` defaults to the same name.
- However, the current scenario implementation is a single configuration in the new framework, so `scenario_profile=legacy` does not
  revert the scenario contents to the old implementation. What changes is mainly the container settings.
- So even when you "only want to switch containers", think in terms of the `container_profile` first. In practice, the differences in
  network / privileged / env / mounts of `docker run` matter more than the differences between scenarios.
- `container_profile` determines the image, network, privileged mode, mounts, and Docker environment. Whether to show a display or run
  headless is finally decided by `--headless`.
- With `--headless`, the Xvfb virtual display is used, and `rviz:=false` is added to the Autoware launch automatically. `DISPLAY=:99`
  alone does not stop RViz.
- To check visually with a display, do not add `--headless`; use the manual commands for X11 display or the procedure for containers
  with a display.
- When starting cluster workers with a `bridge` profile, no Ray worker node is started inside the container. `run_worker_v2.py`
  connects to the `TaskQueueActor` / `SharedStoreActor` on the head node as an external Ray driver. Do not register the Docker bridge's
  internal IP or the host's physical IP as a Ray node inside the container.
- With `bridge` profiles, detached actors are called through Ray Client. On this path, positional-argument signatures of actor methods
  can break, so calls with several arguments to `TaskQueueActor` / `SharedStoreActor` use keyword arguments. If you see
  `too many positional arguments`, check this layer.
- Cluster workers with a `bridge` profile retry the Ray Client connection 6 times at 15-second intervals by default. During a simulation
  they update a `running` heartbeat every 60 seconds so that long cases are not misjudged as stale.
- If the Ray/GCS connection drops midway, the worker stops as `ray_control_plane_lost` and does not keep trying to save the result of
  that case. The orchestrator also stops and records `run_failed_ray_gcs_lost` in the summary, as far as it can detect it as a Python
  exception.
- Only if you want worker containers that `Exited(1)` before registering to be restarted automatically, specify
  `--auto-restart-missing-workers`. Restarts are limited to 2 per node with a 600-second cooldown, and `docker logs --tail 100` is saved
  in `maintenance_events` of the summary before restarting.
- To handle automatically the problem of GPU / NVML becoming invisible from running containers in long experiments, specify
  `--auto-restart-gpu-workers`. Workers check `nvidia-smi` before taking each case from the queue, and on failure they report
  `gpu_unavailable` and exit, so they do not consume new cases.
- The GPU watchdog also checks `nvidia-smi` in each running container every few loops. Killing a running case would lose the
  unfinished task on the queue, so while `running` the container is only marked for isolation, and it is stopped by the check before it
  takes the next case. Then the tail of the log is saved in `maintenance_events`, and the worker container is removed with
  `docker rm -f` and recreated from the same profile. Simulation traces are bind-mounted from `simulation_traces_sim_worker_*` on the
  host, so they are not deleted when the container is recreated.
- After recreation, the GPU is checked both on the host and in the new container, and the temporary isolation is lifted only on
  success. On failure, it waits 600 seconds by default and retries; after 3 consecutive failures or 5 recreations within 24 hours, the
  node is isolated permanently. Experiments on the other nodes continue.
- The standard options for enabling automatic GPU recovery are `--auto-restart-gpu-workers --gpu-worker-max-restarts-per-24h 5
  --gpu-worker-max-consecutive-failures 3 --stale-worker-restart-cooldown-sec 600`. Before raising the limits, make sure there is no
  persistent problem with the host driver or the NVIDIA Container Toolkit.
- When switching containers, basically follow this order:
  - Stop and remove the existing containers
  - Decide on one `container_profile` to use
  - Start the workers / orchestrator with that profile
  - Check that the mounted `cyclonedds.xml`, `VK_ICD_FILENAMES`, `DISPLAY`, and map path are valid for that profile
- For the `1.7.1` line, including display and the campus network countermeasures (the storm-control measures below), use the
  `autoware171` profile as the standard. `legacy` is kept for compatibility.

## Storm control and why containers use the bridge network

- Running several Autoware instances with `network_mode=host` sends ROS 2 / Cyclone DDS multicast traffic to the campus LAN, and the
  switch's storm control may put the ports into error-down. In practice, this setup shut down the lab's ports, and the network
  administrators had to be asked to restore them.
- The main cause is believed to be the ROS 2 / Cyclone DDS traffic of the `legacy` profile (`network_mode=host`, `privileged=true`).
  In host mode, containers use the physical NIC (`enp10s0`) directly, so discovery multicast (`239.255.0.1`) and topic traffic from
  hundreds of Autoware nodes on several machines flowed into the campus LAN.
- As countermeasures, the `autoware171` / `autoware180*` / `autoware190*` profiles combine three measures to keep DDS traffic off the
  physical NIC (`prism_maude` also uses bridge and no privileged mode, but does not mount `cyclonedds.xml` because it does not use ROS):
  - `network_mode=bridge`, `privileged=false`: containers see only docker0 and `lo`, and multicast on docker0 is not forwarded to the
    physical NIC.
  - Mounting `cyclonedds.xml`: `NetworkInterface=lo`, `AllowMulticast=false`, `Peer=127.0.0.1` confine DDS to loopback.
  - For manual runs, `iptables -I DOCKER-USER 1 -i docker0 -o enp10s0 -j REJECT` blocks traffic from containers to the campus LAN.
- Communication between nodes is limited to TCP unicast over Ray Client (`ray://<master-ip>:10001`). Therefore, do not let containers
  join the cluster as Ray nodes.
- Do not switch cluster workers back to `network_mode=host`, remove `cyclonedds.xml`, or enable DDS multicast. When adding a new
  profile, always confirm that DDS traffic does not leave through the physical NIC.
- A loop in switches or hubs causes the same error-down. If a physical loop is the cause, container settings cannot prevent it. Check
  the cabling of the hubs in the lab as well.

## Standard cluster command for Autoware 1.7.1

Run the following command on the head node. It is the standard for long 1.7.1 experiments, including `bridge`, headless mode,
`AWSIMScriptPy` sync, recovery of stale/missing workers, and isolation and recreation on GPU failures.

```bash
cd /home/passd/AWSIM_launch

python3 run_orchestrator_cluster_v2.py \
  --output /home/passd/simulation_traces/uturn_records.jsonl \
  --dataset-csv /home/passd/simulation_traces/uturn_dataset.csv \
  --case-kind uturn \
  --mode binomial_ci \
  --binomial-target c_collision \
  --binomial-method wilson \
  --binomial-confidence 0.95 \
  --binomial-target-width 0.02 \
  --container-profile autoware171 \
  --scenario-profile autoware171 \
  --headless \
  --sync-awsim-script-py \
  --auto-restart-stale-workers \
  --auto-restart-missing-workers \
  --auto-restart-gpu-workers \
  --worker-queue-connect-retries 6 \
  --worker-queue-connect-retry-interval-sec 15 \
  --stale-worker-restart-cooldown-sec 600 \
  --gpu-worker-max-restarts-per-24h 5 \
  --gpu-worker-max-consecutive-failures 3 \
  --gpu-worker-stable-reset-sec 1800 \
  --gpu-worker-probe-timeout-sec 5
```

`--auto-restart-gpu-workers` cannot be applied to an orchestrator that is already running. For an experiment started without this
option, stop the current experiment safely and restart it with the command above. The existing CSV and `simulation_traces_sim_worker_*`
on each host are not deleted.

`--binomial-method wilson` in the command above is an approximation; when the true probability is close to 0 or 1, or the sample size
is small, the actual coverage can fall below the nominal confidence level. Use `--binomial-method clopper-pearson` for final safety
decisions on `c_collision` (see [statistical_method_selection.md](statistical_method_selection.md#english) for choosing a method).

**The "repeated peeking" problem of `binomial_ci` and `--binomial-anytime-valid`**: `--mode binomial_ci` recomputes the interval with
the same `--binomial-confidence` every time a sample is added, so in theory the act of "looking repeatedly" itself can inflate the
effective error rate (unlike SPRT and EBStop, which are valid by design, `binomial_ci` has no built-in protection against this). With
`--binomial-anytime-valid`, instead of a fixed confidence, the confidence level is recomputed each time with a union bound of the form
`d_t = c/t^1.1`, as in EBStop (`evaluation/alpha_spending.py`, a peeling schedule in the style of Mnih, Szepesvári, Audibert 2008),
which guarantees mathematically that "the overall error rate does not exceed `1-confidence` however many times it is checked".

However, **the cost is large**: the number of samples needed to reach the same target width grows by about **5–8 times** in practice
(for example, with p = 0.1, target width = 0.02, and 95% confidence, it normally converges at about 3,600 samples but needs about
28,000 with `--binomial-anytime-valid`). Tuning the spending exponent or checking less often does not improve this ratio much (it is an
inherent cost of anytime-valid confidence sequences in general). It is therefore off by default (opt-in) and intended only for
experiments where the theoretical safety guarantee comes first and the cost of each AWSIM/PRISM simulation can be ignored.

## Python dependency bundle for 1.7.1 bridge workers

The verified `autoware171` image fixes the operational state of Autoware / AWSIM, but the current `AWSIM_launch` worker imports `ray`
and `scikit-learn` at startup. So for `binomial_ci` and `dkw`, the worker crashes before taking from the queue if `sklearn` is missing.

Do not solve this by mounting the host's whole `~/.local/lib/python3.10/site-packages`. In practice, the host's `numpy==1.26.4` mixes
with the container's `scipy==1.8.0` and produces the warning `SciPy requires NumPy <1.25.0`. Even if imports work for a while,
reproducibility suffers.

For `1.7.1` bridge workers, the policy is to place a dedicated Python dependency bundle on each host and mount only that, read-only.

```text
host:      ${HOME}/awsim_python_deps/py310
container: /opt/awsim_python_deps/py310
env:       PYTHONPATH=/opt/awsim_python_deps/py310
```

Pin at least the following in the bundle.

```text
ray[client]==2.55.0
scikit-learn==1.7.2
numpy==1.24.4
joblib
threadpoolctl
```

Running `pip install` for `ray` / `scikit-learn` every time makes startup slow and depends on the network. Ultimately, a snapshot image
with this bundle baked in is the goal, but for now the standard is to read this bundle through the mounts/env of the `autoware171`
profile.

As of 2026-09-02, this bundle is in place on nodes 21/22/23/24.

```text
each node: ~/awsim_python_deps/py310
```

On nodes 22/23/24, imports of `ray[client]==2.55.0`, `grpcio`, `sklearn==1.7.2`, `numpy==1.24.4`, the container's own `scipy==1.8.0`,
and `StandardScaler` have been confirmed with the bundle mounted into `autoware_internal:2026-1.7.1-x11-verified-20260808`. Bridge
workers connect over Ray Client at `ray://<master-ip>:10001`.

## Python environment for the Maude checker

The Python dependency bundle of the `AWSIM_launch` worker and the `maude` Python module of AW-CheckerPy are separate. `maude` is in the
following venv, so the verifier prefers this Python over the normal `python3`.

```text
/home/passd/aw-cheaker/Maude-3.5.1/AW-CheckerPy/.venv/bin/python
```

If you see `ModuleNotFoundError: No module named 'maude'`, check that
`AW-CheckerPy/.venv/lib/python3.10/site-packages/maude` exists inside the `aw-cheaker` mount. Adding only
`PYTHONPATH=/opt/awsim_python_deps/py310` does not make Maude visible.

## Emptying the Trash on cluster nodes

Even when Ray warns `/tmp/ray ... is over 95% full`, it is often the root filesystem of the host that is full, not `/tmp/ray`. Check
`~/.local/share/Trash` first.

Emptying the Trash does not touch experiment data, Docker images, maps, or models, and always uses the following tool. Run it on the
head node. From there, it connects over SSH to the nodes with `enabled=True` in `redis_cluster/cluster_config.py` and processes only
the Trash on each node.

```bash
cd /home/passd/AWSIM_launch

# Check only. Nothing is deleted.
python3 tools/maintenance/clean_trash.py

# Empty the Trash on the nodes with enabled=True (for example, 21/22/23/24).
python3 tools/maintenance/clean_trash.py --apply
```

By default only the nodes with `enabled=True` in `redis_cluster/cluster_config.py` are used. To look at specific nodes, specify them as
follows.

```bash
# Check only 22/23.
python3 tools/maintenance/clean_trash.py --nodes 22,23

# Empty the Trash on all nodes registered in cluster_config.
python3 tools/maintenance/clean_trash.py --nodes all --apply
```

Only `~/.local/share/Trash/files/*` and `~/.local/share/Trash/info/*` of each user are deleted. `simulation_traces*`, Docker images
and containers, `autoware_map`, `autoware180_runtime`, `AWSIMScriptPy`, and `AWSIM_launch` are never deleted.

If `/tmp/ray ... over 95% full` appears during an experiment, first check without deleting.

```bash
python3 tools/maintenance/clean_trash.py
```

Add `--apply` only when the Trash is large and free space on the root filesystem is dangerously low. This tool does not stop Ray or
the worker containers.

## AWSIM trace timeouts and late JSON

In cluster runs, the global loop numbers assigned by the orchestrator do not match the order in which each worker runs cases. The
Runtime Monitor is given a local sequence number within the worker (`1, 2, 3, ...`), and the backend waits for
`{scenario}_test_sim{local}.json`. After it arrives, the JSON, video, and video metadata are moved to `{scenario}_eval_sim{global}.json`.
Do not pass the global number to the Runtime Monitor directly, even when reusing it.

AWSIM writes the trace JSON at the very end of the scenario shutdown, so even if `uturn_test_sim<loop>.json` is not there exactly at the
watch deadline, the JSON may arrive a few seconds later. `uturn` waits `300 seconds` in the outer watcher, as the old worker did, and has
no separate goal timeout inside the scenario. So the AWSIM backend allows a `10-second` artifact grace period after the normal scenario
timeout. If the JSON is found within the grace period, it is promoted to `eval` and analyzed, and no `TIMEOUT` marker is written.

For runs with a JSON, the final judgment comes from the checker result, not from the exit code of the scenario process. The raw
process state is saved in `meta.raw_run_status` and the breakdown of the timeout in `meta.raw_timeout_reason`; they do not overwrite the
verification result.

| Trace / checker state | Final `status` | Used for the collision rate |
| --- | --- | --- |
| JSON present, the checker evaluated all formulas, and both EGO and NPC moved | `success` | Used (`c_collision` is `0` / `1`) |
| JSON present, EGO or NPC did not move | `analysis_error` | Excluded (`c_collision=-1`) |
| JSON present, missing formulas or Maude failure | `analysis_error` | Excluded |
| No JSON, still missing after the 10-second grace period | `timeout` | Excluded (`meta.timeout_reason=artifact_timeout`) |

The continuous values `min_ttc`, `min_distance`, `min_ttb`, and `z_margin` are computed from the trace JSON by calling the same
`AWKinematicsPipeline` as the old `awchecker.py` through `targets/awsim/kinematics_bridge.py` (the NPCs come from `TARGET_NPCS` of the
case kind, and the method from `--ext_mode`). If extraction fails, the Maude judgment is kept, the values are left empty, and the reason
is recorded in `meta.kinematics_error`. On a Maude analysis error, all four values are `-1`. In v2 runs before 2026-10-05 (including the
1.7.1 / 1.8.0 / 1.9.0 comparison experiments), this extraction was not called, and these columns of the dataset CSV are empty.
`--mode dkw` / `dkw_fixed` / `ebstop` / `worst_ttc` / `ttc_edge` use these columns, so re-extract them from the trace JSON when using
old data.

If required trace JSON fields (`planning_trajectory`, `control_cmds`, and others) are empty, the judgment continues and the field names
are recorded in `meta.empty_trace_keys` (`meta_empty_trace_keys` in the dataset CSV). The Runtime Monitor does not raise an error when
subscribing to a topic that does not exist and writes an empty array instead, so use this column to detect topic renames after an
Autoware update. For example, Autoware 1.9.0 removed the relay of `/planning/scenario_planning/trajectory`, and `planning_trajectory` is
empty in 1.9.0 traces before 2026-10-05. The Runtime Monitor now records `/planning/trajectory`, which was confirmed on the real system to
record the same content on 1.7.1 / 1.8.0.

For example, even if the scenario returns exit code `124`, the run is accepted as `success` if the JSON collected afterwards passes the
checker and the movement of EGO/NPC and the collision judgment are confirmed. Conversely, a run whose vehicles did not move is not
counted as "no collision" even if there is a JSON.

Valid samples for `binomial_ci` are only rows with `status=success` and a target value of `0` / `1`. Here `status` is the final
judgment after the checker analysis, and the state at scenario execution is stored separately in `meta.raw_run_status`
(`meta_raw_run_status` in the dataset CSV). So even with `meta.raw_run_status=timeout`, a run whose trace JSON was collected and
successfully analyzed by the checker ends up as `status=success` and is used for statistical verification. A final `status=timeout`
means that no valid trace or judgment was obtained, and such runs are excluded from statistical verification.

To re-match local traces of the same worker against timeout markers without changing the existing CSV, use the following. Run it on
the head node.

```bash
cd /home/passd/AWSIM_launch

# Read-only. Does not change the CSV, traces, containers, or Ray state.
python3 tools/maintenance/reconcile_late_traces.py \
  --dataset-csv /home/passd/simulation_traces/uturn_dataset.csv \
  --case-kind uturn

# Specify this only when you want to keep a JSON report in a separate file.
python3 tools/maintenance/reconcile_late_traces.py \
  --dataset-csv /home/passd/simulation_traces/uturn_dataset.csv \
  --case-kind uturn \
  --output /home/passd/simulation_traces/uturn_late_trace_reconciliation.json
```

Changes to this timeout handling are loaded when the worker process starts. They do not apply to workers that are already running, so
they take effect from the next cluster run.

## Creating a CSV with only successful results

To save only the rows whose `status` column is `success` to a separate CSV with the same columns and row order, without changing the
original dataset CSV, run the following. If the output is omitted, `<input name>_success.csv` is created next to the input file.

```bash
cd /home/passd/AWSIM_launch

python3 tools/maintenance/filter_success_csv.py \
  --dataset-csv /home/passd/simulation_traces/uturn_dataset.csv
```

Add `--overwrite` only when rebuilding an existing success CSV from the current dataset.

```bash
python3 tools/maintenance/filter_success_csv.py \
  --dataset-csv /home/passd/simulation_traces/uturn_dataset.csv \
  --output /home/passd/simulation_traces/uturn_dataset_success.csv \
  --overwrite
```

This tool excludes `timeout`, `execution_error`, and `analysis_error`. However, it keeps successful rows from focus runs and error
recovery as well. It is not a CSV limited to the uniform random samples used by `binomial_ci`.

## What else cluster sync can update

When the cluster starts, `~/AWSIM_launch` and `~/AW-Runtime-Monitor` are synced to the remote nodes by default. The former holds the
usual worker / scenario / supervisor code, and the latter is needed to save the trace JSON, so both are aligned every time as the source
of truth for experiment code.

Only when you want to update the `AWSIMScriptPy` itself for the `1.7.1` line, add the following.

```bash
python3 run_orchestrator_cluster_v2.py ... \
  --container-profile autoware171 \
  --scenario-profile autoware171 \
  --headless \
  --sync-awsim-script-py \
  --auto-restart-gpu-workers
```

`AW-Runtime-Monitor` is synced as a whole directory by default. Distributing only `main.py` would leave it out of sync with the recorder
and fail at startup, so partial syncs are not done. `--sync-aw-runtime-monitor` is kept for compatibility with explicit use but is
normally not needed.

```bash
python3 run_orchestrator_cluster_v2.py ... \
  --sync-aw-runtime-monitor
```

This sync checks the SHA-256 and syntax of `main.py`, `recorder/Recorder.py`, and `recorder/AWSIMClientOpStateTracker.py` before the
worker starts. In addition, the worker does not start if the lifecycle tracker cannot be imported inside the container.

Only when you want to update the maps for the `1.8.0` line, add the following. For the `1.9.0` line, use `--sync-autoware190-map` for the
same purpose.

```bash
python3 run_orchestrator_cluster_v2.py ... \
  --container-profile autoware180_ekfdiagfix \
  --scenario-profile autoware171 \
  --headless \
  --sync-autoware180-map
```

When several additional updates are needed, `--sync-awsim-script-py` and `--sync-autoware180-map` can be specified together. Only for
special checks that stop the default sync, use `--no-sync-awsim-launch` or `--no-sync-aw-runtime-monitor`. Do not specify them in normal
experiments.

Before the cluster starts, an `nvidia-smi` preflight with each image is also run. Nodes whose GPU cannot be detected do not take tasks;
the reason is recorded in `cluster_preflight_failures`, and only the other nodes are started. If AWSIM, Autoware, or the Runtime Monitor
exits within the wait time, the run becomes `execution_error` without waiting for the trace timeout, and the tail of the startup log is
included in the error information.

## The process supervisor of cluster workers

Cluster containers do not start `run_worker_v2.py` directly as PID 1. PID 1 is `runtime.container.supervised_process.server`, which
creates the Unix socket `/tmp/awsim-process-supervisor.sock` before importing Ray Client / gRPC, and then starts the worker as a child
process.

The worker asks the supervisor through the socket to start and stop these processes:

- Xvfb
- AWSIM
- Autoware
- AW-Runtime-Monitor
- scenario client
- readiness probe
- external commands of AW Checker / Maude / BBSL

This avoids `fork()` from the worker itself while gRPC threads are running, which caused `cygrpc` and `librclcpp` to segfault
intermittently. The commands, wait times, and parameters of AWSIM, Autoware, and the scenario are unchanged. Where
`AWSIM_PROCESS_SUPERVISOR_SOCKET` is not set, such as manual runs, the previous local subprocess path is used.

When the supervisor exits, it stops the process groups it manages. When the worker exits, the container is not kept alive with
AWSIM/Autoware left running, and the same container is not reused.

## Technical measures and troubleshooting for distributed automation

The system controls remote machines from the head node and fully automates simulations in the background. Unlike manual runs from a
terminal, this causes specific problems such as "the car does not move", "the point cloud disappears", and "communication gets stuck".
The following measures address them.

1. **Loading the full ROS/DDS environment with an interactive shell (`bash -i`) (`cluster_manager.py` / `run_manager.py`)**
   When a container is started and a command runs in the background, plain `bash -c` stops reading `~/.bashrc` partway because of
   Ubuntu's defaults. As a result, the tuning for high-volume communication such as CycloneDDS was not applied to Autoware, causing
   stuck communication and lost point clouds. Using `bash -i -c` to emulate an interactive session establishes exactly the same ROS
   communication environment as a manual login.
   - **Important**: keep this principle when debugging by hand with `docker exec` inside a container. Starting directly with
     `docker exec ... bash -c ...` or `docker exec ... bash -lc ...` does not give the same environment as `run_manager.py` /
     `cluster_manager.py`, and introduces differences that are hard to isolate, such as "the car does not move", "the route is not
     set", or "the point cloud and DDS communication are unstable", even when following the README. Use `bash -i -c` for manual runs
     too, and if needed, explicitly `source /home/passd/autoware/install/setup.bash` inside it.
2. **Separating ROS communication between nodes (`cluster_manager.py`)**
   When several computers run simulations at the same time, each node is assigned its own `ROS_DOMAIN_ID` at container startup so
   that ROS 2 traffic does not mix on the network, giving fully independent communication environments.
3. **Virtual display (Xvfb) and GPU on headless remote machines (`cluster_manager.py`)**
   On remote PCs without a physical display, RViz crashes endlessly and AWSIM stops producing LiDAR point clouds because nothing can be
   drawn. Three measures together solve this.
   - **Starting Xvfb automatically**: `Xvfb :99` is started inside the container first, and `DISPLAY=:99` points GUI applications to
     the virtual monitor. `DISPLAY=:99` only specifies where to draw; it does not start Xvfb itself.
   - **Forcing the NVIDIA GPU to be used**: normally the GPU is not used under Xvfb, but AWSIM's LiDAR computation depends on the GPU
     (Vulkan). The environment variable `VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.json` makes the GPU visible even with the
     virtual display, so the point cloud is generated correctly.
   - **Starting AWSIM normally**: AWSIM's `-batchmode` (no display) causes missing sensor data, so it is not used; AWSIM is started
     normally against Xvfb, which gives a correct simulation.
4. **Isolating logs and preventing hangs (`run_manager.py`)**
   So that the huge standard output of AWSIM and Autoware does not fill pipes and freeze the processes, each output is redirected to its
   own file, `awsim.log` and `autoware.log`. The worker's progress can be monitored in real time with Python's unbuffered output (the
   `-u` option).

## 3D plots of results

The plotting code now lives in `tools/plot/*`. The `visualize_*.py` scripts at the repository root are kept as legacy wrappers that call
`tools/plot/*`.

```bash
# Plot the latest results
python3 tools/plot/visualize_traces.py ~/simulation_traces

# Plot a specific archived dataset
python3 tools/plot/visualize_traces.py ~/simulation_traces_shared_20260512_144346

# 3D plot of severity from continuous MIN_TTC values (the extractor's values take precedence over Maude's flags)
python3 tools/plot/visualize_min_ttc_3d.py ~/simulation_traces

# 3D plot of the risk matrix
python3 tools/plot/visualize_risk_matrix.py ~/simulation_traces

# Collision / non-collision cells as voxel regions
python3 tools/plot/visualize_collision_regions.py ~/simulation_traces

# The collision boundary from real data and the boundary learned by the AI as smooth surfaces
python3 tools/plot/visualize_collision_surfaces.py ~/simulation_traces

# Match the original 1.7.1 cases and the 1.8.0 replay by case number and plot the differences
python3 -m tools.plot.visualize_version_comparison \
  ~/simulation_traces_shared_20260911_125415/uturn_dataset_success.csv \
  ~/simulation_traces_autoware180_replay_20260911/uturn_dataset.csv \
  --output ~/simulation_traces_autoware180_replay_20260911/autoware171_vs_180_comparison.png
```

## Real-time monitoring and troubleshooting

Commands to monitor, from another terminal on the head node, the simulations running inside a worker machine (for example, node 23).

### 1. Worker progress log (main worker log)

Shows which loop is running and whether the worker restarted because of an error.

```bash
# Example: node 23
ssh worker2@192.0.2.23 "docker exec sim_worker_23 tail -f /home/passd/simulation_traces/worker_log_sim_worker_23.txt"
```

### 2. Infrastructure logs (detailed error investigation)

Use these to check whether the simulator or Autoware started correctly.

```bash
ssh worker2@192.0.2.23 "docker exec sim_worker_23 tail -f /home/passd/simulation_traces/awsim.log"
ssh worker2@192.0.2.23 "docker exec sim_worker_23 tail -f /home/passd/simulation_traces/autoware.log"
```

### Manual debugging inside a container

If simulations do not work on a particular node, you can enter a container with exactly the same environment as the system by the
following steps and find where the error occurs.

See [docs/network_storm_control_guide.md](network_storm_control_guide.md#english) for how to start `bridge` containers by hand.

```bash
docker exec -it autoware171-x11-map bash -i -c 'cd /home/passd/awsim_labs && ./awsim_labs.x86_64 -noise false'
docker exec -it autoware171-x11-map bash -i -c 'source /home/passd/autoware/install/setup.bash && cd /home/passd/autoware && ros2 launch autoware_launch e2e_simulator.launch.xml vehicle_model:=awsim_labs_vehicle sensor_model:=awsim_labs_sensor_kit map_path:=/home/passd/autoware_map/nishishinjuku_autoware_map launch_vehicle_interface:=true rviz:=false'
```

Even if `bash -c` or `bash -lc` seems sufficient, use `bash -i -c` as the standard when running in containers.
