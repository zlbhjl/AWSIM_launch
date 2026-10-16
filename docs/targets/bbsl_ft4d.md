# BBSL / FT4D 連携

[BBSL-test](https://github.com/IOKENTOI/BBSL-test) (画像認識へのノイズ付与・推論・判定を行う実験エンジン) の
raw result を、AWSIM_launch 内の FT4D コア (`verification_core/ft4d`) で評価します。
以下の例の `--target-repo` は、BBSL-test を clone したディレクトリに読み替えてください。

## legacy 互換入口から起動する
この入口は、旧構成との互換を保つための legacy CLI です。
新設計では BBSL の本体は `targets/bbsl/` 側へ寄せ、ここは移行期の互換入口として残します。
現在の `bbsl_ft4d` アダプタは、BBSL 側の古い実験スクリプトを直接たたくのではなく、
legacy 入口の形を保ったまま AWSIM_launch 側の新経路を in-process で呼びます。
つまり実際の FT4D 評価は AWSIM_launch 側の
`targets/bbsl/result_interpreter.py -> targets/bbsl/verification_input.py -> evaluation/ft4d_service.py`
で行います。

```bash
# BBSL-test の FT4D 実験を AWSIM_launch 側から legacy 互換CLIで起動
python3 run_external_verifier.py \
  --verifier bbsl_ft4d \
  --target-repo /home/passd/BBSL-test \
  --reuse-existing-output \
  --tree basic \
  --sigma-pf-source dataset \
  --sigma-pb-mode delta-clean \
  --and-rule min

# 軽い疎通確認
python3 run_external_verifier.py \
  --verifier bbsl_ft4d \
  --target-repo /home/passd/BBSL-test \
  --reuse-existing-output \
  --mini \
  --max-images 3 \
  --tree basic \
  --sigma-pf-source dataset \
  --sigma-pb-mode delta-clean \
  --and-rule min
```

このコマンドは AWSIM_launch 内に正規化済みの結果 JSON を保存しつつ、
内部では `run_bbsl_local_ft4d.py` を呼びます。
つまりこの経路は、

- AWSIM_launch = legacy 互換入口として検証器を起動し、新経路で FT4D を評価するフレーム
- BBSL-test = raw result を生成する実験エンジン

という役割分担です。

## BBSL の raw result を FT4D で再計算する (推奨)
こちらが現在の推奨経路です。外部の `BBSL-test` でノイズ生成・推論・BBSL判定を行い、
その raw result JSON を AWSIM_launch 側で
`targets/bbsl/result_interpreter.py -> targets/bbsl/verification_input.py -> evaluation/ft4d_service.py`
へ流して FT4D を計算し直します。

```bash
# 軽い確認
python3 run_bbsl_local_ft4d.py \
  --target-repo /home/passd/BBSL-test \
  --reuse-existing-output \
  --execution-mode legacy \
  --mini \
  --max-images 3 \
  --tree basic \
  --sigma-pf-source dataset \
  --sigma-pb-mode delta-clean \
  --and-rule min

# batch-loop を fresh で最初から回す
python3 run_bbsl_local_ft4d.py \
  --target-repo /home/passd/BBSL-test \
  --execution-mode batch-loop \
  --run-mode fresh \
  --mini \
  --max-images 3 \
  --tree basic \
  --sigma-pf-source dataset \
  --sigma-pb-mode delta-clean \
  --and-rule min

# 既存 clean baseline / noisy batch を引き継いで続きから確認
python3 run_bbsl_local_ft4d.py \
  --target-repo /home/passd/BBSL-test \
  --mini \
  --execution-mode batch-loop \
  --run-mode resume \
  --tree basic \
  --sigma-pf-source dataset \
  --sigma-pb-mode delta-clean \
  --and-rule min \
  --max-batches 1

# sigma_pf を外部設定で与える例
python3 run_bbsl_local_ft4d.py \
  --target-repo /home/passd/BBSL-test \
  --execution-mode batch-loop \
  --run-mode fresh \
  --tree basic \
  --sigma-pf-value SALT_PEPPER=0.12 \
  --sigma-pf-value OCCLUSION=0.03 \
  --sigma-pf-value BLUR=0.08 \
  --sigma-pb-mode delta-clean \
  --and-rule min
```

この batch-loop 経路では:

- `clean` は最初に1回だけ実行
- `clean` で成功した画像だけに noisy 条件をかける
- `BBSL-test/output/batches/` に baseline / batch raw result を保存
- `--run-mode fresh` のときは、開始前に `output/batches/*.json` と
  `output/*.png` と `data/kitti/ft4d_batches/` の生成済みノイズ画像を削除する
- `--run-mode resume` のときは、既存の baseline / noisy batch を見て続きから再開する
- `--sigma-pf-value EVENT_ID=value` または `--sigma-pf-json path.json` を渡すと、
  AWSIM_launch 側の FT4D 再計算で `sigma_pf` 仮定値を上書きできる
- `AWSIM_launch` 側で複数 batch を束ねて `U / D(n) / E(n)` を再構成
- Chernoff-Hoeffding の必要サンプル数や信頼度が足りなければ次 batch を追加

`sigma_pf` 上書きが指定された場合は、`--sigma-pf-source dataset` を指定していても
AWSIM_launch 側では `assumption` 扱いで再計算します。

現在は

- `--execution-mode legacy`
- `--execution-mode batch-loop --condition-policy all`
- `--execution-mode batch-loop --condition-policy underconfident`

の主要経路が、最終的に AWSIM_launch 側の新評価経路へそろっています。
FT4D の計算本体、Bonferroni に基づく `recognition_test` の再計算、
confidence gap の集約は AWSIM_launch 側で実行します。

役割分担は次のとおりです。

- `BBSL-test` = 実験エンジン
  - ノイズ生成
  - 推論
  - 正解比較
  - raw result 保存
- `AWSIM_launch` = 検証コア
  - FT4D
  - `recognition_test` 再構成
  - confidence gap 集約
  - 将来の探索判断

### BBSL 側で raw result だけを作るコマンド
AWSIM_launch へ渡す前段として、BBSL 側単独でも次のように raw result を作れます。

```bash
# clean baseline
python3 /home/passd/BBSL-test/examples/run_ft4d_batch_experiment.py \
  --mode clean-baseline \
  --mini \
  --max-images 4

# noisy batch
python3 /home/passd/BBSL-test/examples/run_ft4d_batch_experiment.py \
  --mode noisy-batch \
  --mini \
  --batch-id 1 \
  --master-seed 1000

# 同一画像で全条件比較する経路
python3 /home/passd/BBSL-test/examples/run_full_experiment_all.py \
  --mini \
  --max-images 3 \
  --tree basic \
  --sigma-pf-source dataset \
  --sigma-pb-mode delta-clean \
  --and-rule min \
  --ft4d-backend none

# 条件ごとに画像を分割する従来経路
python3 /home/passd/BBSL-test/examples/run_full_experiment.py \
  --mini \
  --max-images 6 \
  --tree basic \
  --sigma-pf-source dataset \
  --sigma-pb-mode raw \
  --and-rule min \
  --ft4d-backend none
```

生成される主なファイルは次です。

- `output/batches/clean_baseline.json`
- `output/batches/clean_success_image_ids.json`
- `output/batches/noisy_batch_0001.json`
- `output/experiment_all_raw_result.json`
- `output/experiment_all_raw_result_mini.json`
- `output/experiment_full_raw_result.json`
- `output/experiment_full_raw_result_mini.json`

`run_bbsl_local_ft4d.py --execution-mode batch-loop` は
`output/batches/` を使い、`--execution-mode legacy` では
`experiment_*_raw_result*.json` を読みます。

## FT4D コアを単独で確認する
このコマンドは、まず AWSIM_launch 内だけで FT4D コアが成立しているかを確認するためのものです。

```bash
python3 run_ft4d_smoke.py
```

これは `tests/fixtures/awsim/normal_trace_maude.json` を
`targets/awsim/result_interpreter.py -> targets/awsim/verification_input.py -> evaluation/ft4d_service.py`
へ通し、`verification_core/ft4d/config/awsim_demo_tree.json` を用いて FT4D を計算します。
現在は AWSIM の `EvaluationRecord.output` に `c_collision`, `c_ttc_*` などの Maude 判定値が入り、
その値を使って smoke を確認します。

同じ確認を PRISM 側でも行えます。

```bash
python3 run_prism_ft4d_smoke.py
```

これは `tests/fixtures/prism/prism_stage2_baseline_sample41.jsonl`
(`2026-09-15` の実クラスタ検証 baseline 条件から抜粋した実データ、exact 1件 + sample 40件)を
`targets/prism/verification_input.py::build_verification_input_from_records -> evaluation/ft4d_service.py`
へ通し、`verification_core/ft4d/config/tree_prism_demo.json` を用いて FT4D を計算します。
複数の PRISM サンプル経路をまとめて1つの母集団として集約し、
`FAILURE` / `EARLY_FAILURE` / `REPEATED_DEGRADATION` それぞれの違反率と、
それらを OR で束ねた `top_sigma_pe` を表示します。
`--records-jsonl` で任意の PRISM 実験 JSONL(`run_orchestrator_cluster_v2.py --target prism` の
`--output`成果物など)を指定すれば、その実験データに対して同じ集約を実行できます。
