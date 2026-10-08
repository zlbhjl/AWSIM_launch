# Dynamics / Autoware 1.7.1 最終比較 v2（2026-10-05）

## 要旨

v1の最終比較で残った過検出（非衝突50件中28件）の原因を開発データで特定し、3点を修正した。
その後、結果を一度も見ていない層化100件で1回だけ評価した。

| モデル | TP | TN | FP | FN | 正解率 | 検出率 | 非衝突の正判定率 | TTC MAE [s] | TTC bias [s] |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| v2 校正ODE | 48 | 47 | 3 | 2 | 95.0% | 96.0% | 94.0% | 0.035 | +0.007 |
| JAMA baseline | 2 | 50 | 0 | 48 | 52.0% | 4.0% | 100.0% | 0.459 | +0.459 |

（参考）v1の最終比較は別の100件で、正解率72%・検出率100%・非衝突の正判定率44%だった。

## データの分け方

| 集合 | 件数 | 用途 |
|---|---:|---|
| 制動・開始状態の校正 | 56 | 係数の推定 |
| 開発確認 | 24 | 推定誤差の確認 |
| v1最終評価 | 100 | 結果を確認済みのため、v2では開発評価に使用 |
| **v2最終評価** | 100 | 衝突50・非衝突50。seed `20261007`。上の180件と重複なし。モデル確定後に1回だけ評価 |

選定結果は `artifacts/autoware171_uturn_start_geometry_calibration_v2.json` の
`final_validation_sources` に保存している。全件の配置は `ego lane=514, offset=38`。

## 原因と修正

開発データ（旧最終評価100件）でAWSIMとODEの軌跡を時刻を揃えて比較した。

1. **NPCの旋回経路**。v1ではwaypointの両端から求めた半径4.86 m・180°の半円を、車体の
   幾何中心に適用していた。実際のAWSIMのNPCは、姿勢原点で半径約3.35 m・約176.6°の
   一定曲率の円弧をたどる（ばらつき±3°程度。NPC速度依存は小さい）。
   v2では曲率を姿勢原点に適用し、幾何中心をその1.12 m前方に置く。
   旋回半径の検証誤差は0.023 m、旋回角は0.33°。
2. **ego車の制動**。v1は平滑化した加速度の90パーセンタイル（3.91 m/s²）を使っており、
   持続的な減速度を過大評価していた。v2では速度系列に制動曲線を最小二乗で当てはめた。
   結果は遅れ0.616 s、立上り0.095 s、減速度3.013 m/s²（10--90%区間 2.98--3.03）。
3. **TTCの基準値**。AWSIMの `twist.linear` はワールド座標系である（Autoware 1.7.1 / 1.8.0 /
   1.9.0 の各トレースで、速度方向がyawと5°以内で一致）。一方、`AW_Kinematics_Extractor` の
   CVM/CTRVモードは、これをローカル座標とみなしてyawで再回転している。v2の比較では、
   AWSIM側にもODEと同じTTCコードをワールド座標の速度で適用した。extractorの値は
   `awsim_min_ttc_extractor` 列に参考値として残している。

開発データ上での推移（旧最終評価100件）:

| 段階 | FP | FN | TTC MAE [s] |
|---|---:|---:|---:|
| v1 | 28 | 0 | — |
| + 実測の旋回半径・旋回角 | 18 | 0 | — |
| + 姿勢原点での旋回 | 1 | 4 | — |
| + 制動曲線の当てはめ | 1 | 2 | 0.227（extractor基準） |
| + 同一TTCコードでの比較 | 1 | 2 | 0.041 |

## 残った不一致

v2最終評価の不一致5件は、いずれも衝突の境界近傍だった。

| sim | AWSIM | AWSIMクリアランス [m] | ODEクリアランス [m] |
|---:|---:|---:|---:|
| 8136 | 非衝突 | 0.26 | 0.00 |
| 4379 | 非衝突 | 0.26 | 0.00 |
| 4011 | 非衝突 | 0.09 | 0.00 |
| 6672 | 衝突 | 0.01 | 0.09 |
| 5876 | 衝突 | 0.00 | 0.69 |

AWSIMの非衝突例の多くは、クリアランスが約0.77 mに集まる。これは、Uターンを終えたNPCが
egoの隣の車線（横方向約2.9 m）を並走するときの横方向の隙間であり、ぎりぎりの回避ではない。

Maudeの離散TTCラベルとの一致率は、閾値0.3秒で92%、1.3秒で95%、1.5秒で99%だった。
0.9--1.1秒では非衝突側の正判定率が43--50%と低い。Maudeラベルは連続CVM TTCとは別の評価経路なので、
同じ測定値として扱わない。

## 判定

- 危険候補の抽出: 使用可能。検出率96%。見逃し2件は境界近傍（ODEクリアランス0.09 mと0.69 m）。
  見逃しを0にしたい用途では、開発データでクリアランスの余裕を決めてから適用する（未実施）。
- AWSIMの代替としての判定: この配置（lane 514 / offset 38）では正解率95%・非衝突の正判定率94%。
  他の配置やAutowareバージョンへの一般化は未検証。
- SDE: ODEとAWSIMの軌跡の差が小さくなったため、残差から拡散係数を推定できる段階になった（未実施）。

## 成果物

- `artifacts/dynamics_awsim_final_validation_v2_20261005/per_case.csv`、`summary.json`
- 校正: `artifacts/autoware171_uturn_start_geometry_calibration_v2.json`、
  `artifacts/autoware171_uturn_braking_calibration_v2.json`
- 同梱の校正値: `targets/dynamics/calibrations/autoware171_uturn_start_geometry.json`（`autoware171_uturn_start_linear_v2`）、
  `targets/dynamics/calibrations/autoware171_uturn_braking.json`（schema 2）
- ツール: `tools/analysis/calibrate_uturn_start_geometry.py`（`--exclude-report`）、
  `tools/analysis/calibrate_autoware_uturn_braking.py`、`tools/analysis/validate_dynamics_against_awsim.py`
