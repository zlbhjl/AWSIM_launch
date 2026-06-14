#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import pandas as pd
import numpy as np
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C
from sklearn.preprocessing import StandardScaler
from scipy.stats import gaussian_kde
import point_extractors

class SafetyEstimator:
    def __init__(self, scenario_name, config, traces_dir="~/simulation_traces"):
        """
        汎用安全性推定器 (Gaussian Process Regression)
        Config-Driven アーキテクチャに基づき、あらゆるシナリオに即座に適応します。
        """
        self.traces_dir = os.path.expanduser(traces_dir)
        self.dataset_file = os.path.join(self.traces_dir, f"{scenario_name}_dataset.csv")
        self.base_dataset_file = os.path.join(self.traces_dir, f"{scenario_name}_dataset_base.csv")
        
        self.config = config
        self.scaler = StandardScaler()
        
        # モデル定義: ガウス過程回帰
        # 予測値の平均(μ)だけでなく、不確実性(σ)を算出するために最適化
        kernel = C(1.0, (1e-3, 1e3)) * RBF(1.0, (1e-2, 1e2))
        self.model = GaussianProcessRegressor(
            kernel=kernel, 
            alpha=0.01, 
            n_restarts_optimizer=5, 
            random_state=42
        )
        
        self.is_trained = False
        # [汎用化] 入力パラメータ名を Config のキーから自動取得
        self.feature_names = list(self.config.PARAM_RANGES.keys())

    def load_dataset(self):
        """
        統合されたデータセットCSVを読み込む。
        過去のベースデータ(_base.csv)が存在する場合は結合して返す。
        """
        df_list = []
        if os.path.exists(self.base_dataset_file):
            try:
                df_list.append(pd.read_csv(self.base_dataset_file, engine='python', on_bad_lines='skip'))
            except Exception as e:
                print(f"[Estimator] ❌ ベースデータセットCSVの読み込み失敗: {e}")
                
        if os.path.exists(self.dataset_file):
            try:
                df_list.append(pd.read_csv(self.dataset_file, engine='python', on_bad_lines='skip'))
            except Exception as e:
                print(f"[Estimator] ❌ データセットCSVの読み込み失敗: {e}")

        if not df_list:
            return None
            
        if len(df_list) == 1:
            return df_list[0]
            
        return pd.concat(df_list, ignore_index=True)

    def load_training_data(self, target_column):
        """
        データセットを読み込み、学習用の (X, y) を作成。
        """
        df_dataset = self.load_dataset()
        if df_dataset is None:
            return None

        # ターゲット指標の存在確認
        if target_column not in df_dataset.columns:
            print(f"[Estimator] ⚠️ 指標 '{target_column}' が見つかりません。")
            return None

        # --- 新機能: 異常データのフィルタリング ---
        # 1. 各種評価指標で解析エラー (-1) が発生したデータを除外
        for col in df_dataset.columns:
            if col.startswith("c_") or col.startswith("formula_"):
                df_dataset = df_dataset[~df_dataset[col].isin([-1, "-1", -1.0])]
                
        
        # --- [追加] 既存データに含まれる TTC の論理矛盾（すり抜け）を学習前に補正 ---
        if "c_collision" in df_dataset.columns:
            collision_mask = df_dataset["c_collision"].isin([1, "1", 1.0])
            for col in df_dataset.columns:
                if col.startswith("c_ttc_"):
                    df_dataset.loc[collision_mask, col] = 1
                    
        ttc_cols = sorted([c for c in df_dataset.columns if c.startswith('c_ttc_')], key=lambda x: float(x.split('_')[-1]))
        for i in range(len(ttc_cols) - 1):
            df_dataset.loc[df_dataset[ttc_cols[i]].isin([1, "1", 1.0]), ttc_cols[i+1]] = 1

        # --- [追加] 特徴量の列に文字列（ズレ等）が混入しているとScikit-Learnがクラッシュするため、強制的に数値に変換 ---
        for col in self.feature_names:
            df_dataset[col] = pd.to_numeric(df_dataset[col], errors='coerce')

        # 欠損値の除去と、0/1 (Boolean) データへの絞り込み
        essential_cols = ["loop_num", target_column] + self.feature_names
        df_dataset = df_dataset.dropna(subset=essential_cols)
        df_valid = df_dataset[df_dataset[target_column].isin([0, 1])]
        
        # 学習には「安全(0)」と「危険(1)」の両方のサンプルが必要
        if len(df_valid) < 2 or df_valid[target_column].nunique() < 2:
            return None

        # --- [追加] GPの計算爆発(O(N^3))を防ぐためのスマートなデータ間引き (Active Data Pruning) ---
        MAX_TRAIN_SAMPLES = 1500
        if len(df_valid) > MAX_TRAIN_SAMPLES:
            # 1. 絶対に残す「重要なエッジケース」の条件
            # 衝突した、またはニアミス(TTCが1.5s未満、または接近距離が2.0m未満)だったデータ
            is_critical = (df_valid.get('c_collision', 0) == 1) | \
                          (df_valid.get('min_ttc', 999.0) < 1.5) | \
                          (df_valid.get('min_distance', 999.0) < 2.0)
            
            # 2. 直近のデータ(最新の境界探索トレンド)も一定数残す
            recent_threshold = df_valid['loop_num'].max() - 500
            is_recent = df_valid['loop_num'] > recent_threshold
            
            must_keep_mask = is_critical | is_recent
            df_must_keep = df_valid[must_keep_mask]
            df_others = df_valid[~must_keep_mask]
            
            # 3. 安全で古いデータ(others)から、上限に収まるようにランダムサンプリング
            remain_count = MAX_TRAIN_SAMPLES - len(df_must_keep)
            if remain_count > 0 and len(df_others) > remain_count:
                df_others_sampled = df_others.sample(n=remain_count, random_state=42)
                df_valid = pd.concat([df_must_keep, df_others_sampled])
            else:
                df_valid = df_must_keep
            
        return df_valid

    def train(self, target_column):
        """
        指定されたターゲット指標の境界線を学習。
        """
        df = self.load_training_data(target_column)
        if df is None:
            return False
        
        X = df[self.feature_names].values
        y = df[target_column].values

        # 特徴量を標準化（スケーリング）して学習効率を向上
        X_scaled = self.scaler.fit_transform(X)

        try:
            self.model.fit(X_scaled, y)
            self.is_trained = True
            return True
        except Exception as e:
            print(f"[Estimator] ❌ 学習失敗: {e}")
            return False

    def predict_uncertainty(self, X_new):
        """
        未実行地点の平均予測値と、モデルの「自信のなさ（不確実性）」を算出。
        """
        if not self.is_trained:
            return None, None
            
        X_new_scaled = self.scaler.transform(X_new)
        # ガウス過程回帰の核心: return_std=True で標準偏差(σ)を取得
        mean, std = self.model.predict(X_new_scaled, return_std=True)
        return mean, std

    def calculate_dkw_bounds(self, target_column, delta=0.05, bounds=None, region="custom", df=None, use_kde_weighting=False):
        """
        【ステップ1〜4】DKW (Dvoretzky-Kiefer-Wolfowitz-Massart) 不等式による信頼帯の構築
        経験的累積分布関数 (ECDF) と、信頼水準 (1-delta) に基づく絶対的な信頼帯を計算します。
        """
        if df is None:
            df = self.load_dataset()
            
        if df is None or df.empty or target_column not in df.columns:
            print(f"[Estimator] ⚠️ データセットがない、または指標 '{target_column}' が見つかりません。")
            return None

        try:
            df = point_extractors.filter_by_region_and_bounds(df, region=region, bounds=bounds)
        except Exception as e:
            print(f"[Estimator] ⚠️ {e}")
            return None

        # --- [追加] エラーデータの厳格な除外 ---
        # c_collision等に -1 が入っている完全なエラー行を事前に弾く
        if 'c_collision' in df.columns:
            df = df[~df['c_collision'].isin([-1, "-1", -1.0])]

        data = pd.to_numeric(df[target_column], errors='coerce').dropna()
        
        # --- [修正] 指標に応じたマイナス値の扱い ---
        # min_ttc, min_distance, z_margin は物理的に0未満にならないためマイナスをエラー(ゴミ)として弾くが、
        # min_ttb や theory_margin は「マイナス(手遅れ)」が正常な物理状態であるため評価に残す
        if target_column in ['min_ttc', 'min_distance', 'z_margin']:
            data = data[data >= 0]
            
        # --- [追加] 無限大(inf)の安全な処理 ---
        # TTCなどで衝突しなかった場合の np.inf を、シミュレーションの予測上限値(5.0)にクリップし、幅がinfに発散するのを防ぐ
        data.replace([np.inf, -np.inf], [5.0, -5.0], inplace=True)

        k_actual = len(data) # 実際のサンプル数
        if k_actual == 0:
            print(f"[Estimator] ⚠️ DKWバウンドを計算するための有効なサンプルがありません。")
            return None

        y_values = data.values
        valid_indices = data.index

        # --- [追加] KDEを用いた重要度サンプリング (Importance Sampling) による重み付け ---
        if use_kde_weighting and k_actual > 1:
            try:
                # パラメータ空間(X)の取得とスケーリング (scipy.stats.gaussian_kde は次元D x サンプル数N を要求)
                X_params = df.loc[valid_indices, self.feature_names].values
                X_scaled = self.scaler.fit_transform(X_params).T 
                
                # KDEでAIのサンプリング密度(偏り)を推定
                kde = gaussian_kde(X_scaled)
                dens = kde.evaluate(X_scaled)
                dens = np.clip(dens, 1e-10, None) # ゼロ除算防止
                
                # 一様分布を仮定した場合の重み (密度の逆数) を計算
                weights = 1.0 / dens
                
                # --- [追加] Weight Clipping (重み崩壊の防止) ---
                # 極端に密度の低い未知領域のサンプルが持つ異常な重みをカットし、ESSの崩壊を防ぐ
                clip_val = np.percentile(weights, 99)
                weights = np.clip(weights, 0.0, clip_val)
                
                # 正規化(合計を1)する
                weights /= np.sum(weights)
                
                # 加重ECDF (Weighted ECDF) の構築
                sort_idx = np.argsort(y_values)
                x_sorted = y_values[sort_idx]
                weights_sorted = weights[sort_idx]
                ecdf = np.cumsum(weights_sorted)
                
                # 有効サンプル数 (Effective Sample Size: ESS) の計算
                k_eff = 1.0 / np.sum(weights**2)
                print(f"  [Estimator] 💡 KDE重点サンプリング適用: 実サンプル数 {k_actual} -> 有効サンプル数(ESS) {k_eff:.1f}")
            except Exception as e:
                print(f"  [Estimator] ⚠️ KDEの計算に失敗しました (偏り補正が不可能なため、この状態でのDKW評価を中止します): {e}")
                return None
        else:
            x_sorted = np.sort(y_values)
            ecdf = np.arange(1, k_actual + 1) / k_actual
            k_eff = k_actual

        # DKW不等式による許容誤差 Δ (Delta) の計算
        delta_margin = np.sqrt(np.log(2.0 / delta) / (2 * k_eff))

        # 上限と下限の境界を算出 (0〜1の範囲にクリップ)
        lower_bound = np.maximum(ecdf - delta_margin, 0.0)
        upper_bound = np.minimum(ecdf + delta_margin, 1.0)

        return {
            "x": x_sorted, "ecdf": ecdf,
            "lower_bound": lower_bound, "upper_bound": upper_bound,
            "delta": delta, "delta_margin": delta_margin, "sample_size": k_eff,
            "filtered_df": df
        }
        
    def calculate_quantile_with_dkw(self, target_column, q=0.05, delta=0.05, bounds=None, region="custom", df=None, use_kde_weighting=False):
        """
        【ステップ5】対象指標（分位数）の導出
        例: 95%の信頼水準 (delta=0.05) で、下位5% (q=0.05) の最小TTCがどの範囲にあるかを数学的に保証する。
        """
        dkw_bounds = self.calculate_dkw_bounds(target_column, delta, bounds=bounds, region=region, df=df, use_kde_weighting=use_kde_weighting)
        if dkw_bounds is None: return None
            
        x, ecdf = dkw_bounds["x"], dkw_bounds["ecdf"]
        
        # 分位数の点推定と、DKWバウンドに基づく信頼区間(真の分位数の下限・上限)の計算
        return {
            "q": q,
            "confidence_level": 1 - delta,
            "estimate": x[np.searchsorted(ecdf, q)] if np.searchsorted(ecdf, q) < len(x) else x[-1],
            "lower_bound": x[np.searchsorted(dkw_bounds["upper_bound"], q)] if np.searchsorted(dkw_bounds["upper_bound"], q) < len(x) else x[-1],
            "upper_bound": x[np.searchsorted(dkw_bounds["lower_bound"], q)] if np.searchsorted(dkw_bounds["lower_bound"], q) < len(x) else x[-1],
            "sample_size": dkw_bounds["sample_size"],
            "filtered_df": dkw_bounds["filtered_df"]
        }

    def evaluate_and_summarize_dkw(self, target_column, df, q=0.05, delta=0.05, epsilon=0.15):
        """
        指定されたデータフレームに対してDKW評価を行い、
        評価状態や信頼区間などの結果サマリーを辞書で返す。
        外部モジュール（CLIツールやStrategist）から評価機能として直接利用するためのメソッド。
        """
        if df is None or df.empty or len(df) < 2:
            return {"status": "error", "message": "評価対象のデータが不足しています(2件以上必要)。"}

        bounds_result = self.calculate_quantile_with_dkw(
            target_column=target_column,
            q=q,
            delta=delta,
            df=df
        )

        if not bounds_result:
            return {"status": "error", "message": "DKW評価を実行できませんでした。"}

        return {
            "status": "success",
            "q": q,
            "delta": delta,
            "epsilon": epsilon,
            "estimate": bounds_result["estimate"],
            "lower_bound": bounds_result["lower_bound"],
            "upper_bound": bounds_result["upper_bound"],
            "interval_width": bounds_result["upper_bound"] - bounds_result["lower_bound"],
            "sample_size": bounds_result["sample_size"]
        }

    def evaluate_and_summarize_dkw_multiple(self, target_columns, df, q=0.05, delta_total=0.05, epsilon=0.15, use_kde_weighting=False, region="custom", bounds=None):
        """
        複数指標の同時保証を行う。
        ボンフェローニ補正を用いて、各指標のエラー予算(delta)を分割して評価する。
        """
        if df is None or df.empty or len(df) < 2:
            return {"status": "error", "message": "評価対象のデータが不足しています(2件以上必要)。"}

        M = len(target_columns)
        if M == 0:
            return {"status": "error", "message": "評価対象の指標が指定されていません。"}
            
        delta_i = delta_total / M  # ボンフェローニ補正によるエラー予算の分割

        results = {"status": "success", "metrics": {}}
        for target in target_columns:
            bounds_result = self.calculate_quantile_with_dkw(
                target_column=target, q=q, delta=delta_i, df=df, use_kde_weighting=use_kde_weighting,
                region=region, bounds=bounds
            )
            
            if not bounds_result:
                return {"status": "error", "message": f"指標 '{target}' のDKW評価に失敗しました。有効なサンプルが不足しています。"}
                
            results["metrics"][target] = {
                "estimate": bounds_result["estimate"], "lower_bound": bounds_result["lower_bound"],
                "upper_bound": bounds_result["upper_bound"], "interval_width": bounds_result["upper_bound"] - bounds_result["lower_bound"],
                "sample_size": bounds_result["sample_size"], "filtered_df": bounds_result["filtered_df"]
            }
        return results
