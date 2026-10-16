# 重点サンプリング (固定P・固定Q) の使い方

稀な事象 (衝突・故障) の確率を少ない標本で推定するため、評価したい元の分布 **P** を固定したまま、
人間が設定した提案分布 **Q** で標本を生成し、各標本に P/Q の重みを記録します。
対象は PRISM、連続ダイナミクス (ODE/SDE)、AWSIM です。

- 現在の範囲は、**固定P・人手で指定した固定Q** での標本生成と、通常の P 標本との固定標本比較です。
  Q の自動更新や停止判定は扱いません。
- P/Q の定義、標本の追跡、比較の考え方は [importance_sampling_design.md](importance_sampling_design.md)、
  AWSIM の入力重点サンプリングは [awsim_importance_sampling_design.md](awsim_importance_sampling_design.md) を参照してください。

## PRISM重点サンプリング（固定P・固定Q）

PRISM の `simple_reliability_dtmc` では、元のモデル遷移確率を **P** として固定したまま、人間が設定した提案遷移確率 **Q** で経路を生成できます。各経路に `log_p`、`log_q`、`log_importance_weight = log(P/Q)` を記録し、通常重要度推定でP上の故障確率を推定します。

標準運用は固定標本比較です。Qによる効率は、通常P標本との推定値、**同じ固定時点Hoeffding区間による区間幅**、ESS、最大重みを同じ標本数で比べて人間が判断します。Pのモデル確率を変更したり、Qを自動採用したりはしません。

```bash
# Q1で100件の重点標本と通常P標本を比較する
python3 -m apps.cli.prism_importance_main \
  --sampling-plan configs/prism_rare_event_q1.yaml \
  --output-root artifacts/prism_rare_event_q1_fixed_100 \
  --proposal-mode fixed_samples \
  --max-samples 100 \
  --sampling-seed 20261009
```

結果は `ordinary/records.jsonl`、`importance/records.jsonl`、`comparison.json` に保存されます。`comparison.json` の重点側は、PRISMでP/Qを厳密に計算できるため、自己正規化版ではなく通常重要度推定を既定として使います。

停止判定、候補Qの `suggest`、承認・自動更新、BBSLなどへのadapter追加は将来の拡張例です。AWSIMの固定P・固定Q入力重点サンプリングは設計済みですが、実装・実験の完了条件は [AWSIM入力重点サンプリング設計](awsim_importance_sampling_design.md) に分離しています。

## 微分方程式の入力重点サンプリング（固定P・固定Q）

U-turn ODEでは、ODEの式・キャリブレーション・ノイズ設定を変更せず、`dx0`、`ego_speed`、`npc_speed` の**入力分布**をP/Qとして扱います。Pは評価したい元の一様入力分布、Qは人間が設定する標本生成用の分布です。QはPの全範囲を残す混合分布に限定しているため、入力ごとの `log_p`、`log_q`、`log_importance_weight = log(P/Q)` を正確に記録できます。

実装済みの範囲は、固定P・人手指定の固定Q・指定件数の標本生成・P/Q重み付き比較です。SDEでは、Euler--MaruyamaのBrownian増分について、人間指定のP/Q Gaussian drift schedule（直接Qまたはdefensive Gaussian Q）を評価し、入力とノイズ経路を合わせたP/Q重みを記録します。拡散係数の変更やQの自動更新は扱いません。数式と設定は[重点サンプリング設計のSDE節](importance_sampling_design.md#sdeノイズ経路の重点サンプリング)を参照してください。

### 1. まず Q=P で計算経路を確認する

`configs/dynamics_importance_baseline.yaml` はQをPと同一にした検証用設定です。同じseedでは通常標本と重点標本の入力列が一致し、各標本の対数重みは0、ESSは標本数になります。

```bash
python3 -m apps.cli.dynamics_importance_main \
  --sampling-plan configs/dynamics_importance_baseline.yaml \
  --output-root artifacts/dynamics_q_equals_p \
  --max-samples 100 \
  --sampling-seed 42
```

### 2. 人間が設定した Q を固定標本で比較する

例えば `configs/dynamics_importance_q4.yaml` は、衝突が起きやすいと考えた入力領域へ `dx0`、ego速度、NPC速度を偏らせるQです。Pは変更されません。

```bash
python3 -m apps.cli.dynamics_importance_main \
  --sampling-plan configs/dynamics_importance_q4.yaml \
  --output-root artifacts/dynamics_importance_q4 \
  --max-samples 1000 \
  --sampling-seed 20261011
```

結果は次に保存されます。

- `ordinary/records.jsonl`: Pで生成した通常標本
- `importance/records.jsonl`: Qで生成した重み付き標本
- `comparison.json`: 推定値、ESS、最大重み、区間幅、標本数

比較器は `target`、`case_kind`、`target_measure_id` の一致を検査するため、異なる対象やPを誤って比較しません。`comparison.json` には二つの目的の区間を分けて保存します。

- `diagnostic`: Q候補の効率比較用。重点推定の正規近似区間幅、ESS、複数seedでの推定値のばらつきを見る。
- `comparable`: 重み上限を用いた固定時点Hoeffding区間。採用後の固定Qについて、保証を確認するために使う。

重み上限が大きいQはHoeffding区間だけでは不利に見えるため、**Q候補の順位付けに `comparable` の区間幅は使いません**。

### 3. 複数の人手候補を同じ条件で比べる

候補Qごとに同じ外側seed集合・同じ標本数で実行します。外側seedから各標本のseedを重複しない範囲へ写像するため、別seed実験の標本は重なりません。

```bash
for candidate in q2 q3 q4; do
  for seed in $(seq 1 20); do
    python3 -m apps.cli.dynamics_importance_main \
      --sampling-plan "configs/dynamics_importance_${candidate}.yaml" \
      --output-root "artifacts/dynamics_candidate_ranking/${candidate}/seed_${seed}" \
      --max-samples 100 \
      --sampling-seed "${seed}"
  done
done
```

実行例では、Q2・Q3・Q4を20 seed × 各100標本で比較しました。診断用区間幅の平均はQ4が `0.02125`、Q3が `0.02476`、Q2が `0.02677` でした。一方で平均ESSはQ4が約`82.5`、Q3が約`83.7`、Q2が約`87.2` です。この結果はQ4を**次に人間が検討する候補**とする根拠であり、自動承認・自動適用を意味しません。
