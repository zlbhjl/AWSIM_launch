#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import pandas as pd
import numpy as np
from sklearn.gaussian_process import GaussianProcessClassifier
from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C
from sklearn.preprocessing import StandardScaler

class SafetyEstimator:
    # 修正: scenario_name と config を受け取り、特定のファイルへの依存を排除
    def __init__(self, scenario_name, config, traces_dir="~/simulation_traces"):
        self.traces_dir = os.path.expanduser(traces_dir)
        # シナリオ名に基づいたパラメータCSVを動的に指定
        self.param_file = os.path.join(self.traces_dir, f"{scenario_name}_parameters.csv")
        self.result_file = os.path.join(self.traces_dir, "checker_results.csv")
        
        self.config = config
        self.scaler = StandardScaler()
        kernel = C(1.0, (1e-3, 1e3)) * RBF(1.0, (1e-2, 1e2))
        self.model = GaussianProcessClassifier(kernel=kernel, n_restarts_optimizer=5, random_state=42)
        
        self.is_trained = False
        
        # 設定ファイルの PARAM_RANGES からパラメータ名を自動取得（汎用化の肝）
        self.feature_names = list(self.config.PARAM_RANGES.keys())

    def load_and_merge_data(self):
        if not os.path.exists(self.param_file) or not os.path.exists(self.result_file):
            print(f"[Estimator] エラー: 必要なファイル ({self.param_file} または {self.result_file}) がありません。")
            return None

        df_params = pd.read_csv(self.param_file)
        df_results = pd.read_csv(self.result_file)

        # loop_num をキーにして結合
        df_merged = pd.merge(df_params, df_results, on="loop_num", how="inner")
        
        # 必須列（loop_num, 判定結果, および動的なパラメータ列）で欠損値を除去
        essential_cols = ["loop_num", "is_collision"] + self.feature_names
        df_merged = df_merged.dropna(subset=essential_cols)
        
        if len(df_merged) == 0:
            print("[Estimator] エラー: 有効なデータセットが空です。")
            return None
            
        return df_merged

    def train(self):
        df = self.load_and_merge_data()
        if df is None:
            return False
        
        # 動的に取得したパラメータ名（feature_names）を使用して特徴量を抽出
        X = df[self.feature_names].values
        y = df["is_collision"].values

        X_scaled = self.scaler.fit_transform(X)

        print(f"[Estimator] {len(X)}件のデータで学習中... (項目: {self.feature_names})")
        
        try:
            self.model.fit(X_scaled, y)
            self.is_trained = True
            print(f"[Estimator] 学習完了。最適化カーネル: {self.model.kernel_}")
            return True
        except Exception as e:
            print(f"[Estimator] 学習失敗: {e}")
            return False

    def predict_uncertainty(self, X_new):
        if not self.is_trained:
            return None, None

        X_new_scaled = self.scaler.transform(X_new)
        # 衝突確率 (クラス1の確率) を予測
        probabilities = self.model.predict_proba(X_new_scaled)[:, 1]
        # 0.5付近で最大(1.0)になる不確実性スコアを算出
        uncertainties = 1.0 - 2.0 * np.abs(probabilities - 0.5)

        return probabilities, uncertainties
