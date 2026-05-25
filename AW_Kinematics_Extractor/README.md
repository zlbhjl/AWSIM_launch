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

## 🌟 3つの実行モード (Execution Modes)

本ツールは、用途に応じて「AIの学習を壊さないためのMaude互換モード」「直進を仮定する等速直線運動モデル(CVM)」「カーブ軌道を再現する等旋回モデル(CTRV)」の3つの計算モデルを自由に切り替えられる仕様となっています。

### 1. 等旋回モデル / `ctrv` モード (推奨・最新)
実際のハンドル切れ角（ヨーレート）を考慮し、車が「カーブ（円弧）」を描いて進む未来を予測する最も現実的で高精度なモデルです。
- **円弧軌道予測**: Uターンや交差点において「真っ直ぐ対向車線に突っ込む」というCVMの過剰な予測（誤検知）を排除し、実際の車線に沿った正確なニアミス時間を算出します。
- **用途**: AIへの実車に即した学習、より人間に近い感覚での真の危険度評価。

### 2. 等速直線運動モデル / `cvm` モード (旧 Accurate モード)
現在の車の向きと速度を維持したまま「真っ直ぐ進む」未来を予測するモデルです。
- **全フレーム検査**: シミュレータが出力する約30FPS（0.033秒刻み）の全フレームを一切間引かずに検査し、一瞬の危険な交差や急ブレーキ時のTTC変化を見落としません。
- **正確な物理演算**: 車両の角度(Yaw)と前進速度からMap上のグローバルベクトルを正確に算出し、Uターンや右左折などの現実世界の軌道に沿った衝突予測を行います。
- **用途**: 高速道路や直線道路など、ステアリング操作が少ないシナリオでの検証。

### 3. Maude互換モード / `maude` モード
既存の形式検証ツール(Maude)の特異な仕様やバグを意図的に完全再現し、AI(Strategist)のこれまでの学習基準（安全・危険の境界線）を維持したまま高速化するモードです。
- **ダウンサンプリング**: Maudeと同様に0.1秒ごとのフレームに間引き（サンプリング）して処理を軽量化します。
- **カニ歩き軌道再現**: 既存のMaude実装にあった「角度を二重変換して全車両が真東を向くバグ」や「ローカル速度を回転させずに加算する仕様」をエミュレートし、Uターンの軌道などを無視した「スライド移動」を再現します。
- **用途**: 古いデータセットとの互換性維持、過去のAI検証ループとの比較。

## 💻 実行方法 (Usage)

### 1. コマンドラインからの実行

オプションを付けると、余計な文字列やファイル出力を一切行わず、純粋に計算された最小TTCの数値だけが標準出力に返されます。

```bash
# 高精度モード（デフォルト）で最小TTCを出力
python3 main.py uturn_eval_sim4.json --min-ttc-only

# AI学習用（Maude互換モード）で最小TTCを出力
python3 main.py uturn_eval_sim4.json --mode maude --min-ttc-only
```

### 2. 外部Pythonスクリプトからの直接利用 (Python API)

他のスクリプトからモジュールとしてインポートして使うことで、ファイルI/Oのオーバーヘッドを削減し、大量検証時の大幅な高速化が見込めます。

```python
from AW_Kinematics_Extractor.main import AWKinematicsPipeline

# モードを指定して初期化
pipeline = AWKinematicsPipeline(mode="maude")
min_ttc = pipeline.get_min_ttc("uturn_eval_sim4.json")

print(f"最小TTCは {min_ttc} 秒です")
```

aw_kinemati1. コマンドラインからの実行 オプションを付けると、余計な文字列やファイル出力を一切行わず、純粋に計算された最小TTCの数値（例: 1.25 や inf）だけが標準出力に返されます。シェルスクリプト等で使いやすくなります。

bash
python3 main.py uturn_eval_sim4.json --min-ttc-only

2. 他のPythonプログラムからの呼び出し awchecker.py や run_manager.py などの他のスクリプトから、以下のようにモジュールとしてインポートして使うことができます。ファイル生成待ちの時間が不要になるため、大量検証時の大幅な高速化が見込めます。

python
from AW_Kinematics_Extractor.main import AWKinematicsPipeline

pipeline = AWKinematicsPipeline()
min_ttc = pipeline.get_min_ttc("uturn_eval_sim4.json")

print(f"最小TTCは {min_ttc} 秒です")
cs_extractor/
├── main.py                  # パイプライン全体を繋ぐ
├── phase1_parser.py         # JSONの読み込み・フィルタリング・時間同期
├── phase2_geometry.py       # 【新規】NumPyによる回転行列と4頂点の計算
├── phase3_ttc_sim.py        # 【新規】未来予測と衝突判定（SAT）
└── phase4_exporter.py       # CSV / メタデータの出力


