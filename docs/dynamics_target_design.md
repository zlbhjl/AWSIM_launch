# 連続ダイナミクス対象の統計的検証拡張設計

## 1. 目的と結論

本書は、AWSIM_launch の汎用統計的検証フレームに、常微分方程式
(ODE) および将来の確率微分方程式 (SDE) による連続ダイナミクスを
検証対象として追加する設計と実装計画を定める。

最初の適用例は、既存 AWSIM の JAMA Uターンシナリオとする。
ただし、この題材だけに固定した実装にはしない。`target="dynamics"` を
連続ダイナミクス用の汎用 target とし、個別の物理モデル、制御器、外乱、
安全仕様は差し替え可能にする。

この拡張の役割は、高忠実度な AWSIM を置き換えることではない。軽量な
数理モデルで大量の試行を行って危険条件を絞り込み、その候補を AWSIM で
再検証する二層の検証フローを提供することである。PRISM は引き続き、
統計手法の推定値を厳密なモデル検査値と照合するために用いる。

## 2. 設計上の前提

この設計は、[refactor_design.md](refactor_design.md) と
[implementation_rules.md](implementation_rules.md) の以下の原則に従う。

- `TestCase`、`RawRunResult`、`EvaluationRecord` を境界の共通契約にする。
- target 固有処理は `targets/` に閉じ込める。
- 数値積分、物理モデル、制御器、安全判定は、統計評価・保存・Ray 実行基盤から分離する。
- 旧 `master_orchestrator.py` と `run_manager.py` は直接変更しない。v2 の CLI と registry に新 target を接続する。
- 新モジュールは unit test、契約テスト、回帰比較、smoke test を通すまで既存運用へ接続しない。

共通 contracts は、複数 target で実際に必要になった時点だけ拡張する。連続モデル固有の
状態、パラメータ、軌跡、数値積分情報は、まず `TestCase.input`、`RawRunResult.evidence`、
`EvaluationRecord.output`、`EvaluationRecord.meta` に置く。

## 3. 対象範囲

### 3.1 初期実装

初期実装は ODE を解く `uturn` モデルとする。既存の AWSIM Uターンの
`dx0`、`ego_speed`、`npc_speed`、シナリオ profile、発進トリガ、JAMA profile、
安全ラベルを正本として再利用する。

- ego 車と NPC の位置・速度・方位を状態に持つ平面モデルにする。
- NPC は既存の Uターン経路と同じ開始条件で、レーン走行からUターンへ切り替える。
- Uターン開始条件は既存どおり `longitudinal_distance_to_ego <= dx0` とする。
- NPC の前進開始条件、加速度、速度帯ごとの lane / offset / profile は既存設定と揃える。
- JAMA の反応遅れ、jerk、最大減速度を、停止距離・安全ゾーンの基準として再利用する。
- 数値積分には Python の `scipy.integrate.solve_ivp` を用いる。

ODE 自体は決定論的である。危険確率を推定する場合の確率性は、入力パラメータと初期状態の
明示的な確率分布から与える。各標本は、その分布から独立に生成した入力とする。

### 3.2 将来の拡張対象

`dynamics` target は、次のような `case_kind` または `model_id` を追加できる構造にする。

- `uturn`: JAMA Uターンの連続近似モデル
- `longitudinal_following`: 車間追従、急ブレーキ、ACC/AEB
- `cut_in`: 割込みを含む追従
- `lane_change`: 横方向運動と車線変更
- `intersection`: 交差点進入・右左折・優先関係
- `platooning`: 複数車両の追従と隊列制御
- `pedestrian_crossing`: 歩行者横断と制動制御
- `vehicle_dynamics`: 横滑り、タイヤ、二輪車などを含む車両運動
- `battery_thermal`: バッテリー・熱・制御系の連続ダイナミクス

SDE は ODE の受入後に導入する。SDE は新しい target に分けず、原則として
`target="dynamics"` と `solver_kind="sde"` の組合せで表す。ただし、依存環境や
実行形態が ODE と恒常的に異なる場合は `runners/sde.py` と専用コンテナ profile を追加する。

### 3.3 対象外

