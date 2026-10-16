# 連続ダイナミクス (ODE/SDE) 対象

`--target dynamics --case-kind uturn` は、AWSIMのUターンと同じ入力（`dx0`・`ego_speed`・`npc_speed`）を
連続モデルで計算する対象です。AWSIM・Autoware・GPUは使わず、1件あたり1秒未満で終わります。
設計は [docs/dynamics_target_design.md](../dynamics_target_design.md) を参照してください。

使い方は2系統あります。

- **単独モード**: ODE/SDEそのものを統計的に評価します。AWSIMの結果は混ぜません。
  既定の制動はJAMA ai_aeb profileです。Autoware校正済みの制動を使う場合は
  `--param controller_kind=autoware171_uturn_calibrated` を付けます。
- **AWSIM比較モード**: 同じ入力をODEとAWSIMの両方に渡して結果を比べます。
  ODEはAutoware 1.7.1のtraceから校正した開始状態・NPC旋回・制動を使います。

## 汎用モデルとUターンへの具体化

モデルはすべて、離散モード $q$ を持つハイブリッド確率微分方程式として書きます。

```text
dx = f_q(t, x) dt + G_q(t, x) dW_t        （G_q = 0 なら ODE）
ガード g(t, x) が 0 を横切ったら、リセット x+ = r(x-) を適用してモード q -> q' へ切り替える
```

- `targets/dynamics/models/base.py`: モデルが実装するインターフェース `HybridSystem`
  （状態名、初期モード・状態、ドリフト $f_q$、拡散 $G_q$、ガードとリセット、射影）
- `targets/dynamics/runners/ode.py`: モードごとの `solve_ivp`。ガードは終端イベントとして根を求めます。
- `targets/dynamics/runners/sde.py`: 固定刻みEuler--Maruyama。ノイズがなければODE runnerに委譲します。

新しいモデル（車線変更など）は `HybridSystem` を実装すれば、runner・統計モード・保存経路をそのまま使えます。
Uターンはその一実装 `UTurnSystem`（[models/uturn.py](../../targets/dynamics/models/uturn.py)）です。
モードは `lane` → `turn` → `exit` で、式は [設計書 6.2節](../dynamics_target_design.md) にあります。

## Uターンモデルの中身

時刻0は、AWSIMでNPCがUターンを始めた瞬間（`dx0` 到達）に揃えています。

- 開始時の相対位置・速度・方位: `dx0`・ego速度・NPC速度からの線形校正
- NPCの旋回: 姿勢原点が半径約3.35 m・約176.6°の円弧をたどる。幾何中心はその1.12 m前方
- egoの制動: 遅れ0.616 s、立上り0.095 s、減速度3.013 m/s²（速度系列への曲線当てはめ）
- 衝突判定: 車体寸法を使った向き付き矩形（OBB）の重なり
- TTC: 5秒先まで0.1秒刻みの等速予測でOBBが重なる最初の時刻

校正値は `targets/dynamics/calibrations/` に置いています。各実行結果の `execution` metadata には、
解決後の初期状態・半径・旋回角・制動profile・校正元が保存されます。

## 2つの判定モード

同じ軌跡から、2つの判定を常に出力します。

| モード | 出力列 | 判定 | 用途 |
|---|---|---|---|
| 判定（judgment） | `c_collision` | 車体が重なったら衝突 | AWSIMの代わりの判定 |
| スクリーニング（screening） | `c_screening_candidate` | 車体間の隙間 `min_clearance` が1.1 m未満 | AWSIMで再検証する候補の抽出 |

Autoware 1.7.1で、結果を一度も見ていない100件（衝突50・非衝突50）を1回だけ評価した結果は次のとおりです。

| モード | 衝突の検出 | 見逃し | 非衝突の正判定率 | 正解率 |
|---|---|---|---|---|
| 判定 | 49/50 | 1 | 92% | 95% |
| スクリーニング | 50/50 | 0 | 72% | 86% |

連続TTCの平均誤差は0.039秒でした。見逃し0/50は見逃し率0を意味しません（95%上側信頼限界は約6%）。
検証は `ego lane=514, offset=38` の配置だけで行っています。余裕幅1.1 mはAutoware校正済みの制動に対して
決めた値なので、JAMA制動で使った場合は `screening_provenance.calibrated_for_current_controller=false` が記録されます。
詳細は [docs/dynamics_awsim_decision_modes_20261005.md](../dynamics_awsim_decision_modes_20261005.md) と
[docs/dynamics_awsim_final_validation_v2_20261005.md](../dynamics_awsim_final_validation_v2_20261005.md) を参照してください。

