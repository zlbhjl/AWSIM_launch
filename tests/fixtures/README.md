# Test Fixtures

このディレクトリには、新実装の unit / regression / smoke テストで使う最小 fixture を置く。

## AWSIM

- `awsim/normal_trace_kinematics.json`
  - 切り出し元: `/home/passd/simulation_traces_host_20260720_090926/uturn_eval_sim6973.json`
  - 用途: `targets/awsim/kinematics_bridge.py`
  - 備考: 旧 `AWKinematicsPipeline` 互換の fixture

- `awsim/normal_trace_maude.json`
  - 切り出し元: `/home/passd/simulation_traces_host_20260720_090926/uturn_eval_sim1.json`
  - 用途: `verifiers/maude/*`, `targets/awsim/result_interpreter.py`
  - 備考: 旧 `aw_checkerpy.py` 互換の fixture

- `awsim/timeout_trace.txt`
  - 内容: `TIMEOUT`
  - 用途: timeout ハンドリングの回帰確認
  - 旧実装基準: `run_manager.py` が timeout 時に書くダミー内容

- `awsim/maude_failure_missing_vehicle_sizes.json`
  - 切り出し元: `awsim/normal_trace_maude.json` を最小化し、`groundtruth_size.vehicle_sizes` を欠落させた派生 fixture
  - 用途: `verifiers/maude/*`, `targets/awsim/result_interpreter.py`
  - 備考: 旧 `aw_checkerpy.py` が `groundtruth_size["vehicle_sizes"]` に依存するため、`analysis_error` 回帰確認に使う

- `awsim/empty_trace_with_vehicle_sizes.json`
  - 切り出し元: `awsim/normal_trace_maude.json` の `vehicle_sizes` だけを残し、時系列配列を空にした最小 fixture
  - 用途: `targets/awsim/result_interpreter.py`
  - 備考: 有効 JSON だが時系列が空のため、`invalid` と `analysis_error` の境界確認に使う

## BBSL

- `bbsl/experiment_all_raw_result_mini.json`
  - 切り出し元: `/home/passd/BBSL-test/output/experiment_all_raw_result_mini.json`
  - 用途: `targets/bbsl/result_interpreter.py`, `targets/bbsl/dataset_adapter.py`

- `bbsl/clean_baseline.json`
  - 切り出し元: `/home/passd/BBSL-test/output/batches/clean_baseline.json`
  - 用途: batch 系 FT4D 回帰比較

- `bbsl/noisy_batch_0001.json`
  - 切り出し元: `/home/passd/BBSL-test/output/batches/noisy_batch_0001.json`
  - 用途: batch 集約・`verification_input.py` 回帰比較

- `bbsl/invalid_missing_bbsl_results.json`
  - 切り出し元: `bbsl/experiment_all_raw_result_mini.json` を最小化し、`bbsl_results` を欠落させた派生 fixture
  - 用途: `targets/bbsl/result_interpreter.py`
  - 備考: 必須キー欠落時の `analysis_error` 確認に使う

- `bbsl/empty_experiment_all_raw_result.json`
  - 切り出し元: `bbsl/experiment_all_raw_result_mini.json` を最小化し、dataset と結果配列を空にした派生 fixture
  - 用途: `targets/bbsl/result_interpreter.py`, `targets/bbsl/verification_input.py`
  - 備考: 必須キーはあるがデータが空のときの `invalid` / 空集合処理確認に使う

## ルール

- 可能な限り旧コードが実際に読んでいた入力・出力から切り出す
- fixture を差し替える場合は、切り出し元パスもここへ追記する
- regression 比較では、旧コードと新コードに同じ fixture を入力する
