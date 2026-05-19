# AW-Kinematics-Extractor
Quantitative Feature Extractor for Autoware / AWSIM Logs

## 📌 プロジェクト概要 (Project Overview)
AW-Kinematics-Extractor は、自動運転シミュレータ（AWSIM / Autoware 等）が出力する時系列JSONログから、車両の運動学（Kinematics）に基づく具体的な物理数値（定量データ）を算出し、外部の検証・分析ツールへ提供するためのデータ精緻化パイプラインです。

既存の形式検証ツール（`aw_checkerpy.py` 等）が、ログをMaude状態へ変換して「LTL公式を満たすか（True/False）」という定性的な検証を行うのに対し、本ツールは重要度サンプリング（Importance Sampling）などの統計的モデルチェッキングツールが確率計算の重み付けに利用できる連続的な数値データ（例：最小TTCが具体的に何秒だったか）を高速に抽出・エクスポートすることに特化しています。

※ 現在のバージョン (v1.0) では「TTC（衝突余裕時間）」の算出に特化していますが、Maudeの PROPOSITIONS に定義された他指標の追加を前提とした設計になっています。

## 🎯 主な特徴と設計思想 (Core Features & Philosophy)
- **Maude運動学ロジックの完全再現**:
  形式仕様ファイル（`kinematic.maude`, `machine.maude`, `vectors.maude`）で定義されている「2D矩形の衝突判定」や「等速直線運動による未来予測」をPython上で厳密に再現します。
- **高速なベクトル化演算 (NumPy Vectorization)**:
  数千万回に及ぶTTCの未来予測ループを排除し、NumPyの行列演算（ブロードキャスト）を用いることで、1000本規模のシミュレーションログを現実的な時間で一括処理します。
- **フェールセーフとエラー耐性 (Defensive Extraction)**:
  センサーロストによるJSONキーの欠落や、異常な寸法のオブジェクトに対し、Maudeの `nullVector3` 思想に基づく安全なデフォルト値適用とサニティチェックを行い、バッチ処理全体のクラッシュを防ぎます。
- **他ツールへのフラットな出力 (Flat Data Export)**:
  複雑にネストされたJSONを「各タイムステップにおける自車とNPCのペア」のフラットな時系列テーブル（CSV/JSON）に整理し、外部ツールが即座に読み込める形式を提供します。

## 🏗️ 処理パイプライン (Pipeline Architecture)
本パイプラインは、以下の4つのフェーズで構成されています。

### Phase 1: データ抽出と時間同期 (`phase1_parser.py`)
- **データの抽出**: 巨大なJSONファイルから、自車と他車の位置（x, y）、速度、向き（yaw角）、車両サイズを抽出します。
- **Defensive Extraction**: `.get()` メソッドを用いて欠損データを安全に処理します。
- **フィルタリング**: Maudeの公式仕様に準拠し、`classification` 配列内の分類確率(`probability`)が `0.1` 以上の車両のみを対象とします。
- **時間同期 (Time Alignment)**: 自車のタイムスタンプを基準とし、±50ms以内のNPCデータを結合（`merge_asof`）。一定時間更新がないNPCは「ロスト」として除外します。

> **📝 補足: 認識オブジェクトのフィルタリング仕様とシミュレータの特性**
> なぜ存在確率(`existence_prob`)を使わず、分類確率(`classification.probability`)でフィルタリングするのかには重要な技術的背景があります。
> 
> 1. **実環境のAutoware**: センサーAIは「物体が実在する確率」と「それが車である確率」の2つを推測し出力します。
> 2. **シミュレータ(AWSIM)の特性**: 仮想世界では「実在すること」も「車であること」も自明です。そのためAWSIMの仕様上、存在確率の計算は省略されて初期値の `0.0` が出力され、分類確率の方に `1.0` (100%) が確定で記録されます。
> 3. **Maudeの設計基準**: Maude側の元の判定ロジックはこの偏りを把握しており、確実に `1.0` が入る分類確率のみを見て判定するように設計されていました。
> 
> 本ツールもこのMaudeの設計思想を厳密に踏襲し、存在確率の `0.0` で誤って足切りしない（欠損なく解析する）ように設計されています。

### Phase 2: 幾何学演算と状態の構造化 (`phase2_geometry.py`)
Maudeの `AWState` に相当するデータをPandas/NumPyの多次元配列として構築します。
車両の中心オフセットと寸法に回転行列を適用し、2D平面上のバウンディングボックス4頂点座標を算出します。
- *⚡ 高速化*: フレームごとにループで計算するのではなく、NumPyのテンソル積(`np.einsum`)を用いて全フレーム・全車両の4頂点を一括（ベクトル化）計算します。

