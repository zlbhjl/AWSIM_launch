# PRISM 対応計画

## 1. 目的

`AWSIM_launch` が AWSIM / Autoware 専用ではなく、異なる種類の検査対象を共通フレームへ接続できることを示すため、PRISM による簡単なマルコフ連鎖の実行経路を追加する。

この対応では、AWSIM / Autoware の実験や車両挙動を PRISM 上で再現しない。PRISM 用の独立した小規模 DTMC を用意し、既存の共通契約、worker、保存処理、Maude 判定器に加え、二項信頼区間、DKW、GP 境界推定、FT4D を可能な範囲で再利用できることをデモする。

PRISM 自身が返す厳密なモデル検査結果と、サンプルパスを用いた統計評価は役割が異なる。両者を混ぜず、同じモデルに対する結果を比較できる構成にする。

## 2. 到達目標

最初のリリースでは、次の一連の処理をローカル環境で実行可能にする。

```text
PRISM exact model checking
  -> 有限ステップ障害到達確率を計算

Repeated TestCase
  -> PRISM simulator で独立な有限長パスを生成
  -> 共通 JSON へ正規化
  -> Maude で各パスの規則を判定
  -> EvaluationRecord として保存
  -> 二項CI / DKW / GP / FT4D で集約
  -> PRISM の厳密値と統計推定値を比較
```

利用例の目標形は次のとおりとする。CLI の最終的な引数名は実装時に既存 worker との整合を確認して確定する。

```bash
python3 run_worker_v2.py \
  --target prism \
  --case-kind simple_reliability_dtmc \
  --param steps=20 \
  --param p_fail=0.05 \
  --output ./verification_results/prism_demo.jsonl
```

成功時には、少なくとも次を確認できるようにする。

- PRISM が計算した `failure` 状態への到達確率
- PRISM simulator が生成した状態遷移列
- Maude が判定した規則違反の有無
- 二項信頼区間による `failure_reached` の推定値と区間
- DKW による有限ステップ指標の分位点と区間
- 条件を変化させる場合の GP 境界推定
- Maude の規則違反を basic event とした FT4D 集約
- PRISM の厳密値と統計推定値の差
- 実行条件、成果物パス、実行時間、PRISM バージョン
- `success`、`timeout`、`execution_error`、`analysis_error`、`invalid` の区別

## 3. 対象範囲

### 3.1 初期対応に含めるもの

- PRISM command-line tool のローカル実行
- DTMC モデル 1 種類
- PCTL プロパティ 1 ファイル
- モデル定数の CLI 指定
- PRISM による厳密なプロパティ評価
- PRISM simulator による有限長パス生成
- PRISM 出力から共通 JSON への変換
- PRISM 用の小さな Maude 規則と判定処理
- v2 worker の `--target prism` 対応
- 同一条件で複数パスを収集する PRISM sampling mode
- 既存 `BinomialCIService` による障害確率の逐次評価
- 既存 `DKWService` による有限長パス指標の評価
- 既存 `GPBoundaryService` によるパラメータ境界のデモ
- 既存 `FT4DService` による Maude 違反イベントの集約
- 統計評価結果と PRISM 厳密値の比較レポート
- fixture、unit、contract、smoke テスト
- README におけるデモ手順と位置づけの説明

### 3.2 初期対応に含めないもの

- AWSIM / Autoware の挙動再現または比較
- PRISM向けの適応的な分散サンプリング戦略（固定パラメータ1件のRay queue実行は対応）
- GP を使って次のパラメータを自動選択する適応探索
- PRISM の厳密値と統計区間を合成した新しい数学的保証
- 適応的に選んだ条件のサンプルを i.i.d. とみなす評価
- PRISM の厳密な到達確率を、統計評価の1サンプルとして数える処理
- MDP、CTMC、PTA、POMDP の実行保証
- PRISM GUI の操作支援
- 任意の利用者提供モデルを無制限に実行する機能
- PRISM モデルを自動生成する汎用 DSL

MDP と CTMC は将来拡張候補として記載するが、初期版の対応実績は DTMC に限定して表現する。

### 3.3 実行環境の決定

PRISM対応は、次の専用開発image内で最初から実装・テストする。既存の Autoware / AWSIM image は流用も変更もしない。

| 項目 | 決定 |
|---|---|
| image 名 | `awsim-launch/prism-maude:0.1.0` |
| Dockerfile | `docker/prism-maude.Dockerfile` |
| container profile | `prism_maude` |
| ベース image | `ubuntu:24.04` (amd64) |
| PRISM | 4.10.1 Linux x86_64 binary |
| Maude | 3.5.1 |
| Python | Ubuntu 24.04 の Python 3.12（`/opt/venv`） |
| Java | headless JRE 17 |
| GPU / CUDA | 使用しない |
| ROS / Autoware / X11 | 含めない |
| Ray client | 2.55.0（AWSIM workerと同じqueueへ接続） |
| ソースコード | imageへコピーせず `/workspace` へ bind mount |

PRISM 4.10.1 の同梱 `lp_solve` は `GLIBC_2.38` 以降を必要とするため、ホストとは分離して Ubuntu 24.04（glibc 2.39）を採用する。既存 Maude 3.5.1 と AW-CheckerPy の Python runtime は参照せず、Maude 3.5.1 と Python binding を image 内で固定する。

PRISMは公式配布の `prism-4.10.1-linux64-x86.tar.gz` を使用する。Dockerfileには取得URLだけでなく SHA-256 を固定し、初回導入時に値を記録する。Maudeもローカル環境を `docker commit` でコピーするのではなく、3.5.1の配布物またはソースをDockerfileで明示的に導入する。

コンテナは CPU 専用であり、`--gpus`、NVIDIA Container Toolkit、`DISPLAY`、`privileged`、host network は使用しない。したがって、新しい GPU driver 設定や既存 Autoware container profile の変更は発生しない。

