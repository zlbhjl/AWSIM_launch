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
        self.param_file = os.path.join(self.traces_dir, f"{scenario_name}_parameters.csv")
        self.result_file = os.path.join(self.traces_dir, "checker_results.csv")
        
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

    def load_results(self):
        """
        検証結果CSVを読み込む。
        新旧フォーマット（ヘッダー有無）を自動判別し、Configの定義に同期させる。
        """
        if not os.path.exists(self.result_file):
            return None
            
        try:
            # Config で定義された「あるべき列名」
            expected_labels = ["loop_num"] + getattr(self.config, 'RESULT_LABELS', [])
            
            # 1. まず普通に読み込む
            df = pd.read_csv(self.result_file)
            
            # 2. ヘッダー自動判別ロジック
            # 最初の列名が数値（loop_numのデータ）なら「ヘッダーなし」と判定
            if str(df.columns[0]).replace('.','',1).isdigit():
                df = pd.read_csv(self.result_file, header=None, names=expected_labels)
            else:
                # すでに見出しがある場合は、Configの最新定義に名前を強制上書きして同期を保証
                # (これにより、途中でラベル名を変えても過去のデータが壊れない)
                df.columns = expected_labels[:len(df.columns)]
            
            return df
        except Exception as e:
            print(f"[Estimator] ❌ 結果CSVの読み込み失敗: {e}")
            return None

    def load_and_merge_data(self, target_column):
        """
        パラメータと結果を結合し、学習用データセット (X, y) を作成。
        """
        df_results = self.load_results()
        if df_results is None or not os.path.exists(self.param_file):
            return None

        # パラメータCSVを読み込み、loop_num で内部結合
        df_params = pd.read_csv(self.param_file)
        df_merged = pd.merge(df_params, df_results, on="loop_num", how="inner")
        
        # ターゲット指標の存在確認
        if target_column not in df_merged.columns:
            print(f"[Estimator] ⚠️ 指標 '{target_column}' が見つかりません。")
            return None

        # 欠損値の除去と、0/1 (Boolean) データへの絞り込み
        essential_cols = ["loop_num", target_column] + self.feature_names
        df_merged = df_merged.dropna(subset=essential_cols)
        df_valid = df_merged[df_merged[target_column].isin([0, 1])]
        
        # 学習には「安全(0)」と「危険(1)」の両方のサンプルが必要
        if len(df_valid) < 2 or df_valid[target_column].nunique() < 2:
            return None
            
        return df_valid

    def train(self, target_column):
        """
        指定されたターゲット指標の境界線を学習。
        """
        df = self.load_and_merge_data(target_column)
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
