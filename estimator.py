#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import pandas as pd
import numpy as np
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C
from sklearn.preprocessing import StandardScaler
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
                
        # 2. NPCのスタック (c_npc_stuck 等) が発生したデータを除外 (値が 1 の場合)
        stuck_cols = [col for col in df_dataset.columns if "stuck" in col]
        for col in stuck_cols:
            df_dataset = df_dataset[~df_dataset[col].isin([1, "1", 1.0])]
        # ----------------------------------------
        
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

    def calculate_dkw_bounds(self, target_column, delta=0.05, bounds=None, region="custom", df=None):
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

        # 異常値（-1やタイムアウト等の文字列）を除外し、有効な連続値データのみを抽出
        data = pd.to_numeric(df[target_column], errors='coerce').dropna()
        data = data[data >= 0]
        
        k = len(data) # サンプル数 (k)
        if k == 0:
            print(f"[Estimator] ⚠️ DKWバウンドを計算するための有効なサンプルがありません。")
            return None

        # 経験的累積分布関数 (ECDF) の構築
        x_sorted = np.sort(data.values)
        ecdf = np.arange(1, k + 1) / k

        # DKW不等式による許容誤差 Δ (Delta) の計算
        # Δ = sqrt( ln(2/δ) / 2k )
        delta_margin = np.sqrt(np.log(2.0 / delta) / (2 * k))

        # 上限と下限の境界を算出 (0〜1の範囲にクリップ)
        lower_bound = np.maximum(ecdf - delta_margin, 0.0)
        upper_bound = np.minimum(ecdf + delta_margin, 1.0)

        return {
            "x": x_sorted, "ecdf": ecdf,
            "lower_bound": lower_bound, "upper_bound": upper_bound,
            "delta": delta, "delta_margin": delta_margin, "sample_size": k,
            "filtered_df": df
        }
        
    def calculate_quantile_with_dkw(self, target_column, q=0.05, delta=0.05, bounds=None, region="custom", df=None):
        """
        【ステップ5】対象指標（分位数）の導出
        例: 95%の信頼水準 (delta=0.05) で、下位5% (q=0.05) の最小TTCがどの範囲にあるかを数学的に保証する。
        """
        dkw_bounds = self.calculate_dkw_bounds(target_column, delta, bounds=bounds, region=region, df=df)
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