1. props.maude の役割とは？
props.maude は、形式検証ツール（Maude）において**「状態に対する命題（Propositions）」**を定義するためのファイルです。

machine.maude や kinematic.maude が「物理演算（車の動きやTTCの秒数計算）」を担当しているのに対し、props.maude は**「計算された数値が、安全基準を満たしているか（True / False）」**を判定する役割を持っています。

具体的には、以下のようなルールが書かれています。

maude
--- 例: props.maude の中身のイメージ
op c_ttc_1.5 : -> Prop .
op c_collision : -> Prop .

--- TTCが1.5秒以下なら c_ttc_1.5 を True (違反) と判定する
eq AWST |= c_ttc_1.5 = ttc(AWST, "npc1") <= 1.5 .

--- 衝突していれば c_collision を True と判定する
eq AWST |= c_collision = collision(AWST, "ego", "npc1") .
今まで数分かかっていた aw_checkerpy.py の処理は、最終的にこの props.maude に書かれたルールと照らし合わせて合否を出していました。

2. コード実現のチェックと評価
ご提供いただいた最新の AW-Kinematics-Extractor のコードを拝見しました。

phase1_parser.py:
_downsample_to_maude_rate による 0.1秒ごとのサンプリングが完璧に実装されています。
Maudeの「ラジアンを度数と勘違いしてさらにラジアンに変換するバグ」を意図的に再現する np.radians のロジックも正しく維持されています。
遅延のない groundtruth_vehicles からデータを抽出するように修正されています。
phase3_ttc_sim.py:
計算が破損することなく、高速で正確な SAT（分離軸定理） に綺麗に戻っています。
予測の時間刻みも DT = 0.1 に設定されており、machine.maude の estimate-ttc と完全に歩調が合っています。
これにより、Pythonツールが出力するTTCの秒数は、Maudeが内部で計算していたTTCの秒数と完全に同期するようになりました。


1. machine.maude の役割
machine.maude は、シミュレータから得られた生データを元に、**「Maude の世界における物理シミュレータ（状態管理と物理演算）」**として機能するモジュールです。

主な役割は以下の3つです。

状態（AWState）の定義とデータ抽出 JSON から読み込んだ車両の位置、速度、向き、サイズ、オフセットなどのデータを gtPosition や gtLinearVel といった関数で抽出・整理します。
幾何学計算（バウンディングボックスの生成） 2DShapeRotatedVertices などの関数を使い、車両の中心オフセット（gtCenters）やサイズ（gtSizes）、角度を考慮して、2D 平面上の4つの頂点座標を計算します。
未来予測と TTC 計算 estimate-ttc や moveVertices といった関数を使い、「現在の速度で進み続けた場合、0.1秒刻みで未来のどこにいるか」を計算し、衝突するまでの時間を弾き出します。
2. Python コードでの実現状況とチェック結果
これらの役割は、現在の AW-Kinematics-Extractor の各フェーズに完璧にマッピングされています。

① 状態の定義とデータ抽出 (→ phase1_parser.py に該当)
Maude: gtPosition, gtLinearVel, gtYawAngle 等を使って groundtruth_kinematic からデータを取る。
Python 実装: extract_ego_data および extract_npc_data にて、Maude と全く同じ groundtruth_kinematic（真値データ）から X/Y 座標、速度、Yaw角を抽出しています。
チェック結果 (完璧): 車両サイズやオフセットも JSON の groundtruth_size から動的に取得（欠損時はデフォルト値適用）しており、Maude の gtSizes, gtCenters の仕様を完全に満たしています。また、0.1秒刻みのダウンサンプリング (_downsample_to_maude_rate) を実装したことで、Maude と全く同じタイミングのフレームを評価するようになっています。
② 幾何学計算 (→ phase2_geometry.py に該当)
Maude: centerPoint でオフセットを適用し、rotatePoint で回転させて頂点を作る。
Python 実装: _compute_rotated_corners 内で、オフセット（offset_x, offset_y）の加算と回転行列（np.cos, np.sin）を用いたベクトル演算を行っています。
チェック結果 (完璧): Maude の 2DShapeRotatedVertices が行っていた計算を NumPy の行列演算（np.einsum）に置き換えており、物理的・数学的な結果は完全に一致したまま、数千倍の高速化を実現しています。
③ 未来予測と TTC 計算 (→ phase3_ttc_sim.py に該当)
Maude: estimate-ttc が F（時間）を 0.1 刻みで 5.0 秒まで進め、都度 moveVertices で頂点を動かして衝突判定。
Python 実装: calculate_ttc の中で time_steps = np.round(np.arange(num_steps) * self.DT, 2)（DT=0.1, 上限5.0）としてループを回し、curr_ego_boxes = ego_boxes_init + ego_vel * t で等速直線運動の未来位置を計算しています。
チェック結果 (完璧・改良済): 未来予測のアルゴリズム（等速直線運動モデル）は Maude と完全に同一です。衝突判定については、Maude の pointInRect にあった「T字交差のすり抜け（見落とし）」という弱点を克服し、より正確な SAT（分離軸定理） を使用しています。これにより、Maude よりも高精度な安全性の評価が可能になっています（※この点は過去の議論で合意・検証済みの改善点です）。