- AWSIM の物理モデルとの完全同値の主張
- 全入力空間を網羅する形式的完全証明
- 最初の段階での PDE、DAE、複雑な三次元車両モデルの実装
- 既存 AWSIM case_kind や legacy CLI の置換

## 4. アーキテクチャ

### 4.1 target の位置

```text
strategy / CLI
  -> TestCase(target="dynamics", case_kind=<model>)
  -> targets/dynamics/backend.py
  -> RawRunResult
  -> targets/dynamics/result_interpreter.py
  -> EvaluationRecord
  -> result sink / dataset adapter / existing statistical modes
```

`backend.py` は軌跡生成だけを責務とし、安全判定や統計的停止判断を行わない。
`result_interpreter.py` は軌跡を読み、安全指標を算出して `EvaluationRecord` を作る。
二項CI、DKW、SPRT、EBStop、JSONL 保存、dataset CSV、Ray queue は既存の共通経路を使う。

### 4.2 統計モードと再現性

`dynamics/uturn` の統計試行では、ODE 自体へ乱数を隠蔽しない。CLI の
`--dynamics-input-distribution` で独立一様分布を宣言し、`--seed` と
`sample_index` から各入力を決定する。指定がない場合は、固定した `--param` を除く
`dx0`、`ego_speed`、`npc_speed` の共有範囲を一様分布として使う。

- `binomial_ci` と `sprt` は `c_collision` を評価する。
- `dkw`、`dkw_fixed`、`ebstop` は `min_ttc` を評価する。
- 各 JSONL／dataset CSV 行には seed、分布、sample index、実際の標本値を保存する。
- 実行終了時には `<output>.summary.json` に統計レポート、入力分布、seed、停止理由を保存する。

このため、分散実行の処理順が変わっても、同じ分布・seed・sample index から同一の
試験入力を再構成できる。

### 4.3 ディレクトリ（実装済み）

```text
targets/dynamics/
├── backend.py                 # TestCase から実行を調停、軌跡CSV・raw JSONを保存
├── profile.py                 # 入力の解釈・型・範囲検証、校正値の解決
├── calibration.py             # 同梱校正値の読み込み
├── calibrations/              # 版付きの校正値 JSON
├── result_interpreter.py      # 軌跡から EvaluationRecord を構築（衝突・隙間・TTC・JAMA）
├── statistical.py             # 統計モードの既定指標
├── models/
│   ├── base.py                # 汎用ハイブリッド確率微分方程式のインターフェース
│   ├── uturn.py               # UTurnSystem（base の一実装）と ODE 入口
│   └── uturn_sde.py           # SDE 入口（ノイズ設定）
└── runners/
    ├── ode.py                 # モード分割した solve_ivp（ガード = 終端イベント）
    └── sde.py                 # 固定刻み Euler--Maruyama（ノイズ0なら ode へ委譲）
```

controller や judge は、2つ目のモデルで重複が実際に生じるまでモデル内・interpreter 内に置く。

### 4.4 汎用モデルとモデルの最小インターフェース

すべてのモデルを、離散モード $q$ を持つハイブリッド確率微分方程式として表す。

```text
dx = f_q(t, x) dt + G_q(t, x) dW_t        （G_q = 0 なら ODE）
モード切替 q -> q' : ガード g(t, x) が指定方向に 0 を横切った時点で x+ = r(x-)
```

各モデル（`models/base.py` の `HybridSystem`）は次を提供する。

| 要素 | 意味 |
|---|---|
| `state_names` | 状態変数名（単位は名前の接尾辞） |
| `initial_mode()` / `initial_state()` | 初期モードと初期状態 |
| `drift(t, x, q, entry_times)` | $f_q$。`entry_times` は各モードに入った時刻 |
| `noise_channels(q)` / `diffusion(...)` | ブラウン運動の成分名と $G_q$（n × m_q） |
| `transitions(q)` | `Transition(target_mode, guard, direction, reset)` の列 |
| `project(x)` | SDE の1ステップ後に許容集合へ戻す写像（速度 ≥ 0 など） |
| `has_noise` | すべての拡散が恒等的に0なら False |

runner はモデル固有の意味を解釈しない。

- ODE runner: モードごとに `solve_ivp` で積分し、ガードを終端イベントとして根を求める。
  モードに入った時点ですでにガードが成り立つ場合は、その時刻で切り替える
  （開始点上の根は符号変化として捉えられないため）。
