#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import numpy as np
import time

# 修正: 固定の設定ファイルをインポートせず、Estimatorのみを読み込む
from estimator import SafetyEstimator

class ActiveLearningStrategist:
    # 修正: 初期化時にシナリオ名と設定オブジェクトを受け取る
    def __init__(self, scenario_name, config, num_candidates=10000):
        self.scenario_name = scenario_name
        self.config = config
        self.num_candidates = num_candidates
        # Estimatorを生成する際にもシナリオ名と設定を渡す
        self.estimator = SafetyEstimator(scenario_name, config)

    def generate_candidate_points(self):
        """
        AIフェーズ用：設定ファイルの PARAM_RANGES に基づき、
        どのようなパラメータ構成でも自動で候補点（1万個）を生成する
        """
        ranges = self.config.PARAM_RANGES
        
        # パラメータ名と範囲をループで回し、ランダムな列を作成
        cols = []
        for p_name, (p_min, p_max) in ranges.items():
            cols.append(np.random.uniform(p_min, p_max, self.num_candidates))
            
        # 全ての列を結合して行列にする
        candidates = np.column_stack(cols)
        return candidates

    def get_fresh_random_point(self):
        """
        ランダムフェーズ用：現在の設定から、その場でパラメータを1点サンプリングする
        """
        ranges = self.config.PARAM_RANGES
        # どのシナリオでも対応できるよう辞書形式で生成
        return {name: np.random.uniform(*r) for name, r in ranges.items()}

    def decide_next_target(self):
        """過去のデータを分析し、次に実行すべき最適なパラメータを決定する"""
        print(f"\n=== 司令官(Strategist) [{self.scenario_name}] 次期作戦の立案開始 ===")
        
        # 現在のデータ数を取得
        df = self.estimator.load_and_merge_data()
        num_samples = len(df) if df is not None else 0
        
        # 初期探索の回数を設定から取得、あるいはデフォルト値を使用
        INITIAL_EXPLORATION_LIMIT = 100 

        # ---------------------------------------------------------
        # 1. 初期土台作りフェーズ (100回まで)
        # ---------------------------------------------------------
        if num_samples < INITIAL_EXPLORATION_LIMIT:
            print(f"[Strategist] 🛠️ 初期探索中 ({num_samples + 1}/{INITIAL_EXPLORATION_LIMIT})")
            
            point_dict = self.get_fresh_random_point()
            point_dict["reason"] = f"Initial Random Sampling ({num_samples + 1})"
            return point_dict

        # ---------------------------------------------------------
        # 2. AI(GPC) 学習と予測フェーズ (101回目以降)
        # ---------------------------------------------------------
        success = self.estimator.train()
        
        # データが偏っている場合などはランダム探索を継続
        if not success or len(np.unique(self.estimator.model.classes_)) < 2:
            print("[Strategist] ⚠️ データ不足または偏りのため、ランダム探索を継続します。")
            point_dict = self.get_fresh_random_point()
            point_dict["reason"] = "Extended Random (Imbalanced Data)"
            return point_dict

        # ---------------------------------------------------------
        # 3. Active Learning (不確実性の高い場所を狙う)
        # ---------------------------------------------------------
        candidates = self.generate_candidate_points()
        
        print(f"[Strategist] 🧠 過去{num_samples}件のデータを分析し、最適なターゲットを算出中...")
        probs, uncerts = self.estimator.predict_uncertainty(candidates)
        
        # 最も不確実性が高い（衝突するか安全か際どい）インデックスを抽出
        best_idx = np.argmax(uncerts)
        best_point = candidates[best_idx]
        
        # パラメータ名と数値を紐付けた結果を作成
        param_names = list(self.config.PARAM_RANGES.keys())
        result = {name: best_point[i] for i, name in enumerate(param_names)}
        
        # 選定理由を付与
        result["reason"] = f"Active Learning (Uncertainty: {uncerts[best_idx]:.3f})"
        
        print(f"[Strategist] 🎯 精密狙い撃ちターゲットを発見（不確実性: {uncerts[best_idx]:.3f}）")
        return result
