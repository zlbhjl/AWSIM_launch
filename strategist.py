#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import numpy as np
from scipy.stats import qmc  # Sobol配列生成用
from estimator import SafetyEstimator

class ActiveLearningStrategist:
    def __init__(self, scenario_name, config, num_candidates=10000):
        self.scenario_name = scenario_name
        self.config = config
        self.num_candidates = num_candidates
        # Estimatorを生成する際にもシナリオ名と設定を渡す
        self.estimator = SafetyEstimator(scenario_name, config)
        
        # パラメータ名と次元数を確定
        self.param_names = list(self.config.PARAM_RANGES.keys())
        self.dim = len(self.param_names)

    def get_sobol_point(self, index):
        """
        Sobol系列のn番目の点を取得し、実パラメータ範囲にスケーリングする
        """
        # 決定論的な探索のためseedを固定(scramble=Trueで分布の質を向上)
        sampler = qmc.Sobol(d=self.dim, scramble=True, seed=42)
        
        # 累積試行回数(index)に基づいて次の1点を抽出
        # n=index+1個生成し、その最後の1点を取ることで系列を維持
        sample = sampler.random(n=index + 1)[-1] 
        
        point_dict = {}
        for i, name in enumerate(self.param_names):
            p_min, p_max = self.config.PARAM_RANGES[name] #
            point_dict[name] = p_min + sample[i] * (p_max - p_min)
            
        return point_dict

    def decide_next_target(self):
        """過去のデータを分析し、次に実行すべき最適なパラメータを決定する"""
        print(f"\n=== 司令官(Strategist) [{self.scenario_name}] 次期作戦の立案開始 ===")
        
        # 現在のデータ（判定結果付き）をロード
        df = self.estimator.load_and_merge_data()
        num_samples = len(df) if df is not None else 0
        
        INITIAL_EXPLORATION_LIMIT = 100 

        # ---------------------------------------------------------
        # フェーズ1 & 2: Sobol探索 (初期100回 または 衝突未発見時)
        # ---------------------------------------------------------
        # 100回未満、もしくは学習に必要な「衝突(1)」と「安全(0)」が揃っていない場合
        if num_samples < INITIAL_EXPLORATION_LIMIT:
            print(f"[Strategist] 🌌 Sobol探索中 ({num_samples + 1}/{INITIAL_EXPLORATION_LIMIT})")
            point_dict = self.get_sobol_point(num_samples)
            point_dict["reason"] = f"Sobol Phase ({num_samples + 1})"
            return point_dict

        # AI学習を試みる
        success = self.estimator.train()
        
        # 衝突データが1件もなく、学習が成立しない（判定の種類が2未満）場合
        if not success or len(np.unique(self.estimator.model.classes_)) < 2:
            print(f"[Strategist] ⚠️ 衝突未発見のため、Sobol探索を延長します (Trial: {num_samples + 1})")
            # インデックスを維持したままSobol配列の続きを生成
            point_dict = self.get_sobol_point(num_samples)
            point_dict["reason"] = "Sobol Extended (No Collision Found)"
            return point_dict

        # ---------------------------------------------------------
        # フェーズ3: Active Learning (衝突発見後の精密狙い撃ち)
        # ---------------------------------------------------------
        print(f"[Strategist] 🧠 衝突データ発見済み。精密ターゲットを算出中...")
        candidates = self.generate_candidate_points()
        
        # 不確実性スコアを算出
        probs, uncerts = self.estimator.predict_uncertainty(candidates)
        
        # 最も不確実性が高い（境界線に近い）点を選択
        best_idx = np.argmax(uncerts)
        best_point = candidates[best_idx]
        
        result = {name: best_point[i] for i, name in enumerate(self.param_names)}
        result["reason"] = f"Active Learning (Uncertainty: {uncerts[best_idx]:.3f})"
        
        print(f"[Strategist] 🎯 境界線ターゲットを発見（不確実性: {uncerts[best_idx]:.3f}）")
        return result

    def generate_candidate_points(self):
        """AIフェーズ用の候補点生成"""
        cols = []
        for name in self.param_names:
            p_min, p_max = self.config.PARAM_RANGES[name] #
            cols.append(np.random.uniform(p_min, p_max, self.num_candidates))
            
        return np.column_stack(cols)
