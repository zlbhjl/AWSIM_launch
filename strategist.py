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
        sample = sampler.random(n=index + 1)[-1] 
        
        point_dict = {}
        for i, name in enumerate(self.param_names):
            p_min, p_max = self.config.PARAM_RANGES[name]
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
        
        return None

    def decide_next_target(self):
        """過去のデータを分析し、次に実行すべき最適なパラメータを決定する"""
        print(f"\n=== 司令官(Strategist) [{self.scenario_name}] 次期作戦の立案開始 ===")
        
        # 現在のデータをロード
        df_results = None
        if os.path.exists(self.estimator.result_file):
            try:
                df_results = pd.read_csv(self.estimator.result_file)
            except pd.errors.EmptyDataError:
                pass
                
        num_samples = len(df_results) if df_results is not None else 0
        INITIAL_EXPLORATION_LIMIT = 100 

        # ---------------------------------------------------------
        # フェーズ1: Sobol探索 (初期100回 または 違反未発見時)
        # ---------------------------------------------------------
        if num_samples < INITIAL_EXPLORATION_LIMIT:
            print(f"[Strategist] 🌌 Sobol探索中 ({num_samples + 1}/{INITIAL_EXPLORATION_LIMIT})")
            point_dict = self.get_sobol_point(num_samples)
            point_dict["reason"] = f"Sobol Phase ({num_samples + 1})"
            return point_dict

        best_target = self.get_best_target(df_results)

        if best_target is None:
            print(f"[Strategist] ⚠️ 違反未発見のため、Sobol探索を延長します (Trial: {num_samples + 1})")
            point_dict = self.get_sobol_point(num_samples)
            point_dict["reason"] = "Sobol Extended (No Violations Found)"
            return point_dict

        # ---------------------------------------------------------
        # フェーズ2 & 3: Active Learning (境界探索 ＆ 安全確証)
        # ---------------------------------------------------------
        print(f"[Strategist] 🧠 違反データ発見済み。ターゲット '{best_target}' の境界線を算出中...")
        
        success = self.estimator.train(target_column=best_target)
        
        if not success:
             print(f"[Strategist] ⚠️ 学習失敗のため、Sobol探索を延長します (Trial: {num_samples + 1})")
             point_dict = self.get_sobol_point(num_samples)
             point_dict["reason"] = "Sobol Extended (Training Failed)"
             return point_dict

        candidates = self.generate_candidate_points()
        
        # 変更点1: Classifierの確率ではなく、Regressorの予測値(mean)と真の不確実性(std)を受け取る
        mean, std = self.estimator.predict_uncertainty(candidates)
        
        if mean is None or std is None:
             print(f"[Strategist] ⚠️ 予測失敗のため、ランダム探索にフォールバックします")
             best_idx = np.random.randint(0, len(candidates))
             best_mean, best_std = 0.5, 0.0 # ダミー
             reason_str = "Random Fallback"
        else:
             # 変更点2: ハイブリッド戦略の分岐
             exploration_rate = 0.20 # 20%の確率で安全確証(守り)を優先
             
             if np.random.rand() < exploration_rate:
                 # 【フェーズ3: 安全確証 (守り)】
                 # 予測は安全(mean < 0.5)だが、最悪のケースを想定すると危険(mean + 2*std > 0.5)な点
                 mask = (mean < 0.5) & ((mean + 2 * std) > 0.5)
                 
                 if np.any(mask):
                     # 該当する点の中で、最も不確実性(std)が高い場所を叩く
                     valid_indices = np.where(mask)[0]
                     best_idx = valid_indices[np.argmax(std[mask])]
                     best_mean, best_std = mean[best_idx], std[best_idx]
                     reason_str = f"Safety Validation ({best_target}, M:{best_mean:.2f}, Std:{best_std:.3f})"
                     print(f"[Strategist] 🛡️ '{best_target}' の安全確証！(データが薄い安全領域をテストします)")
                 else:
                     # 安全領域の確証が完了している場合、通常の境界探索に戻る
                     print(f"[Strategist] 🎉 '{best_target}' の安全領域の統計的確証が完了しました！境界探索に戻ります。")
                     best_idx = np.argmin(np.abs(mean - 0.5))
                     best_mean, best_std = mean[best_idx], std[best_idx]
                     reason_str = f"Boundary Search ({best_target}, M:{best_mean:.2f}, Std:{best_std:.3f})"
             else:
                 # 【フェーズ2: 境界探索 (攻め)】
                 # meanが0.5（境界線）に最も近い場所を叩く
                 best_idx = np.argmin(np.abs(mean - 0.5))
                 best_mean, best_std = mean[best_idx], std[best_idx]
                 reason_str = f"Boundary Search ({best_target}, M:{best_mean:.2f}, Std:{best_std:.3f})"
                 print(f"[Strategist] ⚔️ '{best_target}' の境界線を探索中...")

        best_point = candidates[best_idx]
        
        result = {name: best_point[i] for i, name in enumerate(self.param_names)}
        
        # 変更点3: 詳細なログと記録
        result["reason"] = reason_str
        print(f"[Strategist] 🎯 次のターゲット: {reason_str}")
        
        return result

    def generate_candidate_points(self):
        """AIフェーズ用の候補点生成"""
        cols = []
        for name in self.param_names:
            p_min, p_max = self.config.PARAM_RANGES[name]
            cols.append(np.random.uniform(p_min, p_max, self.num_candidates))
            
        return np.column_stack(cols)
