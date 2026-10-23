# AWSIM 入力重点サンプリング設計

## 1. 目的と対象範囲

Autoware 1.7.1 と AWSIM を実行対象に、**人間が定義した固定P・固定Q**による入力重点サンプリングを追加する。

- **P**: 評価したい運用・シナリオ入力の分布。実験中に変更しない。
- **Q**: 危険側の入力を多く生成するために人間が指定する提案分布。Pは変更しない。
- **推定対象**: 固定Pの下での `c_collision` などの二値指標の確率。
- **初期ケース**: U-turn。`dx0` は固定したシナリオ条件、ego/NPC速度を確率入力とする。

本設計は、危険ケース探索、Autowareの制動設定変更、Qの自動更新、逐次停止を含まない。これらは本実験の結果を使う将来の拡張であり、混ぜない。

## 2. 意味を混同しないための境界

```text
人間が定義するP                人間が定義するQ
速度データ・適用条件             危険側へ偏らせる標本生成分布
          │                                  │
          └──────── AWSIM sampling adapter ─┘
                             │
                 TestCase.input + meta.sampling
                             │
              AWSIMBackend → AWSIMResultInterpreter
                             │
                  EvaluationRecord（P/Q重み付き）
                             │
       共通の重要度推定・ESS・比較区間・結果保存
```

`dx0` と `trigger_gap_sec` は同じ開始条件を別の座標で表す補助量である。初期実装では、シナリオ生成に使う既存の `dx0` を正本にする。`trigger_gap_sec` を独立な確率変数として追加しない。必要なら、実現した速度と `dx0` から導出して記録するだけにする。

## 3. PとQの定義

### 3.1 P: 固定する評価対象

Pは「どの道路・時間帯・車種等に条件付けた速度分布か」を含めて人間が定義する。国土交通省等の観測データを使う場合、少なくとも次を固定・記録する。

- 入力データの識別子、版、取得元、整形手順のハッシュ
- 適用条件（地点、時間帯、対象車種、速度単位）
- ego/NPC速度を独立とみなすか、結合分布として扱うか
- 分布の表現（例: ヒストグラム、区分一定密度、経験分布）
- Pのsupportと、固定する `dx0` 条件または `dx0` 層

相手車両の発進・Uターン開始時刻の観測分布がない限り、その時刻分布をPに含めない。`dx0`を条件として、

```text
Pr(c_collision | dx0 in B, P_speed)
```

を推定する。複数の `dx0` 層を集計して現実交通全体の確率と呼ぶには、別途その層の確率Pが必要である。

### 3.2 Q: 人間が指定する標本生成方法

QはPのsupportを必ず残す。最初に対応するQは、Pの成分を残した混合分布とする。

```text
Q(v) = b P(v) + (1 - b) R(v),  0 < b <= 1
```

ここで `R` は人間が選ぶ危険側速度領域への分布である。この形ならPが正の入力でQがゼロにならず、

```text
w(x) = P(x) / Q(x)
log_importance_weight = log_p - log_q
```

を計算できる。Qの候補探索・自動採用は行わない。

## 4. 設定の正本

実験ごとに `configs/awsim_importance_<experiment>.yaml` を置く。共通層は既存の `SamplingPlan` の外枠だけを検証し、AWSIM固有の `definition` は `targets/awsim/sampling.py` が実行直前に検証する。

```yaml
schema_version: 1
experiment_id: awsim_uturn_speed_p_v1
target: awsim

target_measure:
  id: shibuya_speed_conditioned_dx0_v1
  definition:
    case_kind: uturn
    fixed_inputs:
      dx0: 15.0
    data_provenance:
      source_id: mlit_shibuya_speed_v1
      source_version: "<data-version-or-hash>"
      unit: kmh
      joint_model: independent_marginals
    parameters:
      ego_speed:
        distribution: histogram
        bins:
          - {min: 30.0, max: 35.0, weight: 0.50}
          - {min: 35.0, max: 40.0, weight: 0.50}
      npc_speed:
        distribution: histogram
        bins:
          - {min: 10.0, max: 15.0, weight: 0.60}
          - {min: 15.0, max: 20.0, weight: 0.40}

proposal:
  id: low_npc_high_ego_q1
  mode: manual_proposal
  definition:
    parameters:
      ego_speed:
        distribution: mixture_target_focus
        base_weight: 0.80
        focus:
          distribution: histogram
          bins: [{min: 35.0, max: 40.0, weight: 1.0}]
      npc_speed: {distribution: target}
```