`runtime/container/profiles/prism_maude.py` は、このimageを司令塔から選択するための登録である。既存AWSIM profileは変更せず、後方互換の能力既定値として `target=awsim`、`requires_gpu=true`、`requires_ros=true`、`requires_runtime_monitor=true` を持つ。PRISM profileはこれらをすべてfalseとして宣言する。cluster managerはこの能力値を参照し、PRISMではGPU preflight、GPU watchdog、`--gpus all`、ROS環境、Xvfb、AWSIM固有mount・同期を行わない。

固定パラメータ1件を共通Ray queueへ投入するcluster経路は次の形で使用する。各workerではPRISMによる経路生成とMaude判定を同じcontainer内で行い、司令塔には共通の評価結果だけを戻す。

```bash
python3 run_orchestrator_cluster_v2.py \
  --target prism \
  --container-profile prism_maude \
  --case-kind simple_reliability_dtmc \
  --param model=simple_reliability_dtmc \
  --param steps=20 \
  --output ./verification_results/prism_cluster.jsonl
```

PRISM cluster workerにも `AWSIM_launch` とworker成果物ディレクトリをbind mountする。実行時のcontainer名は `prism_worker_<ROS_DOMAIN_ID>` とし、既存AWSIMの `sim_worker_*` を停止・置換しない。profile名とtargetが一致しない起動は事前に拒否する。PRISM imageにはRay client 2.55.0を組み込み、Ray nodeとしてGPU資源を登録せず `ray://<head>:10001` 経由で既存のdetached queue actorへ接続する。

現行のcluster CLIは開始時にRay headを再作成するため、AWSIM cluster実験とPRISM cluster実験は同時には起動しない。既存実験を止めずにPRISMだけ確認する場合は、専用image内で `apps.cli.prism_main` または直接実行の `run_worker_v2.py` を使う。

開発時の起動形は次を正本とする。

```bash
docker build \
  -f docker/prism-maude.Dockerfile \
  -t awsim-launch/prism-maude:0.1.0 \
  docker

docker run --rm -it \
  -v "$PWD:/workspace" \
  -w /workspace \
  awsim-launch/prism-maude:0.1.0 \
  bash
```

image 内の `coder` は UID/GID 1000 で作成されるため、通常のホスト利用者
`passd`（UID/GID 1000）との bind mount は書込み可能である。別の UID を使う
ホストでは、起動時に `--user "$(id -u):$(id -g)"` を追加する。

最初の container smoke は、次の3つだけとする。

```bash
prism -version
maude --version
python3 -c "import maude"
```

実装済みの統合デモは、次で実行する。各反復で PRISM が独立経路を生成し、Maude 判定、既存の二項信頼区間・DKW・FT4D の入力変換までを行う。

```bash
python3 -m apps.cli.prism_main --samples 20 --min-samples 10
```

## 4. デモモデル

### 4.1 モデルの意味

デモには、対象分野に依存しない簡単な信頼性モデルを使用する。状態は次の 3 つとする。

| 値 | 状態 | 意味 |
|---:|---|---|
| `0` | `normal` | 正常 |
| `1` | `degraded` | 劣化 |
| `2` | `failure` | 障害。吸収状態 |

遷移確率は PRISM の定数として与えられるようにし、デモ時に変更可能にする。

```text
normal   -> normal / degraded / failure
degraded -> normal / degraded / failure
failure  -> failure
```

確率の和が 1 にならない入力、負の確率、`steps < 1` は PRISM 起動前に拒否する。

### 4.2 PRISM プロパティ

初期版では、少なくとも次のプロパティを評価する。

```text
P=? [ F "failure" ]
P=? [ F<=STEPS "failure" ]
```

1つ目は最終的に障害へ到達する確率、2つ目は指定ステップ以内の到達確率を表す。終了しないモデルに対する意味の取り違えを避けるため、README では有限ステップの結果を主なデモ値として扱う。

### 4.3 Maude のデモ規則

Maude は、PRISM が計算済みの到達確率を再計算しない。生成された1本の有限トレースに対して、次の規則を判定する。

- `EARLY_LIMIT` step 以内に `failure` へ到達したら `EARLY_FAILURE` 違反とする
- `degraded` が規定回数以上現れたら `REPEATED_DEGRADATION` 違反とする
- `failure` は吸収状態であり、その後に他状態へ移ったトレースを `invalid` とする
- 未知の状態値またはステップ番号の欠落を `invalid` とする

これにより、PRISM は確率モデルの実行・解析、Maude は具体的なトレースに対する規則判定、という責務分離を示す。

## 5. 設計

### 5.1 既存契約の利用

既存の型は変更せず、拡張用辞書を使う。

#### `TestCase`

```python
TestCase(
    case_id="prism-demo-001",
    target="prism",
    case_kind="simple_reliability_dtmc",
    input={
        "model_id": "simple_reliability_dtmc",
        "steps": 20,
        "constants": {
            "p_fail": 0.05,
        },
    },
)
```

`model_path` を利用者から直接受け取る方式は初期版では採用しない。`model_id` を allowlist されたモデルへ解決し、意図しないファイル参照や任意コマンド相当の設定混入を防ぐ。

#### `RawRunResult`

成功時の evidence は、既存の標準キーを使う。

```python
RawRunResult(
    target="prism",
    status=RunStatus.SUCCESS,
    evidence={
        "raw_result_json": ".../prism_result.json",
        "trace_json": ".../prism_trace.json",
        "log": ".../prism.log",
    },
    meta={
        "backend_mode": "execution",
        "prism_version": "...",
        "model_id": "simple_reliability_dtmc",
    },
)
```

#### `EvaluationRecord`

`output` には最低限、次を保存する。

```json
{
  "property_results": {
    "eventual_failure": 1.0,
    "bounded_failure": 0.31
  },
  "trace_length": 20,
  "failure_reached": 1,
  "first_failure_step": 8,
  "steps_to_failure_capped": 8,
  "degraded_visit_count": 3,
  "maude_verdict": "violation",
  "maude_violation": 1,
  "rule_violations": ["failure_reached"]
}
```

PRISM の厳密なモデル検査結果と、サンプルパス上の事実を同じ値として扱わない。`property_results` と trace / Maude 結果を別フィールドに保持する。

