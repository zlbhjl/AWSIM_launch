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

        # --- 設定ファイル(config)から戦略パラメータを動的に取得 ---
        self.target_priorities = getattr(self.config, 'TARGET_PRIORITIES', [])
        self.INITIAL_EXPLORATION_LIMIT = getattr(self.config, 'INITIAL_EXPLORATION_LIMIT', 100)
        self.MIN_SAMPLES = getattr(self.config, 'MIN_SAMPLES', 500)
        self.MAX_SAMPLES = getattr(self.config, 'MAX_SAMPLES', 2000)

        # [追加] フェーズ移行のしきい値とマージン範囲をConfigから取得
        self.STEP3_THRESHOLD = getattr(self.config, 'GRAY_ZONE_THRESHOLD_STEP3', 0.15)
        self.MARGIN_RANGE = getattr(self.config, 'MARGIN_RANGE', (0.3, 0.48))

    def get_sobol_point(self, index):
        sampler = qmc.Sobol(d=self.dim, scramble=True, seed=42)
        sample = sampler.random(n=index + 1)[-1] 
        point_dict = {name: self.config.PARAM_RANGES[name][0] + sample[i] * (self.config.PARAM_RANGES[name][1] - self.config.PARAM_RANGES[name][0]) 
                      for i, name in enumerate(self.param_names)}
        return point_dict

    def get_best_target(self, df):
        if df is None or len(df) == 0: return None
        for target in self.target_priorities:
            if target in df.columns:
                v = df[df[target].isin([0, 1])]
                if 1 in v[target].values and 0 in v[target].values: return target
        return None

    def decide_next_target(self):
        df_results = self.estimator.load_results()
        num_samples = len(df_results) if df_results is not None else 0
        best_target = self.get_best_target(df_results)
        num_violations = (df_results[best_target] == 1).sum() if (df_results is not None and best_target) else 0

        # --- STEP 1: 初期探索 ---
        if num_samples < self.INITIAL_EXPLORATION_LIMIT or num_violations == 0:
            return {**self.get_sobol_point(num_samples), "reason": f"STEP1: Global Search (V:{num_violations})"}

        # AI学習・予測
        self.estimator.train(target_column=best_target)
        candidates = self.generate_candidate_points()
        mean, std = self.estimator.predict_uncertainty(candidates)
        if mean is None: return {**self.get_sobol_point(num_samples), "reason": "Fallback (Error)"}

        # --- 数学的指標の計算 (Configの値を反映) ---
        m_low, m_high = self.MARGIN_RANGE
        
        # 1. ターゲット領域（マージン）の特定
        margin_idx = np.where((mean >= m_low) & (mean <= m_high))[0]
        # 2. マージン内の未確定地点
        unverified_idx = margin_idx[(mean[margin_idx] + 2 * std[margin_idx] >= 0.5)] if len(margin_idx) > 0 else []
        # 3. 全体のグレーゾーン比率
        gray_mask = ~((mean + 2 * std < 0.5) | (mean - 2 * std >= 0.5))
        gray_ratio = np.sum(gray_mask) / self.num_candidates

        # --- 【GOAL】終了判定 ---
        if gray_ratio <= self.STEP3_THRESHOLD and len(unverified_idx) == 0 and num_samples >= self.MIN_SAMPLES:
            self._print_final_report(num_samples, best_target, np.mean(mean < 0.5), np.mean(mean >= 0.5), gray_ratio, "Margin Fully Verified")
            return {"system_command": "stop", "reason": "Target Area Verification Complete"}
        
        if num_samples >= self.MAX_SAMPLES:
            return {"system_command": "stop", "reason": "Max Samples Reached"}

        # --- 探索戦略の分岐 ---
        dice = np.random.rand()

        # 【STEP 2】境界形成 (gray_ratio > STEP3_THRESHOLD)
        if gray_ratio > self.STEP3_THRESHOLD:
            phase = "STEP2"
            exploration_rate = 0.50 if gray_ratio > 0.30 else 0.20
            if dice < exploration_rate:
                best_idx, reason = np.argmax(std), f"{phase}: Exploration"
            else:
                best_idx, reason = np.argmin(np.abs(mean - 0.5)), f"{phase}: Boundary 0.5"

        # 【STEP 3】安全確証 (gray_ratio <= STEP3_THRESHOLD)
        else:
            phase = "STEP3"
            if len(unverified_idx) > 0:
                best_idx = unverified_idx[np.argmax(std[unverified_idx])]
                reason = f"{phase}: Stress Test (M:{mean[best_idx]:.2f})"
            else:
                best_idx = np.argmax(std)
                reason = f"{phase}: Global Cleanup"

        best_point = candidates[best_idx]
        result = {name: best_point[i] for i, name in enumerate(self.param_names)}
        result["reason"] = reason
        print(f"[Strategist] {phase} | Gray: {gray_ratio*100:.1f}% | Unverified Margin: {len(unverified_idx)}")
        return result

    def _print_final_report(self, num_samples, target, safe_ratio, danger_ratio, gray_ratio, reason):
        print("\n" + "="*55 + f"\n🎉 [検証完了] {reason}\n" + "="*55)
        print(f"・総実行回数: {num_samples}回\n・主要ターゲット: {target}")
        print(f"✅ 安全領域: {safe_ratio*100:.1f}% | ❌ 危険領域: {danger_ratio*100:.1f}% | ⚠️ 全体グレー: {gray_ratio*100:.1f}%")
        print("="*55 + "\n")

    def generate_candidate_points(self):
        cols = [np.random.uniform(self.config.PARAM_RANGES[n][0], self.config.PARAM_RANGES[n][1], self.num_candidates) for n in self.param_names]
        return np.column_stack(cols)