## 実行例

固定パラメータで1件だけ計算する場合:

```bash
python3 run_orchestrator_v2.py \
  --target dynamics --case-kind uturn \
  --param dx0=15 --param ego_speed=36 --param npc_speed=18 \
  --output artifacts/dynamics/records.jsonl \
  --dataset-csv artifacts/dynamics/dataset.csv \
  --dynamics-output-root artifacts/dynamics/traces
```

入力を一様分布から引き、衝突確率を二項信頼区間で評価する場合（スクリーニング指標を使う例）:

```bash
python3 run_orchestrator_v2.py \
  --target dynamics --case-kind uturn \
  --mode binomial_ci --max-samples 1000 --seed 42 \
  --dynamics-decision-mode screening \
  --output artifacts/dynamics/stat.jsonl \
  --dataset-csv artifacts/dynamics/stat.csv
```

- `--dynamics-decision-mode` は `binomial_ci` / `sprt` の2値指標を選びます（既定 `judgment` = `c_collision`）。
  `dkw` / `dkw_fixed` / `ebstop` は常に `min_ttc` を使います。
- 分布を指定しない場合、固定した `--param` 以外を `scenario_specs/uturn.py` の範囲から独立一様に引きます。
  `--dynamics-input-distribution '{"dx0":{"distribution":"uniform","min":10,"max":25}}'` で指定できます。
- 各行には seed・分布・実際に引いた値が残り、`<output>.summary.json` に統計レポート・停止理由・
  `dynamics_decision_mode` が保存されます。

1件をODEとAWSIMの両方で実行して比べる場合（AWSIMが動く環境が必要です）:

```bash
python3 run_dynamics_awsim_compare_v2.py \
  --dx0 15 --ego-speed 36 --npc-speed 18 \
  --decision-mode screening \
  --output artifacts/dynamics_compare/compare.jsonl
```

比較レコードには `dynamics_decision`、`decision_agree`、`decision_missed_awsim_collision` が入ります。
AWSIM側が失敗した場合は「不一致」ではなく `revalidation_unavailable` として保存されます。

保存済みのAWSIM traceとまとめて照合する場合:

```bash
TD=/home/passd/simulation_traces_sim_worker_21_20260911_125415
python3 tools/analysis/validate_dynamics_against_awsim.py \
  --trace-dir $TD --records-jsonl $TD/uturn_records.jsonl \
  --calibration-report artifacts/autoware171_uturn_screening_holdout_split_20261005.json \
  --sources-key final_validation_sources \
  --output-dir artifacts/my_validation
```

この照合ツールは、AWSIM側のTTCもODEと同じコードで計算し直します（`awsim_min_ttc`）。
`AW_Kinematics_Extractor` CVMの値は `awsim_min_ttc_extractor` 列に参考として残ります。
このextractorはワールド座標系の `twist` をyawでもう一度回転させるため、比較の基準には使いません。

## 校正のやり直し

2つの校正ツール（`tools/analysis/`）は、ODEをAWSIMの実際の挙動に合わせ直すためのものです。
普段の実験で毎回動かすものではなく、AWSIM側の挙動が変わったときに動かします。

- `calibrate_autoware_uturn_braking.py`: ego制動の遅れ・立上り・減速度。トリガー後2.5秒の速度系列に
  制動曲線を最小二乗で当てはめ、校正用traceの中央値を取ります。
- `calibrate_uturn_start_geometry.py`: Uターン開始時の相対位置・速度・方位と、NPCの旋回半径・旋回角。
  入力（`dx0`・ego速度・NPC速度）の線形式として最小二乗で当てはめます。最終評価用の集合の選定も行います。

### どういう状況で使うか

| 状況 | 例 | 動かすツール |
|---|---|---|
| ego車の制動の仕方が変わった | Autowareのバージョン変更、制御パラメータの変更 | 制動の校正（その後、開始状態の校正も） |
| NPCの動き・配置が変わった | シナリオのlane/offset変更、waypoint生成の変更、AWSIM更新 | 開始状態とNPC旋回の校正 |
| 検証でずれが見つかった | 誤検出の増加、TTCの系統的なずれ | 原因に応じてどちらか |
| データを増やして推定を安定させたい | 新しいAWSIM実験のtraceが溜まった | 両方 |