`first_failure_step` は障害が起きなかった場合 `null` になるため、そのまま DKW の標本には使わない。`steps_to_failure_capped` は、障害発生時には最初の発生 step、観測 horizon 内に障害がなければ `steps + 1` とする。これは真の無限時間故障時間ではなく「有限 horizon で打ち切った指標」であることを schema とレポートに明記する。

### 5.2 追加するディレクトリ

```text
targets/prism/
├── __init__.py
├── backend.py
├── runner.py
├── profile.py
├── result_interpreter.py
├── trace_parser.py
├── model_catalog.py
├── sampling.py
├── statistical_adapter.py
├── verification_input.py
├── models/
│   └── simple_reliability_dtmc.prism
└── properties/
    └── simple_reliability_dtmc.props

verifiers/maude/
├── prism_checker.py
├── prism_trace_evaluator.py
└── specs/
    └── prism_trace.maude

tests/
├── fixtures/prism/
├── unit/targets/test_prism_backend.py
├── unit/targets/test_prism_trace_parser.py
├── unit/targets/test_prism_result_interpreter.py
├── unit/targets/test_prism_model_catalog.py
├── unit/targets/test_prism_sampling.py
├── unit/targets/test_prism_statistical_adapter.py
├── unit/targets/test_prism_verification_input.py
├── unit/verifiers/test_prism_maude_evaluator.py
├── unit/targets/test_registry.py
└── smoke/test_run_worker_v2_prism_local.py
```

### 5.3 モジュール責務

#### `targets/prism/model_catalog.py`

- `model_id` とモデル・properties ファイルの対応を管理する
- モデルごとに許可する定数名、型、範囲を定義する
- path traversal や未知のモデル ID を拒否する

#### `targets/prism/profile.py`

- `TestCase.input` の検証と型変換を行う
- PRISM 実行ファイル、steps、timeout、定数を確定する
- 確率制約など、モデル固有の実行前検証を行う

#### `targets/prism/runner.py`

- 引数を文字列連結せず、配列として command を構築する
- `subprocess.run(..., shell=False)` で PRISM を実行する
- timeout、終了コード、stdout、stderr、実行時間を返す
- プロパティ評価とパス生成を別 invocation として扱う
- 一時ディレクトリ内に CSV とログを出力する

PRISM の CLI 機能として、プロパティ評価には properties ファイルと CSV 結果出力、パス生成には `-simpath` と `sep=comma` を使用する。CLI の詳細は実装対象の PRISM バージョンで `prism -help simpath` と `prism -help exportresults` を実行して固定する。

#### `targets/prism/backend.py`

- `TestCase` を受け取り `RawRunResult` を返す
- model catalog と profile を利用して実行内容を決定する
- PRISM の2回の invocation を調停する
- raw result、trace、log を evidence として保存する
- fixture mode では PRISM を起動せず既存成果物を返す

#### `targets/prism/trace_parser.py`

- `-simpath` の CSV を読み込む
- PRISM 固有の列を正規化された状態列へ変換する
- step の順序、必須変数、状態値を検証する
- PRISM のログ本文を解析しない

#### `targets/prism/result_interpreter.py`

- `RawRunResult` を `EvaluationRecord` へ変換する
- property result、trace summary、Maude verdict を統合する
- 実行失敗と解析失敗を区別する
- `ensure_evaluation_meta()` で必須 meta を設定する

#### `verifiers/maude/prism_checker.py`

この環境には Maude 3.5.1 本体と、既存 AW-CheckerPy が使う Python `maude` binding がすでにある。PRISM対応では新しいMaude処理系を導入せず、この既存 runtime を使う。

現行 `verifiers/maude/backend.py` と `AW-CheckerPy/aw_checkerpy.py` は、AWSIM の `groundtruth_kinematic`、車両サイズ、perception object を前提に独自の状態機械を組み立てる。その入力変換・Autoware用モデル・formula parser は PRISM trace には再利用できないため、変更しない。

PRISM 用には、次の小さな checker だけを追加する。

- 既存 `.venv/bin/python` の `maude` binding、または既存 `maude` executable を使用する
- 正規化済み PRISM trace JSON を読む
- `specs/prism_trace.maude` をロードする
- `EARLY_FAILURE` と `REPEATED_DEGRADATION` を評価する
- `safe`、`violation`、`invalid` と違反 rule ID を固定 JSON で出力する
- timeout、stdout、stderr、return code を構造化して返す

`prism_trace_evaluator.py` は checker が出す固定 JSON だけを解釈する。AW-CheckerPy の `Model checking result: True|False` を解析する既存 `evaluator.py` はそのまま AWSIM 専用として残す。

### 5.4 サンプリング単位

統計評価では、PRISM の1本のサンプルパスを1つの `EvaluationRecord` とする。1つの record に複数パスの集計値を入れる方式は採用しない。これにより、既存の `records_to_data_frame()` が1行1標本として扱える。

各 sample case には最低限、次を付与する。

```text
case_id       = prism-<run-id>-sample-000001
model_id      = simple_reliability_dtmc
sample_index  = 1
horizon       = 20
constants     = 同一条件の固定値
random_seed   = 利用する PRISM API が seed 指定を保証する場合のみ設定
reason        = PRISM_SMC
```

同じ統計バッチでは model、constants、horizon、初期状態を固定する。これらが異なる record を無条件に同じ二項CIやDKWへ混ぜない。集計時には `experiment_id` と `sampling_signature` で標本を分離する。

PRISM の厳密なモデル検査は sample case とは別に1回実行し、`record_kind=exact_model_check` として保存する。これはサンプル数に数えない。

### 5.5 既存統計ツールとの対応

初期版の対応関係を次のように固定する。

