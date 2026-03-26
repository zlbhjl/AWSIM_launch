#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import pandas as pd
import numpy as np
from sklearn.gaussian_process import GaussianProcessClassifier
from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C
from sklearn.preprocessing import StandardScaler

# ★ 司令塔(config)を読み込む
import uturn_config 

class SafetyEstimator:
    def __init__(self, traces_dir="~/simulation_traces"):
        self.traces_dir = os.path.expanduser(traces_dir)
        self.param_file = os.path.join(self.traces_dir, "uturn_parameters.csv")
        self.result_file = os.path.join(self.traces_dir, "checker_results.csv")
        
        self.scaler = StandardScaler()
        kernel = C(1.0, (1e-3, 1e3)) * RBF(1.0, (1e-2, 1e2))
        self.model = GaussianProcessClassifier(kernel=kernel, n_restarts_optimizer=5, random_state=42)
        
        self.is_trained = False
        
        # =========================================================
        # ★ スマートな修正1: configの PARAM_RANGES から「現在のパラメータ名」を自動取得する！
        # これなら将来パラメータが増えても、ここは一切書き直す必要がありません。
        # =========================================================
        self.feature_names = list(uturn_config.PARAM_RANGES.keys())

    def load_and_merge_data(self):
        if not os.path.exists(self.param_file) or not os.path.exists(self.result_file):
            print("[Estimator] エラー: 必要なCSVファイルが見つかりません。")
            return None

        df_params = pd.read_csv(self.param_file)
        df_results = pd.read_csv(self.result_file)

        df_merged = pd.merge(df_params, df_results, on="loop_num", how="inner")
        
        # =========================================================
        # ★ スマートな修正2: 必須な列（loop_num, 取得したパラメータ, is_collision）に
        # NaNが含まれている行「だけ」を正確に削除し、幽霊列を無視する。
        # =========================================================
        essential_cols = ["loop_num", "is_collision"] + self.feature_names
        df_merged = df_merged.dropna(subset=essential_cols)
        
        if len(df_merged) == 0:
            print("[Estimator] エラー: 結合・クリーンアップ後に有効なデータがありません。")
            return None
            
        return df_merged

    def train(self):
        df = self.load_and_merge_data()
        if df is None:
            return False
        
        # 動的に取得したパラメータの列だけを抽出する
        X = df[self.feature_names].values
        y = df["is_collision"].values

        X_scaled = self.scaler.fit_transform(X)

        print(f"[Estimator] {len(X)}件のデータでガウス過程モデルの学習を開始します...")
        print(f"[Estimator] 使用パラメータ: {self.feature_names}")
        
        self.model.fit(X_scaled, y)
        self.is_trained = True
        
        print(f"[Estimator] 学習完了！最適化カーネル: {self.model.kernel_}")
        return True

    def predict_uncertainty(self, X_new):
        if not self.is_trained:
            print("[Estimator] モデルが学習されていません。先にtrain()を実行してください。")
            return None, None

        X_new_scaled = self.scaler.transform(X_new)
        probabilities = self.model.predict_proba(X_new_scaled)[:, 1]
        uncertainties = 1.0 - 2.0 * np.abs(probabilities - 0.5)

        return probabilities, uncertainties

if __name__ == "__main__":
    estimator = SafetyEstimator()
    success = estimator.train()
