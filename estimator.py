#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import pandas as pd
import numpy as np
# 変更点1: Classifier から Regressor に変更
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C
from sklearn.preprocessing import StandardScaler

class SafetyEstimator:
    def __init__(self, scenario_name, config, traces_dir="~/simulation_traces"):
        self.traces_dir = os.path.expanduser(traces_dir)
        self.param_file = os.path.join(self.traces_dir, f"{scenario_name}_parameters.csv")
        self.result_file = os.path.join(self.traces_dir, "checker_results.csv")
        
        self.config = config
        self.scaler = StandardScaler()
        kernel = C(1.0, (1e-3, 1e3)) * RBF(1.0, (1e-2, 1e2))
        
        # 変更点2: GaussianProcessRegressorを使用。alpha=0.01 を追加。
        self.model = GaussianProcessRegressor(kernel=kernel, alpha=0.01, n_restarts_optimizer=5, random_state=42)
        
        self.is_trained = False
        self.feature_names = list(self.config.PARAM_RANGES.keys())
        self.current_target = None 

    def load_and_merge_data(self, target_column):
        if not os.path.exists(self.param_file) or not os.path.exists(self.result_file):
            print(f"[Estimator] エラー: 必要なファイル ({self.param_file} または {self.result_file}) がありません。")
            return None

        df_params = pd.read_csv(self.param_file)
        df_results = pd.read_csv(self.result_file)

        df_merged = pd.merge(df_params, df_results, on="loop_num", how="inner")
        
        if target_column not in df_merged.columns:
            print(f"[Estimator] エラー: ターゲット列 '{target_column}' がCSVに存在しません。")
            return None

        essential_cols = ["loop_num", target_column] + self.feature_names
        df_merged = df_merged.dropna(subset=essential_cols)
        
        df_valid = df_merged[df_merged[target_column].isin([0, 1])]
        
        if len(df_valid) == 0:
            print(f"[Estimator] エラー: '{target_column}' の有効なデータセットが空です。")
            return None
            
        if df_valid[target_column].nunique() < 2:
            print(f"[Estimator] 待機中: '{target_column}' のデータが1種類しかありません（境界線が引けません）。")
            return None
            
        return df_valid

    def train(self, target_column="c_collision"):
        df = self.load_and_merge_data(target_column)
        if df is None:
            return False
        
        X = df[self.feature_names].values
        y = df[target_column].values

        X_scaled = self.scaler.fit_transform(X)

        print(f"[Estimator] {len(X)}件のデータで '{target_column}' を学習中... (項目: {self.feature_names})")
        
        try:
            self.model.fit(X_scaled, y)
            self.is_trained = True
            self.current_target = target_column
            print(f"[Estimator] 学習完了。最適化カーネル: {self.model.kernel_}")
            return True
        except Exception as e:
            print(f"[Estimator] 学習失敗: {e}")
            return False

    def predict_uncertainty(self, X_new):
        if not self.is_trained:
            return None, None

        X_new_scaled = self.scaler.transform(X_new)
        
        # 変更点3: predict_probaの代わりにpredictを使い、return_std=Trueで真のバラつき(std)を取得
        mean, std = self.model.predict(X_new_scaled, return_std=True)

        return mean, std
