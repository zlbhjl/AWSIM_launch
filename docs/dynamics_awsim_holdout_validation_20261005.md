# Dynamics / Autoware 1.7.1 hold-out比較（2026-10-05）

## 目的とデータ分離

保存済みAutoware 1.7.1 Uターン実験を使い、ODE surrogateの外部妥当性を確認した。
制動profileの推定に使った56件は除外し、事前に分離したhold-out 24件だけを評価した。
欠損traceは0件だった。全24件の保存シナリオ配置は `ego lane=514, offset=38` であり、
現在の速度帯別AWSIM profileとは配置条件が異なる可能性がある。

比較したモデルは次の2種類である。

- calibrated: OBB車体判定と `autoware171_uturn_calibrated` 制動profile
- JAMA baseline: 同じOBB・幾何条件とJAMA ai_aeb制動profile

## 衝突ラベル

| モデル | TP | TN | FP | FN | Accuracy | Sensitivity | Specificity |
|---|---:|---:|---:|---:|---:|---:|---:|
| Autoware校正 | 6 | 10 | 8 | 0 | 66.7% | 100.0% | 55.6% |
| JAMA baseline | 1 | 18 | 0 | 5 | 79.2% | 16.7% | 100.0% |

開始幾何校正後はAWSIM衝突6件をすべて検出した。一方、非衝突8件を危険候補として過検出した。
JAMAの高いaccuracyは非衝突多数によるもので、衝突検出は1/6件に留まった。

## TTC

AWSIMの連続TTCは既存CVM extractorで再計算した。有限値を両側で得られた13件の結果は次の
とおりである。

| モデル | MAE [s] | Median absolute error [s] | Bias (dynamics - AWSIM) [s] |
|---|---:|---:|---:|
| Autoware校正 | 0.231 | 0.300 | -0.046 |
| JAMA baseline | 0.754 | 0.700 | +0.754 |

AWSIMと同じCVM OBB予測へ統一した結果、校正モデルのMAEは0.231秒、biasは-0.046秒となった。

Maudeの離散TTCラベルに対する校正モデルのaccuracyは、閾値0.3秒で70.8%、0.5秒で83.3%、
0.7秒で87.5%、0.9秒で83.3%、1.1/1.2秒で87.5%、1.3/1.5秒で75.0%だった。
Maude TTCラベルとCVM連続TTCは異なる評価経路なので、両者を同一測定値として混同しない。

## 結論

開始幾何、車体中心offset、CVM TTCの修正により、この24件では衝突見逃しが0になった。
ただしfalse positiveが残るため、用途は「候補を広めに抽出してAWSIMへ戻す」段階に限定する。

この24件は開発結果を確認済みなので最終評価には再利用しない。別途、衝突50件・非衝突50件を
事前確保して最終評価した。

## 成果物

- `artifacts/dynamics_awsim_holdout_validation_20261005/per_case.csv`
- `artifacts/dynamics_awsim_holdout_validation_20261005/summary.json`
- 実行ツール: `tools/analysis/validate_dynamics_against_awsim.py`
