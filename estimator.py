#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import pandas as pd
import numpy as np
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C
from sklearn.preprocessing import StandardScaler

class SafetyEstimator:
    def __init__(self, scenario_name, config, traces_dir="~/simulation_traces"):
        """
        汎用安全性推定器 (Gaussian Process Regression)
        Config-Driven アーキテクチャに基づき、あらゆるシナリオに即座に適応します。
        """
        self.traces_dir = os.path.expanduser(traces_dir)
        self.dataset_file = os.path.join(self.traces_dir, f"{scenario_name}_dataset.csv")
        
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
        """
        if not os.path.exists(self.dataset_file):
            return None
        try:
            return pd.read_csv(self.dataset_file)
        except Exception as e:
            print(f"[Estimator] ❌ データセットCSVの読み込み失敗: {e}")
            return None

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

        # 欠損値の除去と、0/1 (Boolean) データへの絞り込み
        essential_cols = ["loop_num", target_column] + self.feature_names
        df_dataset = df_dataset.dropna(subset=essential_cols)
        df_valid = df_dataset[df_dataset[target_column].isin([0, 1])]
        
        # 学習には「安全(0)」と「危険(1)」の両方のサンプルが必要
        if len(df_valid) < 2 or df_valid[target_column].nunique() < 2:
            return None
            
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