上の `bins` は設計例であり、共通スキーマに固定しない。観測データの表現と読み方はAWSIM adapterの責務である。
コンテナprofile、scenario profile、headlessはplanではなくCLIの実行条件として指定し、実行結果metadataへ記録する。

## 5. モジュール責務と再利用

| 区分 | 配置 | 責務 |
|---|---|---|
| AWSIM固有の追加 | `targets/awsim/sampling.py` | P/Qの解釈、速度入力の標本化、support検証、`log_p` / `log_q` の計算 |
| AWSIM固有の追加 | `targets/awsim/importance_sampling.py` | AWSIM用の`TestCase`を発行し、固定標本実験を接続（plugin の `importance_sampling` から呼ばれる） |
| 共通として再利用 | `orchestration/sampling_plan.py` | 設定外枠の読込。AWSIM固有definitionは解釈しない |
| 共通として再利用 | `contracts/sampling.py` | `meta.sampling`の条件付き必須項目の検証 |
| 共通として再利用 | `targets/awsim/backend.py` / `result_interpreter.py` | AWSIM実行、trace判定、sampling metadataの引継ぎ |
| 共通として再利用 | `runtime/repository/importance_sampling_results.py` | ordinary / importance のrecordsと比較結果の保存 |
| 共通として再利用 | `evaluation/importance_sampling*.py` | 重み付き推定、ESS、重み上限を使う比較区間 |

既存 `orchestration/random_parameter_sampling.py` と Active Learning は利用しない。これらは通常標本・探索用であり、Qの密度、support、P/Q重みを扱わないためである。

PRISM・Dynamics・AWSIMで重複する「ordinaryとimportanceを別保存し、固定件数実行して比較する」制御は、AWSIMを追加する前に target 非依存の小さな実行helperへ切り出す。targetごとのP/Q解釈や`TestCase`生成は共通化しない。

## 6. データの流れ

```text
sampling_plan.yaml
  → AWSIMInputSampler
      → ordinary: Pから速度を標本化
      → importance: Qから速度を標本化し log(P/Q) を付与
  → TestCase(input: dx0, ego_speed, npc_speed, ...;
             meta.sampling: P/Qの追跡情報)
  → AWSIMBackend
  → RawRunResult.meta.sampling
  → AWSIMResultInterpreter
  → EvaluationRecord.meta.sampling
  → ImportanceSamplingResultsRepository
  → 共通比較器
  → comparison.json
```

AWSIMの内部乱数をP/Qの対象に含めない初期実装では、既存どおり `-noise false` を使用し、残る乱数源があればseed・image・scenario設定を記録する。内部確率を変更する重点サンプリングは別設計にする。

## 7. 段階的な実装と確認ゲート

1. **設計・fixture**: 実データをまだ読み込まず、合成した小さなヒストグラムP/Qをfixtureとして、density・support・重みの単体テストを書く。
2. **AWSIM adapter**: `targets/awsim/sampling.py` を追加し、P/Qから同じ`TestCase.input`を作れることを契約テストで確認する。
3. **実行接続**: AWSIM runnerとCLIを追加する。CLIはplanの場所、seed、件数、出力先をorchestrationへ渡すだけにする。
4. **Q=P回帰**: 同一seed・同一固定`dx0`でordinaryとimportanceを実行する。入力列が一致し、全標本で`log_p=log_q`、重みが1、ESSが標本数、同じ比較区間になることを確認する。
5. **AWSIM smoke**: fixtureだけでなく、実コンテナで1件のordinaryと1件のimportanceを実行し、trace、判定、`meta.sampling`、records保存までを確認する。
6. **少数固定Q実験**: 人間が指定したQで10〜20件を実行し、support外入力がないこと、重みが有限であること、通常標本と重点標本が混ざらないことを確認する。
7. **本実験**: 固定P・固定Q・固定`dx0`層・固定件数で実行する。比較に使うのは推定値、ESS、重み、共通Hoeffding区間であり、Qの自動採用はしない。

各段階で、unit test、既存AWSIM 1件smoke、前段の出力形式との回帰確認を満たしてから次へ進む。

## 8. 成果物と非対象

実験成果物は、`ordinary/records.jsonl`、`importance/records.jsonl`、`comparison.json`、使用したplanのコピーまたはハッシュ、Pデータの版情報である。

今回の非対象は次である。

- Autowareの制動・planner・モデル確率の変更
- Qの提案、承認、自動更新
- 重み付き逐次停止
- 観測されていないUターン開始時刻分布の推定
- AWSIM内部ノイズ・制御乱数への重点サンプリング
