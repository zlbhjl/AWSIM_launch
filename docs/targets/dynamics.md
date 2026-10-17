# 連続ダイナミクス (ODE/SDE) 対象

The continuous dynamics (ODE/SDE) target. **[English version below ↓](#english)**

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

人間が指定した固定Qで入力を偏らせ、P/Q重み付きで固定標本比較を行う場合は、
[重点サンプリングの使い方](../importance_sampling.md#微分方程式の入力重点サンプリング固定p固定q) を参照してください。
ここでの通常の統計モードとは別のCLI・保存形式を使用します。

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

---

<a id="english"></a>

# English: Continuous dynamics (ODE/SDE) target

`--target dynamics --case-kind uturn` computes the same inputs as the AWSIM U-turn scenario (`dx0`, `ego_speed`, `npc_speed`)
with a continuous model. It does not use AWSIM, Autoware, or a GPU, and each case finishes in less than a second.
See [docs/dynamics_target_design.md](../dynamics_target_design.md) for the design (Japanese only).

There are two ways to use it.

- **Standalone mode**: evaluates the ODE/SDE itself statistically, without mixing in AWSIM results.
  The default braking is the JAMA ai_aeb profile. To use braking calibrated to Autoware, add
  `--param controller_kind=autoware171_uturn_calibrated`.
- **AWSIM comparison mode**: gives the same inputs to both the ODE and AWSIM and compares the results.
  The ODE uses the start state, NPC turn, and braking calibrated from Autoware 1.7.1 traces.

## Generic model and its U-turn instance

Every model is written as a hybrid stochastic differential equation with discrete modes $q$.

```text
dx = f_q(t, x) dt + G_q(t, x) dW_t        (an ODE when G_q = 0)
When the guard g(t, x) crosses 0, apply the reset x+ = r(x-) and switch mode q -> q'
```

- `targets/dynamics/models/base.py`: the `HybridSystem` interface that models implement
  (state names, initial mode and state, drift $f_q$, diffusion $G_q$, guards and resets, projections)
- `targets/dynamics/runners/ode.py`: `solve_ivp` per mode. Guards are located as terminal events.
- `targets/dynamics/runners/sde.py`: fixed-step Euler–Maruyama. Without noise it delegates to the ODE runner.

A new model (such as a lane change) only needs to implement `HybridSystem` to reuse the runners, statistical modes, and storage.
The U-turn is one implementation, `UTurnSystem` ([models/uturn.py](../../targets/dynamics/models/uturn.py)).
Its modes are `lane` → `turn` → `exit`; the equations are in [section 6.2 of the design document](../dynamics_target_design.md) (Japanese only).

## What the U-turn model contains

Time 0 is aligned with the moment the NPC starts its U-turn in AWSIM (when `dx0` is reached).

- Relative position, speed, and heading at the start: linear calibration from `dx0`, the ego speed, and the NPC speed
- NPC turn: the pose origin follows an arc of radius about 3.35 m over about 176.6°; the geometric center is 1.12 m ahead of it
- Ego braking: delay 0.616 s, rise time 0.095 s, deceleration 3.013 m/s² (curve fit to speed series)
- Collision check: overlap of oriented bounding boxes (OBB) using the vehicle dimensions
- TTC: the first time the OBBs overlap in a constant-velocity prediction up to 5 s ahead in 0.1 s steps

The calibration values are in `targets/dynamics/calibrations/`. The `execution` metadata of each result stores the resolved
initial state, radius, turn angle, braking profile, and the calibration source.

## Two decision modes

Two decisions are always produced from the same trajectory.

| Mode | Output column | Decision | Purpose |
|---|---|---|---|
| Judgment | `c_collision` | Collision when the vehicle boxes overlap | A substitute for the AWSIM judgment |
| Screening | `c_screening_candidate` | The gap between vehicles `min_clearance` is below 1.1 m | Selecting candidates to re-check on AWSIM |

On Autoware 1.7.1, a single evaluation of 100 cases never looked at before (50 collisions, 50 non-collisions) gave:

| Mode | Collisions detected | Misses | Correct non-collision rate | Accuracy |
|---|---|---|---|---|
| Judgment | 49/50 | 1 | 92% | 95% |
| Screening | 50/50 | 0 | 72% | 86% |

The mean error of the continuous TTC was 0.039 s. Zero misses out of 50 does not mean a miss rate of 0 (the 95% upper confidence
bound is about 6%). The validation used only the `ego lane=514, offset=38` placement. The 1.1 m margin was chosen for the braking
calibrated to Autoware, so when it is used with JAMA braking, `screening_provenance.calibrated_for_current_controller=false` is recorded.
See [docs/dynamics_awsim_decision_modes_20261005.md](../dynamics_awsim_decision_modes_20261005.md) and
[docs/dynamics_awsim_final_validation_v2_20261005.md](../dynamics_awsim_final_validation_v2_20261005.md) for details (Japanese only).

## Examples

To bias the inputs with a human-specified fixed Q and compare with P/Q weights on fixed samples, see
[Using importance sampling](../importance_sampling.md). It uses a different CLI and output format from the statistical modes here.

Compute a single case with fixed parameters:

```bash
python3 run_orchestrator_v2.py \
  --target dynamics --case-kind uturn \
  --param dx0=15 --param ego_speed=36 --param npc_speed=18 \
  --output artifacts/dynamics/records.jsonl \
  --dataset-csv artifacts/dynamics/dataset.csv \
  --dynamics-output-root artifacts/dynamics/traces
```

Draw inputs uniformly and evaluate the collision probability with a binomial confidence interval (using the screening metric):

```bash
python3 run_orchestrator_v2.py \
  --target dynamics --case-kind uturn \
  --mode binomial_ci --max-samples 1000 --seed 42 \
  --dynamics-decision-mode screening \
  --output artifacts/dynamics/stat.jsonl \
  --dataset-csv artifacts/dynamics/stat.csv
```

- `--dynamics-decision-mode` selects the binary metric for `binomial_ci` / `sprt` (default `judgment` = `c_collision`).
  `dkw` / `dkw_fixed` / `ebstop` always use `min_ttc`.
- Without a distribution, every input not fixed by `--param` is drawn independently and uniformly from the ranges in
  `scenario_specs/uturn.py`. Use `--dynamics-input-distribution '{"dx0":{"distribution":"uniform","min":10,"max":25}}'` to specify one.
- Each row keeps its seed, distribution, and the drawn values, and `<output>.summary.json` stores the statistical report, the stop
  reason, and `dynamics_decision_mode`.

Run one case on both the ODE and AWSIM and compare them (requires an environment where AWSIM runs):

```bash
python3 run_dynamics_awsim_compare_v2.py \
  --dx0 15 --ego-speed 36 --npc-speed 18 \
  --decision-mode screening \
  --output artifacts/dynamics_compare/compare.jsonl
```

Comparison records contain `dynamics_decision`, `decision_agree`, and `decision_missed_awsim_collision`.
When the AWSIM side fails, the record is saved as `revalidation_unavailable` rather than as a disagreement.

Check against saved AWSIM traces in bulk:

```bash
TD=/home/passd/simulation_traces_sim_worker_21_20260911_125415
python3 tools/analysis/validate_dynamics_against_awsim.py \
  --trace-dir $TD --records-jsonl $TD/uturn_records.jsonl \
  --calibration-report artifacts/autoware171_uturn_screening_holdout_split_20261005.json \
  --sources-key final_validation_sources \
  --output-dir artifacts/my_validation
```

This tool recomputes the AWSIM-side TTC with the same code as the ODE (`awsim_min_ttc`).
The CVM value from `AW_Kinematics_Extractor` is kept for reference in the `awsim_min_ttc_extractor` column.
That extractor rotates the world-frame `twist` by the yaw a second time, so it is not used as the reference for comparison.

## Recalibration

The two calibration tools (`tools/analysis/`) fit the ODE to the actual behavior of AWSIM.
They are not run for every experiment; run them when the behavior on the AWSIM side changes.

- `calibrate_autoware_uturn_braking.py`: delay, rise time, and deceleration of the ego braking. Fits a braking curve to the speed
  series for 2.5 s after the trigger by least squares and takes the median over the calibration traces.
- `calibrate_uturn_start_geometry.py`: relative position, speed, and heading at the start of the U-turn, and the NPC turn radius and
  angle. Fits them as linear functions of the inputs (`dx0`, ego speed, NPC speed) by least squares. It also selects the final
  evaluation set.

### When to use them

| Situation | Example | Tool to run |
|---|---|---|
| The ego braking changed | A new Autoware version, changed control parameters | Braking calibration (then the start-state calibration) |
| NPC behavior or placement changed | Changed scenario lane/offset, waypoint generation, AWSIM update | Start-state and NPC-turn calibration |
| Validation found a discrepancy | More false detections, a systematic TTC offset | Either one, depending on the cause |
| More data to stabilize the estimates | New AWSIM traces have accumulated | Both |

**Do not** recalibrate repeatedly while looking at the evaluation set to improve the final numbers. That makes the evaluation
meaningless. After recalibrating, evaluate again on an unused set.

### Steps (in order)

You need an AWSIM trace directory (`uturn_eval_sim*.json` and `uturn_records.jsonl`).

**Step 1: calibrate braking** (this also fixes the calibration/check split)

```bash
TD=/home/passd/simulation_traces_sim_worker_21_20260911_125415
python3 tools/analysis/calibrate_autoware_uturn_braking.py $TD \
  --output artifacts/autoware171_uturn_braking_calibration_v2.json \
  --max-files 200 --calibration-fraction 0.7 --seed 20261005
```

The estimates (delay, rise time, deceleration) and the errors on the check traces are printed. The same seed gives the same split.

**Step 2: calibrate the start state and NPC turn, and select the final evaluation set**

```bash
python3 tools/analysis/calibrate_uturn_start_geometry.py \
  --trace-dir $TD --records-jsonl $TD/uturn_records.jsonl \
  --split-report artifacts/autoware171_uturn_braking_calibration_v2.json \
  --exclude-report artifacts/autoware171_uturn_start_geometry_calibration.json \
  --exclude-report artifacts/autoware171_uturn_start_geometry_calibration_v2.json \
  --seed 20261008 \
  --output artifacts/<new-split>.json
```

- `--split-report`: uses the same calibration/check split as step 1.
- `--exclude-report`: excludes evaluation sets whose results have already been seen. Can be given more than once.
- `--seed`: the random seed that selects the 100 final evaluation cases (50 collisions, 50 non-collisions). Use a new value each time.

**Step 3: apply the results to the ODE** (currently manual)

The tools only write the estimates to files. Copy them by hand into the bundled files the ODE reads.

- Braking: `profile` and related fields into `targets/dynamics/calibrations/autoware171_uturn_braking.json`
- Start state: `coefficients` and related fields into `targets/dynamics/calibrations/autoware171_uturn_start_geometry.json`

**Step 4: check on development data**

```bash
python3 tools/analysis/validate_dynamics_against_awsim.py \
  --trace-dir $TD --records-jsonl $TD/uturn_records.jsonl \
  --calibration-report artifacts/<new-split>.json \
  --sources-key development_evaluation_sources \
  --output-dir artifacts/<development-check-output>
```

You may try this as many times as needed. This is the stage to change the model or the calibration method.

**Step 5: re-derive the margin** (currently manual)

On the development data, find "the largest ODE gap among cases that collided on AWSIM + 0.1 m" and update
`targets/dynamics/calibrations/autoware171_uturn_screening.json`.

**Step 6: run the final evaluation once**

Run the step 4 command with `--sources-key` changed to `final_validation_sources`.

### Current limitations

- Applying the results to the bundled files (step 3) and computing the margin (step 5) are not automated.
- The bundled file and setting names are for Autoware 1.7.1. Calibrating 1.8.0 or 1.9.0 overwrites the 1.7.1 values.
  Using several versions side by side requires code changes so that each version has its own calibration files.

## SDE

Passing `solver_kind=sde`, `sde_seed`, `sde_dt_sec`, and `sde_noise` with `--param` adds noise to the braking, the NPC acceleration,
and the NPC heading.

```bash
python3 run_worker_v2.py \
  --target dynamics --case-kind uturn \
  --param dx0=12 --param ego_speed=36 --param npc_speed=18 \
  --param solver_kind=sde --param sde_seed=3 \
  --param 'sde_noise={"ego_brake_acceleration_std":0.25}' \
  --output artifacts/dynamics/sde.jsonl --dataset-csv artifacts/dynamics/sde.csv
```

With all noise set to 0, the result is identical to the ODE. The noise magnitudes have not yet been calibrated against real data,
so treat SDE collision rates as a sensitivity analysis, not as real-world probabilities.