**使ってはいけない使い方**: 最終評価の数字を良くするために、評価集合を見ながら何度も校正し直すこと。
評価の意味がなくなります。校正し直したら、未使用の集合で評価し直してください。

### 使い方（手順と順番）

AWSIMのtraceディレクトリ（`uturn_eval_sim*.json` と `uturn_records.jsonl`）が必要です。

**手順1: 制動を校正する**（校正用・確認用のデータ分割もここで決まります）

```bash
TD=/home/passd/simulation_traces_sim_worker_21_20260911_125415
python3 tools/analysis/calibrate_autoware_uturn_braking.py $TD \
  --output artifacts/autoware171_uturn_braking_calibration_v2.json \
  --max-files 200 --calibration-fraction 0.7 --seed 20261005
```

推定結果（遅れ・立上り・減速度）と確認用traceでの誤差が表示されます。同じseedなら同じ分割になります。

**手順2: 開始状態とNPC旋回を校正し、最終評価用の集合を選ぶ**

```bash
python3 tools/analysis/calibrate_uturn_start_geometry.py \
  --trace-dir $TD --records-jsonl $TD/uturn_records.jsonl \
  --split-report artifacts/autoware171_uturn_braking_calibration_v2.json \
  --exclude-report artifacts/autoware171_uturn_start_geometry_calibration.json \
  --exclude-report artifacts/autoware171_uturn_start_geometry_calibration_v2.json \
  --seed 20261008 \
  --output artifacts/<新しい分割>.json
```

- `--split-report`: 手順1と同じ校正用・確認用の分割を使います。
- `--exclude-report`: 結果を見たことのある評価集合を除外します。複数指定できます。
- `--seed`: 最終評価の100件（衝突50・非衝突50）を選ぶ乱数です。毎回新しい値にします。

**手順3: 結果をODEに反映する**（現状は手作業）

ツールは推定結果をファイルに出すだけです。ODEが読む同梱ファイルへは手でコピーします。

- 制動: `profile` などを `targets/dynamics/calibrations/autoware171_uturn_braking.json` へ
- 開始状態: `coefficients` などを `targets/dynamics/calibrations/autoware171_uturn_start_geometry.json` へ

**手順4: 開発用データで確認する**

```bash
python3 tools/analysis/validate_dynamics_against_awsim.py \
  --trace-dir $TD --records-jsonl $TD/uturn_records.jsonl \
  --calibration-report artifacts/<新しい分割>.json \
  --sources-key development_evaluation_sources \
  --output-dir artifacts/<開発確認の出力先>
```

ここでは何度試してもかまいません。モデルや校正方法を変えるのはこの段階です。

**手順5: 余裕幅を決め直す**（現状は手作業）

開発用データで「AWSIMで衝突したのにODEの隙間が最大だった値 + 0.1 m」を求め、
`targets/dynamics/calibrations/autoware171_uturn_screening.json` を更新します。

**手順6: 最終評価を1回だけ行う**

手順4のコマンドの `--sources-key` を `final_validation_sources` に変えて実行します。

### 現状の制約

- 同梱ファイルへの反映（手順3）と余裕幅の計算（手順5）は自動化していません。
- 同梱ファイル名と設定名はAutoware 1.7.1用です。1.8.0や1.9.0を校正すると1.7.1の値を上書きします。
  複数バージョンを並べて使うには、バージョンごとに校正ファイルを持てるようにコードを変える必要があります。

## SDE

`--param` で `solver_kind=sde`・`sde_seed`・`sde_dt_sec`・`sde_noise` を渡すと、制動・NPC加速度・NPC方位にノイズを加えられます。

```bash
python3 run_worker_v2.py \
  --target dynamics --case-kind uturn \
  --param dx0=12 --param ego_speed=36 --param npc_speed=18 \
  --param solver_kind=sde --param sde_seed=3 \
  --param 'sde_noise={"ego_brake_acceleration_std":0.25}' \
  --output artifacts/dynamics/sde.jsonl --dataset-csv artifacts/dynamics/sde.csv
```

ノイズがすべて0の場合はODEと同一の結果になります。ノイズの大きさはまだ実データから校正していないため、
SDEの衝突率は感度分析として扱い、現実の確率とは解釈しないでください。