| 既存ツール | PRISM 用の標本 | 用途 | 初期版 |
|---|---|---|---|
| 二項信頼区間 | `failure_reached` 0/1 | horizon 内障害確率の推定 | 対応 |
| 二項信頼区間 | `maude_violation` 0/1 | Maude 規則違反率の推定 | 対応 |
| DKW | `steps_to_failure_capped` | 打ち切り故障stepの分布・分位点 | 対応 |
| DKW simultaneous | 上記 + `degraded_visit_count` | 複数指標の同時評価 | 対応 |
| GP 境界推定 | 入力定数 -> `failure_reached` | パラメータ空間の危険境界 | 対応。ただし第2段階 |
| FT4D | Maude 違反を basic event 化 | 複数規則のフォールトツリー集約 | 対応。ただし第2段階 |
| KDE 重点補正 | 適応的に選んだ PRISM 条件 | 重点サンプルの再利用 | 初期版では無効 |
| AWSIM 固有 edge mode | TTC / JAMA / collision region | 車両シナリオ探索 | 非対応 |

#### 二項信頼区間

`evaluation.binomial_ci.BinomialCIService` は任意の0/1列を扱えるため、計算本体は変更しない。PRISM sampling strategy が `failure_reached` または `maude_violation` を `EvaluationRecord.output` に出せば再利用できる。

初期版は純粋な一様サンプルパスだけを対象にし、次のように逐次停止する。

```text
collect sample path
  -> EvaluationRecord を保存
  -> Wilson / Clopper-Pearson CI を更新
  -> min_samples を満たし、interval_width <= target_width なら停止
  -> max_samples 到達でも停止
```

PRISM の厳密な `bounded_failure` が二項CIに含まれるかを比較し、`exact_value_in_interval` をレポートする。ただし、区間外だった場合も PRISM の厳密値を誤りと断定せず、sampling signature、乱数生成、parser、標本数を診断する。

#### DKW

`evaluation.dkw.DKWService` は数値列を扱えるため、`region="custom"`、`use_kde_weighting=False` で再利用する。初期版の対象は `steps_to_failure_capped` と `degraded_visit_count` とする。

PRISM の1本の path 内の各 step を別標本として DKW に入れてはならない。同一 path 内の状態は独立ではないため、1 path から1つの要約値だけを作る。

`steps_to_failure_capped` の DKW 結果は有限 horizon の打ち切り分布に対する評価であり、無限時間の真の故障時間分布とは表現しない。

#### GP 境界推定

GP のデモでは、モデル定数を特徴量として複数の sampling signature を作る。

```text
features = [p_normal_to_failure, p_degraded_to_failure]
target   = failure_reached
```

`evaluation.gp_boundary.GPBoundaryService` の学習本体は再利用する。一方、現在のデータ前処理には `c_collision`、`c_ttc_*`、`min_ttc`、`min_distance` など AWSIM 固有の優先保持処理がある。次のどちらかで分離する。

1. `prepare_training_data_frame()` に optional な `invalid_value_policy` と `must_keep_policy` を注入する
2. 対象非依存の最小前処理を新しい helper に切り出し、AWSIM 側だけ既存の優先保持 policy を渡す

第2案を推奨する。PRISM 側は `loop_num`、特徴量、0/1 target、欠損除外だけを要求する。既存 AWSIM の結果を変えない regression test を先に追加する。

GP は同一条件の path 確率を推定するものではなく、「遷移確率パラメータを変えたときの違反境界」を近似するデモとして位置づける。同一条件の厳密値との比較は二項CIが担当する。

#### FT4D

`evaluation.ft4d_service.FT4DService` と `verification_core/ft4d` は対象非依存なので、PRISM 用 `VerificationInput` builder と tree を追加して再利用する。

```text
verification_core/ft4d/config/tree_prism_demo.json

TOP_FAILURE
  OR
  ├── EARLY_FAILURE
  └── REPEATED_DEGRADATION
```

- `universal_dataset`: 統計バッチ内の有効な `case_id` 集合
- `dataset_d`: その basic event の判定対象になった `case_id` 集合
- `dataset_e`: Maude が違反と判定した `case_id` 集合
- `total_count`: `dataset_d` の件数
- `error_count`: `dataset_e` の件数

各 event の activation condition と `dataset_d` の意味を spec に明記する。すべての path を機械的に `dataset_d` に入れるのではなく、規則を評価できる path だけを対象にする。

FT4D の `sigma_pf` を PRISM の厳密な確率から与える場合は `sigma_pf_source=assumption` として出所を記録する。サンプルから計算した `sigma_pb` と厳密値を同一量として二重計上しない。

### 5.6 統計アダプタ

`targets/prism/statistical_adapter.py` は、PRISM record を既存評価サービスへ渡すための target 固有変換だけを担当する。

- `experiment_id` / `sampling_signature` による record 選択
- exact model-check record の除外
- status が `success` でない record の除外と件数報告
- metric の存在、型、有限値の検証
- 二項 metric が 0/1 であることの検証
- PRISM exact result との比較 summary 作成

統計計算式はここへ複製せず、必ず `BinomialCIService`、`DKWService`、`GPBoundaryService`、`FT4DService` を呼ぶ。

### 5.7 orchestration の汎用化

現在の統計計算サービスは概ね対象非依存だが、統計モードの orchestration には次の AWSIM 依存が残っている。

- `apps/cli/orchestrator_main.py` が strategy mode を `target=awsim` に限定している
- `ActiveLearningStrategist` が生成する target に AWSIM 固定箇所がある
- `DKWModeRunner` / `BinomialModeRunner` が `targets.awsim.theory` を直接 import している
- region filter が TTC / JAMA を前提にしている
- default metric が `c_collision`、`min_ttc`、`min_distance` である

これを一度に全面改修せず、次の境界を追加する。

```text
StatisticalSamplingStrategy
  inputs:
    target
    fixed_input
    sampling_signature
    metric
    min_samples / max_samples / target_width
    sample_case_factory
  output:
    next TestCase or stop
```

PRISM 用 `sample_case_factory` は、同じモデル・定数・horizon を持ち、sample index だけが異なる `TestCase` を返す。既存二項CI / DKWサービスによる停止判定はこの strategy から呼ぶ。

`DKWModeRunner` と `BinomialModeRunner` の AWSIM region 処理は、次の callable として注入可能にする。

