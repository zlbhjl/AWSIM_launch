# Autoware 1.7.1 / 1.8.0 / 1.9.0 の replay 実験

研究で実施した、同じ入力を Autoware の複数バージョンで再実行して結果を比べる実験の記録です。
件数・パス・日付は実施当時のものです。`replay` モードの使い方の例として参照してください。

## 1.7.1の二項標本を1.8.0で再実行する

`replay`モードは、入力CSVから次の条件をすべて満たす行だけを元の行順で再実行します。

- `status=success`
- `reason`に`BINOMIAL_CI:`を含む
- `c_collision`が`0`または`1`

`uturn_dataset_success.csv`の全`10,309件`ではなく、実際に1.7.1の二項信頼区間へ
採用された`9,066件`だけが比較対象です。件数が変わっていた場合は実験を開始しないよう、
`--replay-expected-count 9066`を指定します。

```bash
cd /home/passd/AWSIM_launch

python3 run_orchestrator_cluster_v2.py \
  --output /home/passd/simulation_traces_autoware180_replay_20260911/uturn_records.jsonl \
  --dataset-csv /home/passd/simulation_traces_autoware180_replay_20260911/uturn_dataset.csv \
  --case-kind uturn \
  --mode replay \
  --replay-csv /home/passd/simulation_traces_shared_20260911_125415/uturn_dataset_success.csv \
  --replay-expected-count 9066 \
  --run-id autoware180_replay_20260911 \
  --container-profile autoware180_ekfdiagfix \
  --scenario-profile autoware171 \
  --headless \
  --sync-autoware180-map \
  --auto-restart-stale-workers \
  --auto-restart-missing-workers \
  --auto-restart-gpu-workers
```

`--run-id`は各号機のtrace mount先を別ディレクトリに分離し、1.7.1の既存traceを
上書きしないために必須です。1.8.0の結果には`meta_replay_source_loop_num`、
`meta_replay_source_case_id`、`meta_replay_source_collision`、`meta_replay_source_reason`が入り、
元試行と一対一で比較できます。
再実行先で発生した`timeout`や`execution_error`もバージョン差なので削除しません。同じ出力CSVで
再開した場合は、すでに記録済みの`meta_replay_source_loop_num`を自動的に除外します。

## 1.7.1 / 1.8.0 の両方で有効だった標本を 1.9.0 で再実行する

1.9.0 では、1.7.1 の二項標本 `9,066件` のうち 1.8.0 replay でも `status=success` かつ
`c_collision` が `0` / `1` だった `8,914件` だけを再実行します。1.7.1 の無効試行
(`execution_error` / `timeout` / `analysis_error` の `8,110件`) と、1.8.0 側で無効になった `152件` は含めません。
入力CSVは元の`loop_num`を保持しているため、3バージョンを同じケース番号で対応付けられます。
抽出条件とSHA-256は同じディレクトリの `*_manifest.json` に残しています。

```bash
cd /home/passd/AWSIM_launch

python3 run_orchestrator_cluster_v2.py \
  --output /home/passd/simulation_traces_autoware190_replay_20261002/uturn_records.jsonl \
  --dataset-csv /home/passd/simulation_traces_autoware190_replay_20261002/uturn_dataset.csv \
  --case-kind uturn \
  --mode replay \
  --replay-csv /home/passd/simulation_traces_autoware190_replay_input/uturn_replay_input_valid_171_180.csv \
  --replay-expected-count 8914 \
  --run-id autoware190_replay_20261002 \
  --container-profile autoware190_ekfdiagfix \
  --scenario-profile autoware171 \
  --headless \
  --sync-autoware190-map \
  --auto-restart-stale-workers \
  --auto-restart-missing-workers \
  --auto-restart-gpu-workers
```

## バージョン差と確率的変動を反復検証する準備

反転したケースと固定seedの無作為対照群を選び、5反復ずつ実行するためのCSV、manifest、
手動コマンド一覧を一括生成します。生成コマンドは実験を開始しません。

```bash
python3 -m tools.analysis.prepare_version_repeat \
  --source-csv /home/passd/simulation_traces_shared_20260911_125415/uturn_dataset_success.csv \
  --replay-csv /home/passd/simulation_traces_autoware180_replay_20260911/uturn_dataset.csv \
  --output-dir /home/passd/simulation_traces_version_repeat_20260913 \
  --random-count 500 \
  --seed 171180
```

実験は出力された`manual_commands.md`の順番で1ブロックずつ手動実行します。各ブロックの
`run-id`と出力先は別になっているため、反復結果は上書きされません。中断したブロックだけは、
同じコマンドを再入力すると完了済みケースを除外して再開します。

## 1.8.0 の運用入力の配置記録 (`2026-09-01` 確認)

- `AWSIMScriptPy`、`autoware180_runtime/maps`、`autoware180_runtime/ml_models`、`cyclonedds.xml` は 22/23/24号機へ配置済みです。
- `cyclonedds.xml` は 3台とも `026239b408bfde21dfbc136b4ab84c12588c8571314e0f0c34d7826c5fd360be` で一致しています。
- `autoware180_runtime/maps` は 3台とも `223M`、`69` files で一致しています。
- `autoware180_runtime/ml_models` は 3台とも `3.7G`、`198` files で一致しています。
- `autoware_internal:1.8.0-ekfdiagfix` は `numpy==1.24.4`、`scikit-learn==1.7.2`、`ray==2.55.0`、`typing_extensions==4.15.0` を含む image として rebuild し、22/23/24号機へ再配布済みです。
- 依存確認では 22/23/24号機で `numpy=1.24.4 scipy=1.8.0 sklearn=1.7.2 ray=2.55.0` の import が通っています。
