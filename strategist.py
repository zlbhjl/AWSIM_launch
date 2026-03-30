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
        self.estimator = SafetyEstimator(scenario_name, config)
        
        self.param_names = list(self.config.PARAM_RANGES.keys())
        self.dim = len(self.param_names)

        # --- 設定ファイル(config)からAIの戦略を読み込む ---
        # 設定ファイルに記述がない場合はデフォルト値を使用する安全設計 (getattrを使用)
        self.target_priorities = getattr(
            self.config, 'TARGET_PRIORITIES', 
            [
                "c_collision",
                "c_ttc_0.7",
                "c_ttc_1.2",
                "c_ttc_1.5"
            ]
        )
        
        # --- 終了条件の設定 (configから読み込み) ---
        self.INITIAL_EXPLORATION_LIMIT = getattr(self.config, 'INITIAL_EXPLORATION_LIMIT', 100)
        self.MIN_SAMPLES = getattr(self.config, 'MIN_SAMPLES', 500)
        self.MAX_SAMPLES = getattr(self.config, 'MAX_SAMPLES', 2000)
        self.TOLERANCE_GRAY_ZONE = getattr(self.config, 'TOLERANCE_GRAY_ZONE', 0.01)

    def get_sobol_point(self, index):
        sampler = qmc.Sobol(d=self.dim, scramble=True, seed=42)
        sample = sampler.random(n=index + 1)[-1] 
        
        point_dict = {}
        for i, name in enumerate(self.param_names):
            p_min, p_max = self.config.PARAM_RANGES[name]
            point_dict[name] = p_min + sample[i] * (p_max - p_min)
            
        return point_dict

    def get_best_target(self, df):
        if df is None or len(df) == 0:
            return None

        for target in self.target_priorities:
            if target in df.columns:
                valid_data = df[df[target].isin([0, 1])]
                if 1 in valid_data[target].values and 0 in valid_data[target].values:
                    return target
        return None

    def decide_next_target(self):
        print(f"\n=== 司令官(Strategist) [{self.scenario_name}] 次期作戦の立案開始 ===")
        
        df_results = None
        if os.path.exists(self.estimator.result_file):
            try:
                df_results = pd.read_csv(self.estimator.result_file)
            except pd.errors.EmptyDataError:
                pass
                
        num_samples = len(df_results) if df_results is not None else 0

        # ---------------------------------------------------------
        # フェーズ1: Sobol探索 (初期回数 または 違反未発見時)
        # ---------------------------------------------------------
        if num_samples < self.INITIAL_EXPLORATION_LIMIT:
            print(f"[Strategist] 🌌 Sobol探索中 ({num_samples + 1}/{self.INITIAL_EXPLORATION_LIMIT})")
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
        mean, std = self.estimator.predict_uncertainty(candidates)
        
        if mean is None or std is None:
             print(f"[Strategist] ⚠️ 予測失敗のため、ランダム探索にフォールバックします")
             best_idx = np.random.randint(0, len(candidates))
             best_mean, best_std = 0.5, 0.0
             reason_str = "Random Fallback"
        else:
             # --- 領域の分類と終了判定 ---
             safe_mask = (mean + 2 * std) < 0.5    # 誤差を考慮しても絶対に安全
             danger_mask = (mean - 2 * std) >= 0.5 # 誤差を考慮しても絶対に危険
             gray_mask = ~(safe_mask | danger_mask) # どちらとも言えない不確実な領域
             
             total_candidates = len(candidates)
             safe_ratio = np.sum(safe_mask) / total_candidates
             danger_ratio = np.sum(danger_mask) / total_candidates
             gray_ratio = np.sum(gray_mask) / total_candidates

             # --- 堅牢な終了シグナルの返却 ---
             if num_samples >= self.MIN_SAMPLES and gray_ratio <= self.TOLERANCE_GRAY_ZONE:
                 self._print_final_report(num_samples, best_target, safe_ratio, danger_ratio, gray_ratio, "統計的確証 (グレーゾーン1%未満)")
                 # 明示的なシステムコマンドと理由を辞書で返す
                 return {"system_command": "stop", "reason": "Validation Completed (Gray zone < 1%)"}
             
             elif num_samples >= self.MAX_SAMPLES:
                 self._print_final_report(num_samples, best_target, safe_ratio, danger_ratio, gray_ratio, "最大実行回数 (セーフティネット到達)")
                 # 明示的なシステムコマンドと理由を辞書で返す
                 return {"system_command": "stop", "reason": "Max Samples Reached"}

             # --- ハイブリッド探索の続行 ---
             exploration_rate = 0.20 # 20%の確率で安全確証(守り)を優先
             
             if np.random.rand() < exploration_rate:
                 # 【フェーズ3: 安全確証 (守り)】
                 mask = (mean < 0.5) & ((mean + 2 * std) >= 0.5)
                 
                 if np.any(mask):
                     valid_indices = np.where(mask)[0]
                     best_idx = valid_indices[np.argmax(std[mask])]
                     best_mean, best_std = mean[best_idx], std[best_idx]
                     reason_str = f"Safety Validation ({best_target}, M:{best_mean:.2f}, Std:{best_std:.3f})"
                     print(f"[Strategist] 🛡️ '{best_target}' の安全確証！ (グレーゾーン残り: {gray_ratio*100:.1f}%)")
                 else:
                     best_idx = np.argmin(np.abs(mean - 0.5))
                     best_mean, best_std = mean[best_idx], std[best_idx]
                     reason_str = f"Boundary Search ({best_target}, M:{best_mean:.2f}, Std:{best_std:.3f})"
                     print(f"[Strategist] 🎉 確証すべきグレーゾーンはほぼありません。境界探索に移行します。")
             else:
                 # 【フェーズ2: 境界探索 (攻め)】
                 best_idx = np.argmin(np.abs(mean - 0.5))
                 best_mean, best_std = mean[best_idx], std[best_idx]
                 reason_str = f"Boundary Search ({best_target}, M:{best_mean:.2f}, Std:{best_std:.3f})"
                 print(f"[Strategist] ⚔️ '{best_target}' の境界線を探索中... (グレーゾーン残り: {gray_ratio*100:.1f}%)")

        best_point = candidates[best_idx]
        
        result = {name: best_point[i] for i, name in enumerate(self.param_names)}
        result["reason"] = reason_str
        print(f"[Strategist] 🎯 次のターゲット: {reason_str}")
        
        return result

    # --- 見える化レポートの出力関数 ---
    def _print_final_report(self, num_samples, target, safe_ratio, danger_ratio, gray_ratio, reason):
        print("\n" + "="*55)
        print(f"🎉 [検証完了] 自動運転AIの安全性検証が終了しました")
        print("="*55)
        print(f"・終了理由: {reason}")
        print(f"・総実行回数: {num_samples}回")
        print(f"・最終検証ターゲット: {target}")
        print("\n📊 【検証領域の統計的確証レポート】")
        print(f"✅ 安全が証明された領域 : {safe_ratio * 100:>5.1f} %")
        print(f"❌ 衝突が不可避な領域   : {danger_ratio * 100:>5.1f} %")
        print(f"⚠️ 不確実なグレーゾーン : {gray_ratio * 100:>5.1f} %")
        print("="*55 + "\n")

    def generate_candidate_points(self):
        cols = []
        for name in self.param_names:
            p_min, p_max = self.config.PARAM_RANGES[name]
            cols.append(np.random.uniform(p_min, p_max, self.num_candidates))
            
        return np.column_stack(cols)