```text
point_validator(point) -> bool
point_enricher(point) -> mapping
```

AWSIM は既存 theory / region 実装を adapter として渡し、PRISM の固定条件サンプリングは常に true の validator と no-op enricher を使う。これにより評価ロジックを複製せず、既存 AWSIM の挙動も維持する。

### 5.8 統計的妥当性の条件

統計モードを「対応済み」と表現する前に、次を満たす必要がある。

- 各 `EvaluationRecord` が異なる simulator path に由来する
- 同一バッチの model、constants、horizon、初期状態が固定されている
- PRISM simulator の乱数生成と seed / process 間独立性を Phase 0 で確認する
- 同じ seed または同じ path の再利用を重複排除する
- timeout や解析失敗を単純に安全標本 `0` と置換しない
- 適応的なパラメータ探索データを固定条件の二項CI / DKWへ混ぜない
- KDE weighting は理論条件を別途確認するまで PRISM では無効にする

seed を明示指定できない PRISM バージョンまたは実行方法の場合、独立性を説明できる実行方法へ変更する。確認できるまでは、統計結果を機能デモとして出力しても「保証」とは記載しない。

### 5.9 registry と CLI

`targets/registry.py` に `prism` 分岐を追加し、次を返す。

```text
backend            = PrismBackend
result_interpreter = PrismResultInterpreter
```

`apps/cli/worker_main.py` の `--target` choices に `prism` を追加する。ただし、現在の CLI には AWSIM 固有引数が多数存在するため、初期版では削除・再設計せず、PRISM target では無視される引数とエラーにする引数を明示する。

PRISM で受け付ける入力は、既存の反復可能な `--param key=value` から構築する。PRISM 専用引数が3個以上必要になった段階で、`apps/cli/prism_main.py` の追加を再検討する。

orchestrator には少なくとも次の実行形を追加する。

```bash
# 固定サンプル数で収集し、二項CIとDKWを最終評価
python3 run_orchestrator_v2.py \
  --target prism \
  --case-kind simple_reliability_dtmc \
  --mode statistical_fixed \
  --param steps=20 \
  --param p_fail=0.05 \
  --max-samples 500 \
  --output ./verification_results/prism_samples.jsonl

# 二項CIの幅で逐次停止
python3 run_orchestrator_v2.py \
  --target prism \
  --case-kind simple_reliability_dtmc \
  --mode binomial_ci \
  --binomial-target failure_reached \
  --binomial-target-width 0.05 \
  --max-samples 2000 \
  --output ./verification_results/prism_samples.jsonl
```

これは目標 CLI であり、既存 parser の `strategy_mode` 制限を解消してから受入コマンドとして固定する。

## 6. 成果物形式

PRISM の stdout を永続的な API として直接扱わず、backend が次の JSON を生成する。

```json
{
  "schema_version": 1,
  "model_id": "simple_reliability_dtmc",
  "model_type": "dtmc",
  "constants": {
    "STEPS": 20,
    "p_fail": 0.05
  },
  "property_results": {
    "eventual_failure": 1.0,
    "bounded_failure": 0.31
  },
  "trace": [
    {"step": 0, "state": 0, "state_label": "normal"},
    {"step": 1, "state": 1, "state_label": "degraded"}
  ],
  "execution": {
    "returncode": 0,
    "elapsed_sec": 0.12,
    "prism_version": "..."
  }
}
```

プロパティ名は properties ファイル内の順番だけに依存させず、model catalog で ID と property index の対応を固定する。

乱数 seed の指定と記録が利用する PRISM バージョンで可能かは Phase 0 で確認する。固定できない場合、パス生成の完全再現性を初期版の保証に含めず、モデル、定数、PRISM バージョン、生成されたパスそのものを証跡として保存する。

## 7. 実装フェーズ

### Phase 0: PRISM CLI スパイク

目的は、実装対象環境の CLI 入出力を fixture として固定することである。

- PRISM のインストール方法と実行ファイルパスを決める
- `prism -version` 相当の確認方法を決める
- `maude --version` と既存 AW-CheckerPy venv の `import maude` を確認する
- `.prism` モデルを構文解析・build できることを確認する
- properties の通常評価と CSV 出力を確認する
- `-simpath`、`vars=(...)`、`sep=comma` を確認する
- seed 指定の可否を確認する
- 正常、モデルエラー、timeout の stdout / stderr を fixture 化する

完了条件:

- 使用する PRISM バージョンと CLI command が文書化されている
- parser を実装できる正常・異常 fixture が揃っている

### Phase 1: PRISM 単体デモ

- DTMC モデルと properties を追加する
- model catalog と profile を追加する
- runner と trace parser を実装する
- 確率入力、CSV、timeout の unit test を追加する

完了条件:

- Python から PRISM を実行し、property result と trace JSON を生成できる
- PRISM 非導入環境でも fixture ベースの unit test が通る

### Phase 2: 共通 target 化

- `PrismBackend` と `PrismResultInterpreter` を実装する
- `targets/registry.py` に登録する
- worker CLI に `prism` を追加する
- `TestCase -> RawRunResult -> EvaluationRecord` の contract test を追加する

完了条件:

- `run_worker_v2.py --target prism ...` が JSONL を1行保存する
- AWSIM と BBSL の既存 registry test が回帰しない

### Phase 3: 固定数サンプリングと既存統計評価

- 1 path = 1 `EvaluationRecord` の sampling runner を追加する
- `experiment_id` と `sampling_signature` を保存する
- exact model-check record と sample record を分離する
- `failure_reached` を `BinomialCIService` で評価する
- `steps_to_failure_capped` を `DKWService` で評価する
- PRISM 厳密値との比較 summary を追加する
- `use_kde_weighting=False` を強制する

完了条件:

- 固定条件の N 本の path から二項CIとDKW report を生成できる
- exact value が統計標本数に含まれていない
- 異なる sampling signature が混在しない

### Phase 4: 逐次停止 orchestration

