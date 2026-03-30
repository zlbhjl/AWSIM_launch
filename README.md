estimator.py の改修は、AIの予測モデルを「分類」から「回帰」へ根本的に切り替えるための変更です。具体的に以下の3点を変更しています。estimator.py の改修ポイントまとめインポートするクラスの変更変更前: GaussianProcessClassifier（分類器）をインポート。変更後: GaussianProcessRegressor（回帰モデル）をインポートするように変更しました。これにより、0か1かの確率ではなく、連続値としての予測とそのバラつきを計算できるようになります。モデルの初期化設定の変更変更前: self.model = GaussianProcessClassifier(...) として初期化。変更後: self.model = GaussianProcessRegressor(..., alpha=0.01) として初期化しました。alpha=0.01 を追加したのは、0と1だけの極端なデータを回帰で学習する際に発生しやすい計算エラー（特異行列エラー）を防ぐためのノイズ許容設定です。predict_uncertainty メソッドの計算ロジック変更変更前: predict_proba で確率（$P$）を出し、1.0 - 2.0 * np.abs(P - 0.5) という計算式で擬似的な不確実性を作っていました。変更後: predict(X, return_std=True) という回帰モデル専用のメソッドを呼び出すように変更しました。これにより、計算式を使わずに、AIから直接「予測値（$Mean$）」と「真の不確実性（$\sigma$）」を受け取ってそのまま返却する非常にシンプルな構造になりました。


strategist.py の改修は、先ほど変更した回帰モデル（Regressor）から得られる「予測値（Mean）」と「不確実性（Std）」を受け取り、「攻め（境界探索）」と「守り（安全確証）」のハイブリッド戦略を自動で切り替えるための頭脳のアップデートです。

strategist.py の改修ポイントまとめ
予測結果の受け取り変数を変更

変更前: probs, uncerts = self.estimator.predict_uncertainty(candidates) として、確率と擬似的な不確実性を受け取っていました。

変更後: mean, std = self.estimator.predict_uncertainty(candidates) に変更しました。これにより、AIの純粋な予測値（平均）と、データの少なさに基づく本当の不確実性（標準偏差）を直接扱えるようになります。

「20%の疑心暗鬼（確証フェーズ）」の追加

変更前: 常に np.argmax(uncerts) （0.5に一番近い場所）を次のターゲットに選んでいました。

変更後: 乱数を使って20%の確率で「安全確証フェーズ」に分岐するロジックを追加しました。このフェーズでは、mask = (mean < 0.5) & ((mean + 2 * std) > 0.5) という条件で「AIは安全と言っているが、最悪のケースでは事故になる不確実な点」を絞り込み、その中で一番 std（データの薄さ）が大きい場所を狙い撃ちします。

ログと理由（reason）の解像度アップ

変更前: Active Learning (c_collision, Uncert: 0.999) のような出力でした。

変更後: Safety Validation (c_collision, M:0.25, Std:0.150) や Boundary Search (c_collision, M:0.48, Std:0.020) のように、「今どの戦略で」「どんな予測値と不確実性の場所を」選んだのかがCSVやコンソールに詳細に残るように改修しました。





