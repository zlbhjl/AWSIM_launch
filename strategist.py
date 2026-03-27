#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import numpy as np
import pandas as pd
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

        # ターゲットの優先順位リスト（厳しい順）
        self.target_priorities = [
            "c_collision",
            "c_ttc_0.7",
            "c_ttc_1.2",
            "c_ttc_1.5"
        ]

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

    def get_best_target(self, df):
        """
        現在のデータから、学習可能な最も厳しいターゲットを選択する
        """
        if df is None or len(df) == 0:
            return None

        # 優先順位が高い（厳しい）順にチェック
        for target in self.target_priorities:
            if target in df.columns:
                # エラー値(-1など)を除外した有効なデータのみを見る
                valid_data = df[df[target].isin([0, 1])]
                
                # 違反(1)が1件以上あり、かつ合格(0)も存在すれば学習可能（境界線が引ける）
                if 1 in valid_data[target].values and 0 in valid_data[target].values:
                    return target
        
        # 学習可能なターゲットが見つからない場合
        return None

    def decide_next_target(self):
        """過去のデータを分析し、次に実行すべき最適なパラメータを決定する"""
        print(f"\n=== 司令官(Strategist) [{self.scenario_name}] 次期作戦の立案開始 ===")
        
        # 現在のデータをロード (特定のターゲットに依存しない全データ)
        df_results = None
        if os.path.exists(self.estimator.result_file):
            try:
                df_results = pd.read_csv(self.estimator.result_file)
            except pd.errors.EmptyDataError:
                pass
                
        num_samples = len(df_results) if df_results is not None else 0
        INITIAL_EXPLORATION_LIMIT = 100 

        # ---------------------------------------------------------
        # フェーズ1 & 2: Sobol探索 (初期100回 または 違反未発見時)
        # ---------------------------------------------------------
        # 100回未満の場合は無条件でSobol探索
        if num_samples < INITIAL_EXPLORATION_LIMIT:
            print(f"[Strategist] 🌌 Sobol探索中 ({num_samples + 1}/{INITIAL_EXPLORATION_LIMIT})")
            point_dict = self.get_sobol_point(num_samples)
            point_dict["reason"] = f"Sobol Phase ({num_samples + 1})"
            return point_dict

        # AI学習フェーズ: 最適なターゲットを選択
        best_target = self.get_best_target(df_results)

        # 全てのターゲットで違反(1)が見つかっていない場合（またはデータ不足）
        if best_target is None:
            print(f"[Strategist] ⚠️ 違反未発見のため、Sobol探索を延長します (Trial: {num_samples + 1})")
            point_dict = self.get_sobol_point(num_samples)
            point_dict["reason"] = "Sobol Extended (No Violations Found)"
            return point_dict

        # ---------------------------------------------------------
        # フェーズ3: Active Learning (違反発見後の適応的狙い撃ち)
        # ---------------------------------------------------------
        print(f"[Strategist] 🧠 違反データ発見済み。ターゲット '{best_target}' の境界線を算出中...")
        
        # 選ばれたターゲットでEstimatorを学習させる
        success = self.estimator.train(target_column=best_target)
        
        if not success:
             print(f"[Strategist] ⚠️ 学習失敗のため、Sobol探索を延長します (Trial: {num_samples + 1})")
             point_dict = self.get_sobol_point(num_samples)
             point_dict["reason"] = "Sobol Extended (Training Failed)"
             return point_dict

        candidates = self.generate_candidate_points()
        
        # 不確実性スコアを算出
        probs, uncerts = self.estimator.predict_uncertainty(candidates)
        
        if probs is None or uncerts is None:
             print(f"[Strategist] ⚠️ 予測失敗のため、ランダム探索にフォールバックします")
             best_idx = np.random.randint(0, len(candidates))
             uncerts = np.zeros(len(candidates)) # ダミー
        else:
             # 最も不確実性が高い（確率が0.5に近い＝境界線）点を選択
             best_idx = np.argmax(uncerts)

        best_point = candidates[best_idx]
        
        result = {name: best_point[i] for i, name in enumerate(self.param_names)}
        result["reason"] = f"Active Learning ({best_target}, Uncert: {uncerts[best_idx]:.3f})"
        
        print(f"[Strategist] 🎯 '{best_target}' の境界線ターゲットを発見（不確実性: {uncerts[best_idx]:.3f}）")
        return result

    def generate_candidate_points(self):
        """AIフェーズ用の候補点生成"""
        cols = []
        for name in self.param_names:
            p_min, p_max = self.config.PARAM_RANGES[name] #
            cols.append(np.random.uniform(p_min, p_max, self.num_candidates))
            
        return np.column_stack(cols)