- SDE runner: $x_{k+1} = P(x_k + f_q\,\Delta t + G_q\sqrt{\Delta t}\,\xi_k)$、
  $\xi_k \sim N(0, I_{m_q})$ を1つの seed 付き乱数列から引く。ガードは各格子点で判定する。
  `has_noise=False` なら ODE runner に委譲するので、ノイズ0の極限は厳密にODEと一致する。

新しいモデルを追加するときは `HybridSystem` を実装し、`simulate_*` 入口と profile を足す。
runner・統計モード・保存経路は変更しない。例題モデルでの解析解・リセット・EM の一致は
`tests/unit/targets/test_dynamics_hybrid_runners.py` で確認している。

## 5. 入出力契約

### 5.1 TestCase

```python
TestCase(
    case_id="dynamics_000001",
    target="dynamics",
    case_kind="uturn",
    input={
        "model_id": "uturn",
        "solver_kind": "ode",
        "horizon_sec": 10.0,
        "sample_period_sec": 0.02,
        "solver": {"method": "RK45", "rtol": 1e-6, "atol": 1e-9},
        "scenario": {"dx0_m": 15.0, "ego_speed_kmh": 35.0, "npc_speed_kmh": 15.0},
        "scenario_profile": {"profile_id": "right_15"},
        "parameters": {"npc_acceleration_mps2": 7.0},
        "controller": {"kind": "jama_ai_aeb"},
        "disturbance": {},
        "seed": 12345,
    },
)
```

`input` の値は単位をキー名に明記する。入力分布からサンプルした値は、分布パラメータではなく
実際に使った値を必ず保存する。分布名、seed、サンプリング方式は `meta` に残して再現可能にする。

### 5.2 RawRunResult

`RawRunResult` は `status`、軌跡への参照、数値実行の追跡情報を保持する。

- 正常時は `evidence["trace_csv"]` と `evidence["raw_result_json"]` を保存する。
- `meta` には solver 名、許容誤差、終了理由、実行時間、モデル実装の識別子を保存する。
- 数値積分が失敗・発散・許容時間超過した場合は、接続境界で `execution_error` または `timeout` に正規化する。

### 5.3 EvaluationRecord

共通的に比較可能な安全指標は、既存 AWSIM の命名に合わせる。

```text
output.c_collision             0 または 1
output.min_ttc                 秒
output.c_ttc_1_5               0 または 1
output.min_gap_m               m
output.max_required_decel_mps2 m/s^2
output.aeb_response_delay_sec  秒
output.safe_headway_recovered  0 または 1
```

全モデルに共通でない測定値は、`output` の model 固有キーとして保存する。
統計層が target 固有の列名を直接参照しないよう、実行モードが使用する metric と
有効範囲は target profile または dataset adapter 経由で注入する。

`meta` には、少なくとも `schema_version`、`source_module`、`created_at` を入れる。
加えて `model_id`、`solver_kind`、solver 設定、seed、数値終了理由を入れる。

## 6. 初期モデルと安全仕様

### 6.1 既存 Uターン定義の再利用

既存 AWSIM シナリオには、すでに次の定義がある。

- 探索変数: `dx0`、`ego_speed`、`npc_speed`
- 探索範囲: `dx0=10--25 m`、`ego_speed=30--40 km/h`、`npc_speed=10--25 km/h`
- NPC の速度帯・ego速度帯別の lane、offset、加速度、`npc_start_speed_ratio`
- Uターンの開始条件: `longitudinal_distance_to_ego <= dx0`
- JAMA human / ai_aeb profile による反応遅れ、jerk、最大減速度
- 判定ラベル: 衝突、TTC閾値、位置差、NPC/ego stuck

`theoretical_calculator.py` はJAMA profileから停止距離と安全マージンを計算済みである。
これは新しい連続軌跡を生成するODEそのものではないが、Uターンモデルの入力、制動応答、
安全ゾーンの校正・回帰基準として直接利用する。