- `FixedParameterSamplingStrategy` を追加する
- 同一のtarget、case kind、paramsからsampling signatureを生成する
- `experiment_id` とsampling signatureが一致する結果だけを二項CIへ渡す
- orchestrator のPRISM `binomial_ci` modeから固定パラメータstrategyを選択する
- CI幅またはmax samplesによる停止を接続する
- `prism_main.py` の独自反復loopを削除し、同じorchestratorへ移す

完了条件:

- `target=prism --mode=binomial_ci` が必要標本数まで自動反復する
- 1 pathごとに一意のsample indexを持つ1件の`TestCase`を生成する
- 別experimentまたは別sampling signatureの結果を混ぜない
- AWSIM の既存 binomial / DKW regression test が変わらない

実装済み。DKWを停止条件として使うstrategyと中断再開時の未完了task復元は、次の拡張段階で追加する。

### Phase 5: 統計モードからAWSIM固定処理を分離

- target別の`StatisticalTargetProfile`を追加する
- AWSIMは`c_collision` / `min_ttc` / AWSIM theory regionを選択する
- PRISMは`c_failure` / `steps_to_failure_capped` / `custom`を選択する
- theory行の生成を`targets/awsim/statistical.py`へ閉じ込める
- binomial / DKW modeへtarget別region policyを注入する
- 評価サービスから`c_collision`などのmetric名固定処理を除く

完了条件:

- `orchestration/binomial_mode.py`と`dkw_mode.py`が`targets.awsim`をimportしない
- PRISMデータにAWSIM theory列を要求しない
- AWSIMのnamed regionによる棄却samplingが従来どおり動作する

実装済み。

### Phase 6: PRISM厳密モデル検査と標本生成を分離

- runnerを`run_prism_model_check`と`run_prism_sample_path`に分離する
- 司令塔が実験開始時にexact model-check TestCaseを1件だけ投入する
- exact record完了後にsample-path TestCaseを投入する
- sample pathだけをMaudeで判定する
- exact recordに`c_failure`を持たせず統計標本から除外する
- bounded exact probabilityと二項CIの比較を最終reportへ追加する

完了条件:

- N標本でproperties実行が1回、`-simpath`実行がN回になる
- exact recordを二項CI / DKWのsample countへ含めない
- 複数workerでもexact model-check taskが1件だけ発行される
- 最終reportから厳密確率、CI包含判定、推定誤差を確認できる

実装済み。

### Phase 7: checkerのコード配置をREADMEへ合わせる

- PRISMの実行・変換責務を`targets/prism/`へ配置する
- Maude checker、固定出力evaluator、specを`verifiers/maude/`へ配置する
- `profile.py`でTestCase入力の検証と型変換を行う
- `dataset_adapter.py`でexact recordとsample recordを分離する
- 旧`targets.prism.maude_checker`は互換importだけにする
- 実行コンテナは従来どおり`prism_maude`を使用する

完了条件:

- `result_interpreter.py`が`verifiers.maude`のcheckerとevaluatorを利用する
- Maude checkerが固定schemaの辞書を返し、evaluatorがmetricへ変換する
- READMEのディレクトリ構成と実コードが一致する
- 旧checker import pathも後方互換で利用できる

実装済み。

### Phase 8: 共通cluster CLI

- `run_orchestrator_cluster_v2.py` から `target=prism` と `container-profile=prism_maude` を選べるようにする
- `case-kind`、二項CI設定、反復可能な `--param key=value` を共通の `OrchestratorConfig` へ渡す
- PRISMではGPU preflight、GPU watchdog、ROS起動を無効のまま維持する
- 各workerの完全な `EvaluationRecord` を共有store actor経由で司令塔の `--output` JSONLへ集約する
- 同じrecordを既存統計評価用の `--dataset-csv` へ集約する
- workerごとのJSONLは障害調査用成果物として維持し、司令塔JSONLと役割を分ける
- `prism_main.py` は削除せず、単一containerのsmoke/demo入口として残す

完了条件:

- README記載の1コマンドが、既存のcluster manager、Ray queue、workerを通ってPRISM標本を処理できる
- `--output` にexact recordとsample recordの完全な共通JSONLが生成される
- `--dataset-csv` を既存の二項信頼区間停止判定が読み取れる
- CLI contract testでtarget、profile、型変換後の全パラメータ、統計設定、出力先を固定する
- AWSIM profileの起動設定と結果保存が回帰しない

実装済み。2026-09-15に21・22・23・24号機の実4ノードクラスタで検証済み(25号機は`cluster_config.py`で`enabled: False`のため対象外)。詳細は「16. 実クラスタ検証結果」を参照。

標準コマンド:

```bash
python3 run_orchestrator_cluster_v2.py \
  --target prism \
  --container-profile prism_maude \
  --case-kind simple_reliability_dtmc \
  --mode binomial_ci \
  --binomial-target c_failure \
  --binomial-confidence 0.95 \
  --binomial-target-width 0.3 \
  --max-samples 60 \
  --param steps=20 \
  --param p_normal_degrade=0.1 \
  --param p_normal_failure=0.01 \
  --param p_degraded_normal=0.3 \
  --param p_degraded_failure=0.1 \
  --output /home/passd/prism_results/records.jsonl \
  --dataset-csv /home/passd/prism_results/dataset.csv
```

### Phase 9: デモと文書化

- PRISM と Maude を実際に使う smoke test を追加する
- README にインストール前提と1コマンドの例を追加する
- サンプル出力を `tests/fixtures/prism/` に保存する
- 対応範囲を「DTMC のデモ」と正確に記載する
- exact model checking、サンプル統計、Maude、FT4D の違いを説明する

完了条件:

- 初見の利用者が README の手順だけでデモを実行できる
- PRISM / Maude がない環境でのエラーが理解可能である
- 二項CI / DKW / GP / FT4D のうち有効化した機能が一覧で確認できる

進捗(2026-09-15時点):

- 実4ノードクラスタでの手動検証(40本疎通 + 200〜500本×5条件)は完了。「16. 実クラスタ検証結果」参照
- 未対応: pytest によるPRISM/Maude実行込みの自動smoke test、`tests/fixtures/prism/` へのサンプル出力保存
- README には運用手順・実行例を追記済み。「対応範囲はDTMCのデモ」である旨の明記はまだ未実施

