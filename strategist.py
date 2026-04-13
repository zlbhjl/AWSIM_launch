#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import numpy as np
import pandas as pd
from scipy.stats import qmc  # Sobol配列生成用
from estimator import SafetyEstimator

class ActiveLearningStrategist:
    def __init__(self, scenario_name, config, num_candidates=10000, focus_points=None):
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

        # [変更] フェーズ移行・終了条件の新しいパラメータ
        self.STABILITY_REFERENCE_POINTS = getattr(self.config, 'STABILITY_REFERENCE_POINTS', 2000)
        self.STABILITY_HISTORY_LENGTH = getattr(self.config, 'STABILITY_HISTORY_LENGTH', 50)
        self.STABILITY_HYSTERESIS = getattr(self.config, 'STABILITY_HYSTERESIS', (0.40, 0.60))
        self.STABILITY_SHIFT_THRESHOLD = getattr(self.config, 'STABILITY_SHIFT_THRESHOLD', 0.01)
        self.STABILITY_REQUIRED_STREAK = getattr(self.config, 'STABILITY_REQUIRED_STREAK', 3)
        self.STEP2_MAX_EXPLORATION = getattr(self.config, 'STEP2_MAX_EXPLORATION', 500)
        self.MARGIN_RANGE = getattr(self.config, 'MARGIN_RANGE', (0.3, 0.48))
        self.MARGIN_MAX_UNCERTAINTY = getattr(self.config, 'MARGIN_MAX_UNCERTAINTY', 0.05)
        
        # コマンドライン引数で渡された focus_points を使用 (Configに依存しない)
        self.FOCUS_POINTS = focus_points
        self.FOCUS_NOISE = getattr(self.config, 'FOCUS_NOISE', 0.05)

        # 状態管理変数
        self.reference_points = self.generate_candidate_points(num=self.STABILITY_REFERENCE_POINTS)
        self.stability_history = []
        self.stability_streak = 0
        self.step2_exploration_count = 0
        self.current_phase = "STEP1"

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

    def _evaluate_boundary_stability(self):
        mean, _ = self.estimator.predict_uncertainty(self.reference_points)
        if mean is None: return False, 0.0

        states = np.full(mean.shape, -1)
        states[mean < self.STABILITY_HYSTERESIS[0]] = 0
        states[mean > self.STABILITY_HYSTERESIS[1]] = 1

        self.stability_history.append(states)
        if len(self.stability_history) > self.STABILITY_HISTORY_LENGTH:
            self.stability_history.pop(0)

        if len(self.stability_history) < self.STABILITY_HISTORY_LENGTH:
            return False, 0.0

        oldest = self.stability_history[0]
        newest = self.stability_history[-1]

        flips = np.sum(((oldest == 0) & (newest == 1)) | ((oldest == 1) & (newest == 0)))
        shift_rate = flips / self.STABILITY_REFERENCE_POINTS

        if shift_rate < self.STABILITY_SHIFT_THRESHOLD:
            self.stability_streak += 1
        else:
            self.stability_streak = 0

        is_stable = self.stability_streak >= self.STABILITY_REQUIRED_STREAK
        return is_stable, shift_rate

    def decide_next_target(self):
        df_results = self.estimator.load_results()
        num_samples = len(df_results) if df_results is not None else 0
        best_target = self.get_best_target(df_results)
        num_violations = (df_results[best_target] == 1).sum() if (df_results is not None and best_target) else 0

        # --- 【STEP 0】フォーカスモードのピンポイント検証 ---
        if self.FOCUS_POINTS:
            exact_repeats = getattr(self.config, 'FOCUS_EXACT_REPEATS', 20)
            total_exact_samples = len(self.FOCUS_POINTS) * exact_repeats
            
            if num_samples < total_exact_samples:
                # どのポイントを何回目のリピートで実行するか計算
                point_idx = (num_samples // exact_repeats) % len(self.FOCUS_POINTS)
                repeat_idx = (num_samples % exact_repeats) + 1
                
                exact_point = self.FOCUS_POINTS[point_idx]
                result = {name: exact_point.get(name, sum(self.config.PARAM_RANGES[name])/2.0) for name in self.param_names}
                result["reason"] = f"[FOCUS] Exact Point {point_idx+1}/{len(self.FOCUS_POINTS)} (Repeat {repeat_idx}/{exact_repeats})"
                return result

        # --- STEP 1: 初期探索 ---
        if num_samples < self.INITIAL_EXPLORATION_LIMIT or num_violations == 0:
            if self.FOCUS_POINTS:
                # フォーカスモード時は、全体探索ではなく Focus の周辺をランダム探索する
                candidates = self.generate_candidate_points()
                best_point = candidates[np.random.randint(len(candidates))]
                result = {name: best_point[i] for i, name in enumerate(self.param_names)}
                result["reason"] = f"STEP1: Focus Neighborhood Search (V:{num_violations})"
                return result
            else:
                return {**self.get_sobol_point(num_samples), "reason": f"STEP1: Global Search (V:{num_violations})"}

        # AI学習・予測
        self.estimator.train(target_column=best_target)
        candidates = self.generate_candidate_points()
        mean, std = self.estimator.predict_uncertainty(candidates)
        if mean is None: return {**self.get_sobol_point(num_samples), "reason": "Fallback (Error)"}

        # --- フェーズ移行判定 ---
        if self.current_phase == "STEP1":
            self.current_phase = "STEP2"

        if self.current_phase == "STEP2":
            self.step2_exploration_count += 1
            is_stable, shift_rate = self._evaluate_boundary_stability()
            
            if len(self.stability_history) >= self.STABILITY_HISTORY_LENGTH:
                print(f"[Strategist] STEP2 | 探索回数: {self.step2_exploration_count}/{self.STEP2_MAX_EXPLORATION} | 境界反転率: {shift_rate*100:.2f}% (安定条件: {self.stability_streak}/{self.STABILITY_REQUIRED_STREAK})")
            else:
                print(f"[Strategist] STEP2 | 探索回数: {self.step2_exploration_count}/{self.STEP2_MAX_EXPLORATION} | 定点観測データ収集中 ({len(self.stability_history)}/{self.STABILITY_HISTORY_LENGTH})")
            
            if is_stable or self.step2_exploration_count >= self.STEP2_MAX_EXPLORATION:
                print("\n[Strategist] ✨ STEP3へ移行完了。マージンの不確実性潰しを開始します。✨\n")
                self.current_phase = "STEP3"

        # --- 次のターゲットの選択 ---
        m_low, m_high = self.MARGIN_RANGE
        margin_idx = np.where((mean >= m_low) & (mean <= m_high))[0]

        if self.current_phase == "STEP3":
            if len(margin_idx) > 0:
                max_std = np.max(std[margin_idx])
                print(f"[Strategist] STEP3 | マージン候補数: {len(margin_idx)} | 最大不確実性 σ = {max_std:.4f} (目標 < {self.MARGIN_MAX_UNCERTAINTY})")
                
                if max_std < self.MARGIN_MAX_UNCERTAINTY:
                    self._print_final_report(num_samples, best_target, "マージン領域の死角を完全に排除しました")
                    return {"system_command": "stop", "reason": "Target Area Verification Complete"}
                
                best_idx = margin_idx[np.argmax(std[margin_idx])]
                reason = f"STEP3: Margin Cleanup (σ={max_std:.4f})"
            else:
                print(f"[Strategist] STEP3 | マージン領域に該当する候補点がありません。バックアップ探索を実施します。")
                best_idx = np.argmax(std)
                reason = f"STEP3: Backup Search (M:{mean[best_idx]:.2f})"
        else:
            dice = np.random.rand()
            if dice < 0.3:
                best_idx = np.argmax(std)
                reason = "STEP2: Exploration (Max σ)"
            else:
                best_idx = np.argmin(np.abs(mean - 0.5))
                reason = "STEP2: Boundary 0.5"
        
        if num_samples >= self.MAX_SAMPLES:
            return {"system_command": "stop", "reason": "Max Samples Reached"}

        best_point = candidates[best_idx]
        result = {name: best_point[i] for i, name in enumerate(self.param_names)}
        result["reason"] = "[FOCUS] " + reason if self.FOCUS_POINTS else reason
        
        return result

    def _print_final_report(self, num_samples, target, reason):
        print("\n" + "="*60 + f"\n🎉 [検証完了] {reason}\n" + "="*60)
        print(f"・総実行回数: {num_samples}回\n・主要ターゲット: {target}")
        print("="*60 + "\n")

    def generate_candidate_points(self, num=None):
        num_points = num if num is not None else self.num_candidates
        if not self.FOCUS_POINTS:
            # 従来の全体探索モード (一様分布)
            cols = [np.random.uniform(self.config.PARAM_RANGES[n][0], self.config.PARAM_RANGES[n][1], num_points) for n in self.param_names]
            return np.column_stack(cols)
        else:
            # 集中探索(Focus)モード: 指定されたポイントの周辺に正規分布で生成
            cols = []
            num_per_point = num_points // len(self.FOCUS_POINTS)
            
            for name in self.param_names:
                param_range = self.config.PARAM_RANGES[name][1] - self.config.PARAM_RANGES[name][0]
                std_dev = param_range * self.FOCUS_NOISE  # パラメータの幅に応じた標準偏差
                
                param_candidates = []
                for point in self.FOCUS_POINTS:
                    # 指定ポイントに該当のパラメータが無ければ範囲の中央を基準にする
                    center = point.get(name, sum(self.config.PARAM_RANGES[name])/2.0)
                    samples = np.random.normal(loc=center, scale=std_dev, size=num_per_point)
                    param_candidates.extend(samples)
                
                # 端数合わせ
                while len(param_candidates) < num_points:
                    param_candidates.append(np.random.uniform(self.config.PARAM_RANGES[name][0], self.config.PARAM_RANGES[name][1]))
                    
                # 定義された範囲外にはみ出た値をクリップ（制限）する
                clipped = np.clip(param_candidates[:num_points], self.config.PARAM_RANGES[name][0], self.config.PARAM_RANGES[name][1])
                cols.append(clipped)
                
            return np.column_stack(cols)