シナリオ定義を二重管理しないため、target非依存の Uターン入力範囲、JAMA profile、
発進・Uターン開始条件は `scenario_specs/uturn.py` を正本とする。AWSIM targetと
dynamics targetの双方がこの specification を読む。AWSIMのlane / offset / waypointなど
地図依存のprofileはAWSIM targetに残す。dynamics targetがAWSIM backendや
AWSIMScriptPyを直接importしてはならない。

### 6.2 Uターン連続モデル（汎用モデルの具体化）

Uターンは `UTurnSystem` として 4.4 節の汎用モデルを具体化したものである。
状態は9次元で、NPCは幾何中心ではなく旋回の基準点（AWSIMの姿勢原点）で持つ。

```text
x = (x_e, y_e, psi_e, v_e,  x_n, y_n, psi_n, v_n,  theta)
     ego幾何中心・方位・速度   NPC基準点・方位・速度    累積旋回角
```

モードは `lane`（両車直進）→ `turn`（NPC旋回）→ `exit`（NPC直進）の3つ。各モードのドリフトは次のとおり。

```text
dx_e/dt = v_e cos(psi_e)            dx_n/dt = v_n cos(psi_n)
dy_e/dt = v_e sin(psi_e)            dy_n/dt = v_n sin(psi_n)
dv_e/dt = a_cruise   (lane)         dv_n/dt = a_npc  （ego が発進比率に達した後）
        = a_brake(t - t_turn)  (turn, exit)
dpsi_n/dt = -v_n / R,  dtheta/dt = v_n / R   (turn のみ)
```

- ガード `lane -> turn`: NPC幾何中心とegoの縦方向clearanceが `dx0` に達した時点（リセット `theta=0`）
- ガード `turn -> exit`: `theta` が旋回角に達した時点（リセット `theta=旋回角`）
- 制動 `a_brake` は遅れ `t_d`、立上り `t_j`、減速度 `a_max` の区分線形 profile
- NPC幾何中心は観測量 `c_n = (x_n, y_n) + d (cos psi_n, sin psi_n)`、`d = npc_turn_reference_offset_m`
- SDE の拡散: ego速度（制動中）、NPC速度（発進後）、NPC方位（`turn` 中）の3成分

旋回半径 R と旋回角は、AWSIMでNPCが実際に走った経路から校正した値である（v2）。AWSIMの
waypoint follower はwaypoint半円（半径約4.9 m）より内側を走り、NPCの姿勢原点は半径約3.35 m・
約176.6°の円弧をたどる。幾何中心はその1.12 m前方にある。

幾何中心を一定曲率で動かすと、NPCが前方（-x方向）へ約1 m張り出しすぎる。v1はこれが原因で
過検出していた。
NPCは既存と同じ速度条件を満たした後にレーン走行し、相対距離が `dx0` に達した時点で
Uターン経路へ切り替える。egoの応答は、まずJAMA ai_aeb profileを使う理想化制動モデルとし、
後にAWSIM/Autowareの実測軌跡から校正可能にする。

既定の `alignment_mode=uturn_trigger_aligned` では、地図上のspawnからの助走区間をODEへ
推測で持ち込まず、AWSIMの `longitudinal_distance_to_ego <= dx0` が成立した瞬間を時刻0とする。
初期速度、縦・横相対位置、NPC方位、旋回半径は、AWSIM traceで車体中心offsetを適用した
Uターン開始状態を教師値とし、`dx0`・ego速度・NPC速度から線形校正する。これは
イベント整列した低忠実度surrogateであり、Lanelet上のspawn位置、Autowareの加速・制動過渡、
車体剛体接触までAWSIMと同一であるとは主張しない。解決後の初期状態、半径、発進比率、校正元は
各成果物の `execution` metadataへ保存する。

`dx0` は車両中心間距離ではなくAWSIM triggerの縦方向clearanceとして扱う。ODE内部では
actor原点から幾何中心へのoffsetを含む校正式で中心間距離へ変換する。衝突判定はAWSIM traceに保存された車体寸法を用いる
向き付き矩形のSeparating Axis Testを既定とし、旧中心距離判定は明示指定時だけ利用する。