## 8. テスト計画

### 8.1 unit test

- model ID の解決
- 未知の model ID の拒否
- 定数の型・範囲・確率和
- command が引数配列として生成されること
- PRISM CSV の正常解析
- 列欠落、空 trace、非単調 step の拒否
- property result の正常解析
- Maude verdict の3分類
- timeout と非0終了コードの status 変換
- sampling signature の一致・不一致
- exact result が標本から除外されること
- `failure_reached` の二項CI変換
- `steps_to_failure_capped` のDKW変換
- 同じ seed / case ID の重複排除
- PRISM event set から `VerificationInput` への変換
- GP の汎用前処理と AWSIM policy の回帰

### 8.2 contract test

- `TestCase(target="prism") -> RawRunResult(target="prism")`
- `RawRunResult -> EvaluationRecord`
- `EvaluationRecord.meta` に `schema_version` と `source_module` が存在する
- evidence が既存の標準キーを使う
- 1 sample path が1つの `EvaluationRecord` になる
- `StatisticalRequest -> StatisticalReport` が既存契約のまま成立する
- `VerificationInput -> FT4DResult` が既存契約のまま成立する

### 8.3 smoke test

- PRISM 実体を使う DTMC 実行
- Maude 実体を使う trace 判定
- worker CLI から JSONL 保存までの end-to-end 実行
- 複数 path 収集から二項CI / DKW report 生成まで
- PRISM exact bounded probability と二項CIの比較
- PRISM Maude events から FT4D result 生成まで

実体を必要とする smoke test には marker を付け、通常の unit test から分離する。CI では PRISM / Maude を用意した専用 job のみで実行する。

## 9. エラー処理

| 事象 | `RunStatus` | 方針 |
|---|---|---|
| PRISM executable がない | `execution_error` | 検出した path と導入案内を記録 |
| モデル ID が未知 | `invalid` | PRISM を起動しない |
| 定数が不正 | `invalid` | PRISM を起動しない |
| PRISM timeout | `timeout` | log を evidence に残す |
| PRISM 非0終了 | `execution_error` | stdout / stderr を保存 |
| CSV / JSON 解析失敗 | `analysis_error` | raw evidence を保持 |
| trace が空または不整合 | `invalid` | 理由を output / meta に保存 |
| Maude 起動失敗 | `analysis_error` | PRISM 結果は保持し、判定失敗を明記 |
| Maude が `invalid` を返す | `invalid` | 規則名と理由を保存 |
| sampling signature が異なる | 統計対象外 | 混在件数を diagnostics に保存 |
| metric が欠落・型不正 | 統計対象外 | 除外件数と case ID を diagnostics に保存 |
| 独立性を確認できない | 実行停止または非保証 | 保証を示す report を生成しない |

PRISM のプロパティ評価に成功し、Maude だけが失敗した場合に record 全体を `analysis_error` とするか、部分成功として扱うかは初期実装前に固定する。本計画では、デモの完了条件に Maude 判定を含めるため `analysis_error` を採用する。

## 10. セキュリティと再現性

- 利用可能なモデルを catalog で制限する
- model path、properties path、任意の PRISM option を利用者入力として直接受け取らない
- subprocess は `shell=False` で実行する
- 出力は case ごとの専用ディレクトリに限定する
- timeout と出力サイズ上限を設ける
- command、モデル ID、定数、PRISM / Maude バージョンを記録する
- fixture にホスト固有の絶対パスを含めない
- model と properties はリポジトリでバージョン管理する

## 11. README での表現

初期版完成後は、次の範囲でアピールする。

> 本フレームワークは AWSIM / Autoware に加え、PRISM の DTMC を独立した検査対象として実行できる。PRISM による確率的モデル検査とサンプルパス生成、Maude によるトレース規則判定を、共通の実行・結果契約へ接続している。生成した反復パスは、共通の二項信頼区間、DKW、GP 境界推定、FT4D 評価へ入力できる。

次のような過剰な表現は避ける。

- 任意の PRISM モデルへ対応済み
- MDP / CTMC を完全サポート
- PRISM と Maude による数学的保証を統合済み
- PRISM の厳密値と標本統計を合成した新しい保証を実現済み
- AWSIM の挙動を形式モデルで証明済み

## 12. 将来拡張

初期版の完了後、必要性が確認できたものだけを追加する。

1. CTMC の時間付き path parser
2. MDP の strategy 指定と `Pmin` / `Pmax` 結果
3. PRISM の approximate model checking
4. PRISM target 用の専用 CLI
5. Docker image による PRISM / Maude バージョン固定
6. 他の Maude spec を登録できる verifier catalog
7. 理論的に妥当性を確認した上での適応サンプリング補正

MDP のランダムパスは nondeterministic choice の解消方法に依存するため、単純な DTMC と同じ意味で扱わない。対応時には strategy と simulator の選択規則を結果へ必ず記録する。

## 13. 変更予定ファイル

初期版で変更する既存ファイルは、原則として次に限定する。

- `targets/registry.py`
- `apps/cli/worker_main.py`
- `apps/cli/orchestrator_main.py`
- `orchestration/orchestrator.py`
- `orchestration/binomial_mode.py`
- `orchestration/dkw_mode.py`
- `orchestration/strategy.py`
- `evaluation/gp_boundary.py`
- `README.md`
- `tests/unit/targets/test_registry.py`

新規実装の大部分は `targets/prism/`、`verifiers/maude/` の追加ファイル、`tests/fixtures/prism/` に閉じ込める。統計モードの既存ファイルは AWSIM 固有 policy を注入可能にする範囲だけ変更する。`orchestration/worker_loop.py`、`contracts/*`、既存 AWSIM / BBSL target 実装は、共通契約で不足が見つからない限り変更しない。

## 14. 初期見積もりと進捗

