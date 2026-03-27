#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import pandas as pd
import numpy as np
from sklearn.gaussian_process import GaussianProcessClassifier
from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C
from sklearn.preprocessing import StandardScaler

class SafetyEstimator:
    # あなたの優れた初期化ロジックをそのまま採用
    def __init__(self, scenario_name, config, traces_dir="~/simulation_traces"):
        self.traces_dir = os.path.expanduser(traces_dir)
        self.param_file = os.path.join(self.traces_dir, f"{scenario_name}_parameters.csv")
        self.result_file = os.path.join(self.traces_dir, "checker_results.csv")
        
        self.config = config
        self.scaler = StandardScaler()
        kernel = C(1.0, (1e-3, 1e3)) * RBF(1.0, (1e-2, 1e2))
        self.model = GaussianProcessClassifier(kernel=kernel, n_restarts_optimizer=5, random_state=42)
        
        self.is_trained = False
        self.feature_names = list(self.config.PARAM_RANGES.keys())
        
        # [追加] 現在学習しているターゲットを記録
        self.current_target = None 

    # [修正] 引数に target_column を追加
    def load_and_merge_data(self, target_column):
        if not os.path.exists(self.param_file) or not os.path.exists(self.result_file):
            print(f"[Estimator] エラー: 必要なファイル ({self.param_file} または {self.result_file}) がありません。")
            return None

        df_params = pd.read_csv(self.param_file)
        df_results = pd.read_csv(self.result_file)

        df_merged = pd.merge(df_params, df_results, on="loop_num", how="inner")
        
        # [追加] 指定されたターゲット列がCSVに存在するか確認
        if target_column not in df_merged.columns:
            print(f"[Estimator] エラー: ターゲット列 '{target_column}' がCSVに存在しません。")
            return None

        # [修正] is_collision ではなく target_column を使用
        essential_cols = ["loop_num", target_column] + self.feature_names
        df_merged = df_merged.dropna(subset=essential_cols)
        
        # [追加] エラー値（-1など）を除外。0(合格)と1(違反)のみを残す
        df_valid = df_merged[df_merged[target_column].isin([0, 1])]
        
        if len(df_valid) == 0:
            print(f"[Estimator] エラー: '{target_column}' の有効なデータセットが空です。")
            return None
            
        # [追加] 0と1の両方のクラスが存在しないと分類器がエラーになるため防御
        if df_valid[target_column].nunique() < 2:
            print(f"[Estimator] 待機中: '{target_column}' のデータが1種類しかありません（境界線が引けません）。")
            return None
            
        return df_valid

    # [修正] 引数に target_column を追加（デフォルトは c_collision）
    def train(self, target_column="c_collision"):
        df = self.load_and_merge_data(target_column)
        if df is None:
            return False
        
        X = df[self.feature_names].values
        # [修正] is_collision ではなく target_column を使用
        y = df[target_column].values

        X_scaled = self.scaler.fit_transform(X)

        print(f"[Estimator] {len(X)}件のデータで '{target_column}' を学習中... (項目: {self.feature_names})")
        
        try:
            self.model.fit(X_scaled, y)
            self.is_trained = True
            self.current_target = target_column # 成功したら記録
            print(f"[Estimator] 学習完了。最適化カーネル: {self.model.kernel_}")
            return True
        except Exception as e:
            print(f"[Estimator] 学習失敗: {e}")
            return False

    # あなたの予測ロジックをそのまま採用
    def predict_uncertainty(self, X_new):
        if not self.is_trained:
            return None, None

        X_new_scaled = self.scaler.transform(X_new)
        probabilities = self.model.predict_proba(X_new_scaled)[:, 1]
        uncertainties = 1.0 - 2.0 * np.abs(probabilities - 0.5)

        return probabilities, uncertainties