TTCは、各観測時点から5秒先まで0.1秒刻みで等速直線移動したOBBが最初に重なる時刻とする。
AWSIMとの比較では、AWSIM trace側にも同じコード（`_cvm_obb_ttc`）を適用する。AWSIMの
`twist.linear` は既にワールド座標系であるが、`AW_Kinematics_Extractor` のCVM/CTRVモードは
これをyawで再回転するため、その `min_ttc` は比較の基準にしない（参考値
`awsim_min_ttc_extractor` として保存のみ）。車体間距離は中心間距離の `min_distance` とは別に、
OBBのクリアランス `min_clearance` として出力する。Maudeの離散TTCラベルは別の評価経路として区別して保存する。

AWSIM比較モードでは、AWSIM traceの校正集合から求めた反応遅れ、立上り時間、最大減速度を
持つ `autoware171_uturn_calibrated` controllerを使う。v2の制動profileは、トリガー後2.5秒の
ego速度系列へ遅れ・立上り・一定減速の曲線を最小二乗で当てはめたものである
（`a_max = 3.013 m/s^2`、10--90%区間 `2.98--3.03`）。v1は微分した加速度の90パーセンタイルを
使っていたため、持続的な減速度を約0.9 m/s^2過大に見積もっていた。JAMA profileは理論的安全条件の評価と
単独ODE/SDE実験用として保持する。校正集合とhold-out集合、抽出規則、経験区間、誤差はversioned
JSONへ保存し、比較成果物のcontroller provenanceから追跡可能にする。

拡散係数がすべて0のSDEは決定論的ODEと数学的に同一であるため、実装でもODE solverへ合流する。
非零ノイズの場合だけ固定刻みEuler--Maruyamaを使う。これにより、零ノイズ時の固定刻みや
イベント離散化による人工的な危険境界のずれを除く。

最初に実装する判定は以下とする。

| ID | 仕様 | 判定値 |
| --- | --- | --- |
| S1 | シミュレーション中に衝突しない | `c_collision` |
| S2 | TTC が 1.5 秒未満にならない | `c_ttc_1_5` と `min_ttc` |
| S3 | JAMA profileの制動で停止可能な余裕を維持する | `theory_margin_a_ai`、`theory_margin_b_ai` |
| S4 | NPCが規定どおり発進・Uターンを開始する | `c_npc_stuck` |

時間論理の汎用判定は初期実装の必須要件にはしない。複数の連続モデルで同じ仕様記法を
必要とした時点で、`judges/temporal_rules.py` を STL 系の評価器に発展させる。

## 7. 統計的・数値的な妥当性

### 7.1 統計的主張の範囲

危険確率や信頼区間は、明示した入力分布、独立なサンプル生成、固定した数値積分設定、
安全仕様の下での推定値である。ODEの結果を現実世界やAWSIMへの無条件な保証として
解釈しない。

二項の危険事象には `binomial_ci` または `sprt` を、連続量の分位点には `dkw` を使う。
逐次停止を使う場合は、既存 mode の前提と停止規則をそのまま適用する。

### 7.2 数値積分の検証

各実験で solver 名、`rtol`、`atol`、最大ステップ、サンプル周期を記録する。
受入では以下を確認する。

1. 定数加速度など解析解を持つ例で、位置と速度の誤差が許容範囲内である。
2. 許容誤差と最大ステップを厳しくしても、主要な衝突・TTC判定が安定する。
3. 同一 TestCase と同一 seed では同一の軌跡・判定となる。
4. solver 失敗や非物理値は安全と誤判定せず、error status として保存される。

### 7.3 学術的な位置づけ

連続またはハイブリッド系のシミュレーション軌跡に時間的安全仕様を適用し、
統計的に評価する方法は、統計的モデル検査とハイブリッドシステム検証の先行研究と
整合する。本プロジェクトの主張は、新しいODE solverそのものではなく、離散確率系、
連続ダイナミクス、高忠実度シミュレータを共通 contracts と共通統計評価へ接続する
実装・評価の一般性に置く。

参考文献候補:

- Alexandre David et al., Statistical Model Checking for Stochastic Hybrid Systems, EPTCS 2012.
- Yashwant Annapureddy et al., S-TaLiRo A Tool for Temporal Logic Falsification for Hybrid Systems, TACAS 2011.
- Yu Wang et al., Statistical Verification of Hyperproperties for Cyber Physical Systems, 2019.