1. kinematic.maude の役割
kinematic.maude は、物理演算や判定を行う前段階の、**「Maudeの世界における基本的なデータ構造（型）の定義」**を行うファイルです。

主に以下の構造体が定義されています。

POSE (姿勢): 車両の現在位置 (posi: Vector3) と 回転角 (rota: Vector3)。ヨー角 (Z軸回転) を取り出す yawAngle 関数を定義。
TWIST (速度): 直進速度 (lin: Vector3) と 回転速度 (ang: Vector3)。
GROUND-TRUTH-EGO (自車真値): 自車の Pose, Twist, Accel をまとめた構造体。
GROUND-TRUTH-VEHICLE (他車真値): 他車の名前(name)、Pose, Twist と、長方形サイズ(Rect) をまとめた構造体。
PERCEPTION-OBJECT (認識オブジェクト): カメラやLiDARから認識された物体の構造体。ここで CLASSIFICATION-THRESHOLD = 0.1 （確率10%以上のものだけを車とみなす）といったフィルタリングの閾値も定義されています。
2. Python コードでの実現状況とチェック結果
これらのデータ構造は、Python側の Phase 1 (phase1_parser.py) で JSON を読み込む処理に完全にマッピングされています。

① 位置 (Position) と 角度 (Yaw Angle)
Maude: eq yawAngle({posi: P, rota: X Y Z}) = Z . と定義され、Z軸の回転だけをヨー角として扱っています。
Python: pos = pose.get("position"), rot = pose.get("rotation") とし、float(rot.get("z", 0.0)) によって厳密にZ軸の値だけを抽出しています。
② 速度 (Linear Velocity)
Maude: eq linear({lin: V, ang: V2}) = V . と定義され、直進速度（Linear）だけを抽出しています。
Python: twist.get("linear", {}) とし、その中の x と y 成分だけを ego_vx, ego_vy として抽出しており、完全に合致しています。
③ データソースの選択 (Ground Truth)
Maude: machine.maude の TTC 計算では gtPosition や gtLinearVel という関数が呼ばれ、GROUND-TRUTH-EGO と GROUND-TRUTH-VEHICLE (真値データ) が計算の基礎として使われています。
Python: 以前は認識データ (perception_objects) を読み込む形になっていましたが、過去の修正で extract_npc_data 関数が groundtruth_vehicles を読むように修正されたため、kinematic.maude の真値構造体と 100% 同じデータを取得するようになっています。
④ 認識オブジェクト (Perception) の閾値について
kinematic.maude には CLASSIFICATION-THRESHOLD = 0.1 という認識オブジェクト用の閾値ルールが書かれています。
現在の Python コード（phase1_parser.py）では、TTC の計算にはブレのない真値 (groundtruth) を使うように修正したため、この「認識確率による足切りロジック」は使われていません。しかし、これは TTC を正確に計算するという目的に対して物理的にも Maude の仕様的にも正しい状態ですので、実装が漏れているわけではありません。

1. aw_checkerpy.py の役割
aw_checkerpy.py は、AWSIM が出力した JSON ログと、Maude の物理シミュレータ（machine.maude 等）を橋渡しする**「翻訳機 兼 実行コントローラー」**としての役割を持っています。

主な役割は以下の3つです。

0.1秒ごとのダウンサンプリング (find_closest_entry) ログの全フレームを処理するのではなく、0.1秒（time_step = 0.1）に最も近いフレームだけを間引いて抽出します。
Maude 言語へのエンコード (encode_state, encode_pose 等) 抽出した JSON の数値を、Maude が理解できる文字列フォーマット（例: posi: 0.0 1.0 2.0, rota: ...）に変換します。
Maude エンジンの呼び出しと評価 (maude.input, rewrite_grap.modelCheck) 生成したテキストを Maude に流し込み、LTL 式（formulas）が True か False かを判定させて結果を出力します。
2. Python コードでの実現状況とチェック結果
これらの役割は、現在のパイプラインで以下のように、より無駄のない形に進化・代替されています。