### Phase 3: TTC算出ループ (`phase3_ttc_sim.py`)
`machine.maude` の `estimate-ttc` アルゴリズムをNumPyでベクトル化し、未来の衝突予測を行います。
- **未来予測**: 0.1秒刻みで最大5.0秒先（`TTC-BOUND = 5.0`）まで、現在の速度での等速直線運動を想定した位置を計算します。
- **衝突判定 (SAT)**: 移動後の矩形同士の衝突を「分離軸定理 (SAT)」を用いて一括判定します。
  - *💡 補足*: Maude側の判定（`pointInRect`）に対し、SATは「完璧な十字型に交差して頂点が一つも含まれない」エッジケースでも衝突を検知できるため、より高精度で堅牢です。
- **計算のマスク**: 一度衝突した車両ペアは、以降の未来ステップの計算対象から除外（`active_mask`）し、計算負荷を劇的に軽減します。

### Phase 4: データ集約とエクスポート (`phase4_exporter.py`)
フレームごとのTTC推移を記録し、シミュレーション全体における「最小TTC」を特定します。外部ツール（SMC等）で扱いやすい形式で出力します。
- **時系列詳細ログ**: 全フレームの推移がわかる CSV ファイル。
- **サマリー**: 「最小TTC」などの代表値が書かれた JSON ファイル。

## 📐 前提とするMaude仕様と座標系 (Maude Specifications & Coordinate System)

計算の正確性を担保するため、以下の物理・幾何定義は `kinematic.maude` および `machine.maude` などの形式仕様に従います。機能拡張の際などは必ず確認してください。

| 項目 (Parameter) | Maudeでの定義 / 扱い | Python実装時の注意・変換ルール |
| :--- | :--- | :--- |
| **平面空間 (2D Space)** | `magnitudeIgnoreZ` 等を利用し、Z軸（高さ）は無視 | 位置および速度ベクトルの計算は、$(x, y)$の2次元空間に射影して行う。 |
| **角度の単位 (Yaw Angle)** | `pose` の `rota` に格納されるヨー角 | Pythonの `math` や `numpy` の三角関数は**ラジアン (Radians)** を要求する。ログが度 (Degrees) の場合は `np.radians()` で変換必須。 |
| **回転の方向 (Rotation Direction)** | `rotatePoint` 関数の定義に依存 | 右手系（反時計回りが正）か、左手系（時計回りが正）かをシミュレータの出力と合わせる。 |
| **車両の基準点 (Origin Point)** | 後輪車軸中心などの特定のポイント（`gtCenters` でオフセットを定義） | ログの座標 $(x, y)$ は「車の中心」とは限らない。Maudeの `gtCenters["ego"]` 等のオフセット値を適用して幾何中心を求めること。 |
| **車両の寸法 (Dimensions)** | `gtSizes` (Length, Width, Height) | Length が $X$ 軸方向、Width が $Y$ 軸方向に対応していることを前提とする。欠損時はデフォルト値を使用。 |
| **予測ステップ (Time Step)** | `estimate-ttc` において `0.1` 秒刻み | 計算負荷を考慮しつつ、Python側でも `dt = 0.1` を基本ステップとして未来予測のループや配列生成を行う。 |
| **TTCの上限 (TTC-BOUND)** | `5.0` (秒) | 5.0秒以内で衝突しない場合は「無限大（Infinity / 危険なし、計算対象外）」として扱う。 |

## 🚀 将来の拡張性 (Future Extensibility)
本ツールはPhase 2（状態の構造化）が完了した時点で、すべての車両の「真の位置・速度・サイズ」と「認識された位置・速度・サイズ」がフラットな行列として揃う設計になっています。そのため、将来的には以下のような指標をループを回すことなく数行のコード（NumPyの配列演算）で簡単に追加算出することが可能です。

- **認識位置の誤差 (perp-pos-diff)**: 真値の中心座標と、認識された中心座標の距離（ユークリッド距離）。
- **バウンディングボックスのIoU (IOU-ratio)**: カメラ画像の認識領域と、真の領域の重なり比率。
- **車頭時間 (thw)**: 車間距離を自車の現在速度で割った値。

## 🔌 外部Pythonスクリプトからの直接利用 (Python API)

本ツールはコマンドラインからの実行（CSV/JSONの出力）だけでなく、他のPythonスクリプトから直接呼び出して、結果をメモリ上(`pandas.DataFrame`)で受け取ることも可能です。これによりファイルI/Oのオーバーヘッドを削減し、外部ツール（SMC等）や可視化スクリプトとよりスムーズかつ高速に連携できます。

```python
from main import AWKinematicsPipeline

# パイプラインの初期化
pipeline = AWKinematicsPipeline()

# ログファイルを処理し、結果をDataFrameとして直接取得（ファイル出力はスキップされます）
df_result = pipeline.run_extraction("path/to/log.json")

if not df_result.empty:
    print(f"シミュレーション全体の最小TTC: {df_result['min_ttc'].min()} 秒")
```

aw_kinematics_extractor/
├── main.py                  # パイプライン全体を繋ぐ
├── phase1_parser.py         # JSONの読み込み・フィルタリング・時間同期
├── phase2_geometry.py       # 【新規】NumPyによる回転行列と4頂点の計算
├── phase3_ttc_sim.py        # 【新規】未来予測と衝突判定（SAT）
└── phase4_exporter.py       # CSV / メタデータの出力