## 8. 実装段階と受入ゲート

### P0 仕様と fixture

- `uturn` の状態、単位、入力範囲、入力分布、seed規則を文書化する。
- 既存AWSIMのUターン設定から共有 scenario specification を切り出す。
- 定数加速度、衝突、非衝突、数値失敗の最小 fixture を作る。
- 解析解または手計算可能な期待値を fixture ごとに記録する。

受入条件: 仕様にない暗黙の単位・確率分布・判定閾値が残っていない。

### P1 core target

- 最初に `targets/dynamics/models/uturn.py` を、AWSIM・CLI・ファイル保存に依存しない純粋ODEとして実装する。
- NPCのレーン走行、共有開始条件によるUターン切替、JAMA制動、位置・速度・方位の軌跡をここで扱う。
- 定数速度の直線運動と半円Uターンを、解析可能な回帰基準として確認する。
- `targets/dynamics/profile.py`、`backend.py`、`runner.py`、`result_interpreter.py` を追加する。
- `targets/dynamics/models/uturn.py` を追加する。
- JSON軌跡と `EvaluationRecord` を生成する。

受入条件: `TestCase -> RawRunResult -> EvaluationRecord` の正常系と異常系を契約テストで確認する。

### P2 v2 経路への接続

- `targets/registry.py` の `build_target_components()` に `dynamics` を追加する。
- `apps/cli/worker_main.py` と `apps/cli/orchestrator_main.py` の target 選択・入力正規化を追加する。
- dataset adapter と target profile を使い、二項CIとDKWへ metric を注入する。

受入条件: 既存の AWSIM / PRISM 経路が回帰しない。dynamics の1標本が JSONL と dataset CSV に保存される。

### P3 統計・再現性検証

- 入力分布から複数標本を生成する strategy を追加する。
- `c_collision` の二項CIと `min_ttc` のDKWを動かす。
- seed、入力値、solver設定、停止理由を全て成果物へ残す。

受入条件: 同一seedで再現でき、別seedの独立試行が集計される。統計 mode が
`max_samples` と既存停止規則のどちらで終了したかを保存する。

### P4 運用モードの分離

P4以降は、目的・統計母集団・受入基準を混在させないため、次の二つを別モードとして扱う。

| モード | 目的 | 入力 | 主成果物 | AWSIMの役割 |
| --- | --- | --- | --- | --- |
| `dynamics-standalone` | ODE（後にSDE）単独の安全性・数値安定性・感度を評価する | 明示した確率分布、seed、solver設定 | 危険確率・TTC分布・JAMA余裕・停止理由 | 使用しない。ODE/SDE自身の主張だけを記録する。 |
| `dynamics-awsim-compare` | ODEで抽出した候補をAWSIMで再検証し、近似の適用範囲を測る | 共通の `dx0`・ego速度・NPC速度・profile・初期条件 | ケース差分表、境界差、再検証結果 | 参照実行系。同値性を仮定しない。 |

共通の `TestCase -> RawRunResult -> EvaluationRecord` は維持する。ただし比較モードは
単独モードの統計標本を混ぜず、`comparison_id` で紐付けた二つの実行結果を比較レコードへ
正規化する。AWSIMのタイムアウト・失敗はODEとの不一致ではなく、再検証不能として保存する。

CLIでは既存の `--mode`（`binomial_ci`、`dkw` 等）の意味を変えない。単独モードは
`--target dynamics --mode <statistical-mode>` を継続し、比較は将来追加する
`run_dynamics_awsim_compare_v2.py` を独立入口として表す。比較を `--target dynamics` の
一種にしないことで、対象実行と二系統を束ねる実験手順を分離する。将来の複数ケース
workflowでは、この入口を `--workflow compare --left-target dynamics --right-target awsim`
へ拡張する。

受入条件: 両モードのJSONL／dataset CSV／統計サマリーが別experiment ID・別成果物ディレクトリを使い、集計時に相互混入しない。

### P5 AWSIM 比較モード

- 共通入力 `ComparisonCase` を定義する。必須項目は `comparison_id`、`dx0`、ego速度、
  NPC速度、JAMA profile、初期配置、シナリオprofile、判定閾値とする。