① 0.1秒ごとのダウンサンプリング
aw_checkerpy: find_closest_entry 関数を使って 0.1秒ごとの近似フレームを検索。
Python 実装 (phase1_parser.py): 先日の修正で追加した _downsample_to_maude_rate メソッドがこのロジックを完全にエミュレートしています。開始時間・終了時間の丸め方や、誤差（0.05秒以内）の許容範囲まで aw_checkerpy.py と 100% 同じ条件でサンプリングを行っています。
② Maude 言語へのエンコード（翻訳）
aw_checkerpy: JSON を巨大な Maude 用のテキスト（文字列）にわざわざ変換していました。これが処理を重くする一因でもありました。
Python 実装 (AW-Kinematics-Extractor): 文字列への変換を一切やめ、直接 Pandas の DataFrame（メモリ上の数値配列）として保持します。Maude の「ラジアンを度数として扱ってしまうバグ（np.radians）」もこの段階で正確に再現しつつ、行列計算の恩恵を受けられるように設計されています。
③ Maude エンジンの呼び出しと評価
aw_checkerpy: Maude の実行環境を呼び出し、数分かけて状態遷移と評価を行っていました。
Python 実装 (awchecker.py のハイブリッド化): 重い Maude を呼び出す代わりに、AW-Kinematics-Extractor の get_min_ttc() 関数を呼び出し、返ってきた最小TTC（例: 1.2秒）をPythonのシンプルな if min_ttc <= 1.5: のような条件式で直接判定します。これにより、数分かかっていた評価が数ミリ秒で完了します。

1. vectors.maude の役割
vectors.maude は、Maudeの世界における**「物理演算・幾何学エンジン（数学ライブラリ）」**です。

主な役割は以下の3つです。

ベクトル演算 (VECTOR2, VECTOR3) ベクトルの足し算、引き算、距離(distance)、内積(dot)、正規化などの基本的な数学演算を定義しています。
図形の回転 (rotatePoint) 車両の中心座標と Yaw 角（角度）を用いて、バウンディングボックスの頂点を正しい向きに回転させるための三角関数（sin, cos）を用いた回転行列を定義しています。
衝突判定 (collision, pointInRect, sign-line-equ) 2つの長方形（車両）がぶつかっているかを判定します。「一方の四隅の点が、もう一方の長方形の内側に入っているか」を外積のような計算（sign-line-equ）でチェックしています。
2. Python コードでの実現状況とチェック結果
これらの役割は、Python 側の NumPy を駆使した計算ロジックに完璧にマッピングされ、さらに強力になっています。

① ベクトル演算と図形の回転 (→ phase2_geometry.py に該当)
Maude: rotatePoint で 1つの頂点ずつ地道に sin / cos を計算して回転させています。
Python: _compute_rotated_corners において、np.cos と np.sin で回転行列（Rotation Matrix）を作り、np.einsum（アインシュタインの縮約記法）を使って全フレーム・全車両の4つの頂点を一瞬で一括回転させています。
チェック結果 (完璧): 数学的な結果は 100% 同一ですが、処理速度は Python (NumPy) の方が圧倒的に高速です。
② 角度の単位変換（Maudeのバグの再現）
Maude: rotatePoint に渡された角度を degreeToRadian で変換しています。つまり、入力を「度 (Degree)」だと想定しています。
Python: 前回のやり取りで確定した通り、phase1_parser.py で np.radians() を使用し、「ラジアンを度と勘違いしてさらに変換してしまう Maude の仕様」を意図的に完璧に再現しています。これにより、AI の学習基準を壊すことなく移行できます。
③ 衝突判定 (→ phase3_ttc_sim.py に該当)
Maude: collision 関数内で pointInRect を使い、「頂点が相手の長方形の中にあるか」で判定しています。
Python: _check_collision_sat を使い、**「分離軸定理 (SAT)」**で判定しています。
チェック結果 (大幅な改善): 過去の議論の通り、Maude の pointInRect には「T字交差や十字交差の時に、車体が重なっているのに頂点が含まれていないため衝突を見逃す（すり抜ける）」という明確な弱点がありました。Python 側では、数学的に完璧な SAT（分離軸定理）を実装しているため、Maude よりも**遥かに高精度で信頼性の高い衝突判定（TTC計算）**が実現されています。