| 作業 | 目安 |
|---|---:|
| Phase 0: CLI スパイクと fixture | 0.5〜1日 |
| Phase 1: PRISM runner / parser | 1〜2日 |
| Phase 2: target / worker 統合 | 1日 |
| Phase 3: 固定数サンプリングと二項CI / DKW | 1〜2日 |
| Phase 4: 逐次停止 orchestration | 1〜2日 |
| Phase 5: 統計modeのtarget policy分離 | 実装済み |
| Phase 6: exact model check / sample path分離 | 実装済み |
| Phase 7: checker / targetの責務配置整理 | 実装済み |
| Phase 8: 共通cluster CLI / 結果集約 | 実装済み |
| Phase 9: smoke / README | 0.5〜1日 |
| 初期合計 | 6.25〜10.5日 |

このうち FT4D 本体の変更は含まない。`FT4DService` と `verification_core/ft4d` は既に target 非依存であり、PRISM 側で必要なのは `VerificationInput` builder、basic event の集合化、デモ用 tree の追加だけである。PRISM と Maude が導入済みで、CLI 出力の差異が小さい場合の目安である。Maude 3.5.1 と既存 Python binding を再利用し、AW-CheckerPy のAWSIM固有入力へ PRISM trace を無理に合わせず、小さな PRISM 用 spec / checker を追加する前提とする。

## 15. 参考資料

- [PRISM Manual](https://www.prismmodelchecker.org/manual/)
- [PRISM: Starting PRISM](https://www.prismmodelchecker.org/manual/RunningPRISM/StartingPRISM)
- [PRISM: Debugging Models With The Simulator](https://www.prismmodelchecker.org/manual/RunningPRISM/DebuggingModelsWithTheSimulator)
- [PRISM: Exporting Results](https://www.prismmodelchecker.org/manual/RunningPRISM/Experiments#exportresults)

## 16. 実クラスタ検証結果 (2026-09-15)

21・22・23・24号機の実クラスタ(25号機は`enabled: False`のため対象外)で、`run_orchestrator_cluster_v2.py --target prism` を段階的に実行して検証した。

### 16.1 検証中に見つかった不具合と修正

実クラスタでの実行で、これまでの単体container smoke(`apps.cli.prism_main`)では踏まなかった3件の不具合が見つかり、修正済み。

1. **Ray headの`--num-cpus=0`固定によるRay Client初期化失敗**: PRISM workerはRay nodeとして参加しないclient-only構成のため、headのCPUが0だとRay Clientの初期化自体ができず`ray_control_plane_lost`で全workerが起動前に落ちていた。`runtime/cluster/cluster_manager.py`から`--num-cpus`指定自体を削除し、Rayの標準動作(実ホストの論理コア数を自動検出)に戻した。AWSIM側の起動条件・actor配置には影響しない(制御用actorは`num_cpus=0`かつ`pin_to_current_node=True`で固定のため)。
2. **PRISMコンテナとRay headのPythonバージョン不一致**: `docker/prism-maude.Dockerfile`はUbuntu 24.04ベースで、標準の`python3`が3.12だった。一方クラスタのRayはPython 3.10で動作しており、Ray Clientはクライアントとサーバのバージョンが一致しないと接続を拒否する。deadsnakes PPAで`python3.10`/`python3.10-venv`を追加し、venvを`python3.10`で作成するよう変更(PRISM本体はJava、MaudeはネイティブバイナリなのでUbuntu 24.04のglibc要件には影響しない)。
3. **統計サマリのJSON化失敗**: `binomial_ci`のreportに含まれる`numpy.bool_`が標準の`json.dumps`でシリアライズできず、実行自体は成功していても`orchestrator_cluster_main.py`の最終出力でクラッシュしていた。`apps/cli/prism_main.py`と同じ`_json_default`(numpyスカラーを`.item()`で変換)を追加して解消。

### 16.2 40本疎通試験

4台へ分配、exact検査1回、Maude判定、中央JSONL/CSV集約を確認。41件(exact 1 + sample 40)が生成され、worker間で分散、`worker_exit_code=0`。

### 16.3 基準条件 + 4条件(200〜500本、95% Wilson CI幅0.10で逐次停止)

| 条件 | 変更点 | 標本数 | c_failure推定 | exact確率 | 絶対誤差 | 実行時間 | early_failure違反 | repeated_degradation違反 |
|---|---|---|---|---|---|---|---|---|
| 基準 | (baseline) | 376 | 0.402 | 0.423 | 0.021 | 104秒 | 37 | 34 |
| B: 低リスク | `p_normal_failure=0.001` | 336 | 0.295 | 0.326 | 0.031 | 98秒 | 18 | 36 |
| C: 高リスク | `p_normal_failure=0.05`, `p_degraded_failure=0.3` | 227 | 0.833 | 0.841 | 0.009 | 73秒 | 60 | 8 |
| D: 長horizon | `steps=100` | 128 | 0.914 | 0.946 | 0.032 | 29秒 | 12 | 67 |
| E: 回復弱 | `p_degraded_normal=0.05` | 376 | 0.604 | 0.592 | 0.012 | 92秒 | 41 | 1 |

全5条件でexact確率(厳密モデル検査)がサンプルの95%CI内に収まった(`ci_contains_bounded_probability: true`)。逐次停止は真の失敗確率が高いほど少ない標本数(D: 128本)で、中程度だと多め(基準/E: 376本)で打ち切られており、統計的に妥当な挙動だった。

条件C・Dでは`early_failure`・`repeated_degradation`のMaude違反判定が実際に発火することを確認できた。基準条件の40本・376本ではいずれも全サンプルが"safe"判定で、違反検知の経路自体は未検証だった。

worker負荷分散は概ね均等だが、条件Dでは標本数が少なく(128本)収束が速かったため、後から参加したworker(22号機)がタスクを1件も受け取れずに終了する例が発生した。`exit_code=0`で正常終了しており不具合ではない。

成果物(未コミット): `verification_results/prism_stage2_baseline.{jsonl,csv}`、`verification_results/prism_stage3_{B_lowrisk,C_highrisk,D_longhorizon,E_weakrecovery}.{jsonl,csv}`。
