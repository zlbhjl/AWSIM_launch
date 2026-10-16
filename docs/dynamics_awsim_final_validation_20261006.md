# Dynamics / Autoware 1.7.1 最終比較（2026-10-06）

> v2で置き換え済み: `docs/dynamics_awsim_final_validation_v2_20261005.md`。この文書のTTC MAEは、
> 速度ベクトルをyawで二重に回転する `AW_Kinematics_Extractor` CVMの値を基準にしており、
> v2の数値とは比較できない。この100件は結果を確認済みのため、v2では開発評価に使用した。

## 評価条件

制動・開始幾何の校正56件と開発確認24件を除外し、結果を見る前に別の100件を固定した。
クラス偏りを避けるためAWSIM衝突50件、非衝突50件を層化抽出した。欠損は0件だった。

適用した修正は、車体中心offsetを含む開始状態校正、向き付き矩形衝突、Autoware制動profile、
AWSIM CVMと同じ5秒horizon・0.1秒刻みのOBB TTCである。

## 結果

| モデル | TP | TN | FP | FN | Accuracy | Sensitivity | Specificity |
|---|---:|---:|---:|---:|---:|---:|---:|
| Autoware校正 | 50 | 22 | 28 | 0 | 72.0% | 100.0% | 44.0% |
| JAMA baseline | 28 | 49 | 1 | 22 | 77.0% | 56.0% | 98.0% |

校正モデルはAWSIM衝突50件をすべて候補として抽出した。非衝突28件も候補に含めるため、最終判定
には使えないが、AWSIM再検証へ渡すscreeningモデルとしては「見逃さない」結果になった。

連続TTCを両側で有限値として得られた81件では次の結果だった。

| モデル | MAE [s] | Median absolute error [s] | Bias [s] |
|---|---:|---:|---:|
| Autoware校正 | 0.141 | 0.000 | +0.012 |
| JAMA baseline | 0.538 | 0.400 | +0.538 |

校正モデルはTTC MAEを0.398秒削減した。Maude TTCラベルに対する感度は、0.3秒で96.4%、
0.5秒で88.6%、0.7秒で91.4%、0.9秒で94.2%、1.1秒で95.5%、1.2秒で97.8%、
1.3秒で98.9%、1.5秒で100%だった。

## 判定

- 危険候補抽出: 受入可能。衝突感度100%、TTC MAE 0.141秒。
- AWSIM代替の最終安全判定: 受入不可。specificity 44%で過検出が多い。
- 次段階: NPCの理想半円ではなく実際の追従軌跡・操舵遅れをモデル化し、false positiveを減らす。
- SDE確率: 未校正。ODE/AWSIM軌跡残差から拡散係数を推定するまで現実確率とは解釈しない。

全100件の保存配置は `ego lane=514, offset=38` であり、現在の速度帯別AWSIM profileへ一般化
できるかは別の実験が必要である。

## 成果物

- `artifacts/dynamics_awsim_final_validation_20261006/per_case.csv`
- `artifacts/dynamics_awsim_final_validation_20261006/summary.json`
- `artifacts/autoware171_uturn_start_geometry_calibration.json`
- `tools/analysis/calibrate_uturn_start_geometry.py`
- `tools/analysis/validate_dynamics_against_awsim.py`
