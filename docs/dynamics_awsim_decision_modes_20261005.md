# Dynamics U-turnの2つの判定モード（2026-10-05）

## モード

同じODE軌跡から、2つの判定を常に出力する。

| モード | 出力 | 判定 | 用途 |
|---|---|---|---|
| 判定モード（judgment） | `c_collision` | 車体（OBB）が重なったら衝突 | AWSIMの代わりの判定 |
| スクリーニングモード（screening） | `c_screening_candidate` | 車体間の隙間 `min_clearance` が余裕幅未満なら危険候補 | AWSIMで再検証する候補の抽出 |

重なりは隙間0なので、判定モードで衝突のケースは必ずスクリーニングモードでも候補になる。

切り替え方:

- 統計モード（`binomial_ci` / `sprt`）: `--dynamics-decision-mode judgment|screening`。
  使った指標と `dynamics_decision_mode` は `<output>.summary.json` に保存される。
- AWSIM比較: `run_dynamics_awsim_compare_v2.py --decision-mode judgment|screening`。
  比較レコードには `dynamics_decision`、`decision_agree`、`decision_missed_awsim_collision` が入る。
- 余裕幅の上書き: 入力 `screening_clearance_margin_m`。上書き時は provenance が `input_override` になる。

## 余裕幅の決め方

余裕幅 **1.1 m** は、結果を確認済みの開発データ280件だけで決めた
（`targets/dynamics/calibrations/autoware171_uturn_screening.json`）。

- 開発データ: 校正56件、開発確認24件、v1最終評価100件、v2最終評価100件（AWSIM衝突123件）
- 規則: AWSIM衝突なのにODEの隙間が最大だったケース（0.996 m）＋安全分0.1 m、0.1 m単位で切り上げ
- 開発データでの結果: 見逃し0件、誤検出は非衝突157件中34件

| 余裕幅 [m] | 0 | 0.3 | 0.5 | 0.7 | 1.0 |
|---|---:|---:|---:|---:|---:|
| 見逃し | 6 | 3 | 2 | 1 | 0 |
| 誤検出（/157） | 6 | 15 | 21 | 23 | 31 |

余裕幅はAutoware校正済みの制動（`autoware171_uturn_calibrated`）に対して決めたもの。
他の制動モデル（単独モードの既定のJAMAなど）では、`screening_provenance.calibrated_for_current_controller`
が `false` になり、単なる幾何的なしきい値として扱う。

## 検証（未使用の100件、1回だけ評価）

余裕幅を確定してから、開発データ280件と重ならない層化100件（衝突50・非衝突50、seed `20261008`、
`artifacts/autoware171_uturn_screening_holdout_split_20261005.json`）を選び、1回だけ評価した。
配置は全件 `ego lane=514, offset=38`。

| モード | TP | TN | FP | FN | 正解率 | 検出率 | 非衝突の正判定率 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 判定 | 49 | 46 | 4 | 1 | 95% | 98% | 92% |
| スクリーニング | 50 | 36 | 14 | 0 | 86% | 100% | 72% |
| JAMA baseline | 1 | 50 | 0 | 49 | 51% | 2% | 100% |

連続TTCの平均誤差は0.039秒、偏りは-0.017秒。

スクリーニングモードは見逃し0/50だった。これは見逃し率が0という意味ではない。
衝突50件で見逃し0件のとき、見逃し率の95%上側信頼限界は約6%（片側Clopper-Pearson）である。
この限界を下げるには、未使用の衝突ケースをさらに評価する必要がある。

AWSIMの実行を省ける割合は、AWSIMで非衝突となるケースの約72%（この検証では50件中36件）。
実際にどれだけ省けるかは、探索する入力分布での衝突率による。

## 成果物

- `artifacts/dynamics_awsim_screening_validation_20261005/per_case.csv`、`summary.json`（`collision` = 判定、`screening` = スクリーニング）
- `targets/dynamics/calibrations/autoware171_uturn_screening.json`