- 同一 `ComparisonCase` をdynamicsとAWSIMへ配布し、単位・座標原点・時刻基準をadapterで明示変換する。
- `c_collision`、`min_ttc`、JAMA Zone A/B、JAMA余裕をケース単位で比較する。
- 速度格子ごとに `dx0` を掃引し、衝突有無が切り替わる最小間隔を `critical_dx0` として推定する。
  ODEとAWSIMの `critical_dx0` 差を主要指標とする。
- ODE単独モードで危険候補を抽出した後、候補・境界近傍・対照安全ケースをAWSIMへ再検証する。
  AWSIM結果をODEの学習データへ自動混入させるのは、校正フェーズを明示するまで行わない。

受入条件: ケース別の一致表と速度格子別の危険境界差を残し、不一致をODEの適用範囲・校正対象として説明できる。

結果（Autoware 1.7.1、未使用の層化100件、v2モデル）: 衝突一致95%、検出率96%、非衝突の正判定率94%、
連続TTCの平均誤差0.035秒。詳細は `docs/dynamics_awsim_final_validation_v2_20261005.md`。

判定は2つのモードで出す。判定モード（`c_collision`、AWSIMの代替）と、スクリーニングモード
（`c_screening_candidate`、隙間1.1 m未満を候補としてAWSIMで再検証）である。別の未使用100件では、
スクリーニングモードは見逃し0/50・非衝突の正判定率72%だった。判定モードは正解率95%だった。
詳細は `docs/dynamics_awsim_decision_modes_20261005.md`。

### P6 SDE 拡張

- `solver_kind="sde"` と `sde_seed`、`sde_dt_sec`、`sde_noise` を追加する。
- Uターンには固定刻み Euler--Maruyama solver を使い、NPC加速度、NPC方位、
  ego制動加速度へ独立なブラウン運動の拡散項を与える。
- `sde_noise` は `npc_acceleration_std`、`ego_brake_acceleration_std`、
  `npc_heading_std_rad_per_sqrt_sec` を持つ。全てゼロなら固定刻みの決定論的近似となる。
- seed、刻み幅、拡散係数を raw JSON、EvaluationRecord meta、dataset CSVに保存する。
- ODEとSDEの入力・出力契約の共通部分を確認し、必要な部分だけ runner を分離する。
- ノイズ強度を変えた感度分析を行う。

受入条件: 同一seedの再現、異なるseedの独立性、時間刻み・solver変更に対する判定安定性を確認する。

## 9. テスト計画

実装ルールに従い、各新モジュールに少なくとも1件の正常系と異常系の unit test を置く。

```text
tests/
├── unit/targets/
│   ├── test_dynamics_profile.py
│   ├── test_dynamics_runner.py
│   └── test_dynamics_result_interpreter.py
├── contracts/
│   └── test_dynamics_contract.py
├── regression/
│   └── test_dynamics_kinematics.py
└── smoke/
    └── test_dynamics_pipeline.py
```

回帰比較の基準は、初期段階では旧実装ではなく解析解と手計算可能な運動学モデルとする。
AWSIMとの比較は、物理的な同値性を要求する回帰テストではなく、実験結果として別に管理する。

CLI接続前には、少なくとも次を通す。

1. 単一ODEケースの正常実行
2. 無効入力の `invalid` または `execution_error` 化
3. 数値失敗の status・evidence・meta 保存
4. JSONL、dataset CSV、統計historyの生成
5. `binomial_ci` と `dkw` の短時間smoke

## 10. 将来の設計判断

- 共通化は、二つ以上の dynamics model で必要になった時点で行う。
- STL、SDE、複数車両、専用可視化は初期ODEの受入後に追加する。
- dynamics target の新しい case_kind を足す時は、モデル、入力検証、判定仕様、fixture、unit testを同じ変更単位にする。
- target固有の指標を共通統計層へ直接追加しない。metric選択は target profile / dataset adapter から注入する。
- 実行速度が必要になるまで、専用コンテナ、GPU、Julia実行環境を導入しない。SDEの精度・性能要件が確定した時点で実行環境を選ぶ。

この順序により、最小の車間追従モデルを早く検証しつつ、将来は連続・確率連続・ハイブリッドな対象を増やせる。
