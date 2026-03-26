#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import numpy as np
import time

# 作成したモジュールと設定ファイルを読み込む
from estimator import SafetyEstimator
import uturn_config

class ActiveLearningStrategist:
    def __init__(self, num_candidates=10000):
        # AI覚醒時(101回目以降)にばらまく架空のパラメータ数
        self.num_candidates = num_candidates
        self.estimator = SafetyEstimator()

    def generate_candidate_points(self):
        """AIフェーズ用：設定ファイルの範囲内で、架空のパラメータを大量(1万個)に生成する"""
        ranges = uturn_config.PARAM_RANGES
        
        dx0_min, dx0_max = ranges["dx0"]
        ego_min, ego_max = ranges["ego_speed"]
        npc_min, npc_max = ranges["npc_speed"]

        candidates = np.column_stack((
            np.random.uniform(dx0_min, dx0_max, self.num_candidates),
            np.random.uniform(ego_min, ego_max, self.num_candidates),
            np.random.uniform(npc_min, npc_max, self.num_candidates)
        ))
        return candidates

    def get_fresh_random_point(self):
        """ランダムフェーズ用：その場で1つだけ新鮮な数値を生成する"""
        ranges = uturn_config.PARAM_RANGES
        return (
            np.random.uniform(*ranges["dx0"]),
            np.random.uniform(*ranges["ego_speed"]),
            np.random.uniform(*ranges["npc_speed"])
        )

    def decide_next_target(self):
        """過去のデータを分析し、次に実行すべき最適なパラメータを決定する"""
        print("\n=== 司令官(Strategist)による次期作戦の立案を開始 ===")
        
        # 現在のデータ数を取得
        df = self.estimator.load_and_merge_data()
        num_samples = len(df) if df is not None else 0
        
        INITIAL_EXPLORATION_LIMIT = 100 

        # ---------------------------------------------------------
        # 1. 初期土台作りフェーズ (100回まで)
        # ---------------------------------------------------------
        if num_samples < INITIAL_EXPLORATION_LIMIT:
            print(f"[Strategist] 🛠️ 初期土台作りフェーズ中 ({num_samples + 1}/{INITIAL_EXPLORATION_LIMIT})")
            print(f"[Strategist] 👉 その場で新鮮なパラメータをランダム抽選します。")
            
            # ★修正: 1万個作らず、ここで1回だけサイコロを振る
            dx0, ego, npc = self.get_fresh_random_point()
            
            return {
                "dx0": dx0,
                "ego_speed": ego,
                "npc_speed": npc,
                "reason": f"Fresh Random Sampling (Step {num_samples + 1})"
            }

        # ---------------------------------------------------------
        # 2. AI(GPC) 学習と予測フェーズ (101回目以降)
        # ---------------------------------------------------------
        success = self.estimator.train()
        
        # 万が一、100回やっても「安全」か「衝突」のどちらか片方しかない場合
        if not success or len(np.unique(self.estimator.model.classes_)) < 2:
            print("[Strategist] ⚠️ データは100件ありますが、結果が一方に偏っています（境界線が引けません）。")
            print("[Strategist] 👉 有効なデータが出るまでランダム探索を延長します。")
            
            # ★修正: 延長戦でも新鮮なサイコロを振る
            dx0, ego, npc = self.get_fresh_random_point()
            
            return {
                "dx0": dx0,
                "ego_speed": ego,
                "npc_speed": npc,
                "reason": "Extended Fresh Random (結果の偏りによる延長)"
            }

        # ---------------------------------------------------------
        # 3. Active Learning (不確実性の最も高い場所を狙う)
        # ---------------------------------------------------------
        # ここではAIの計算のために1万個の候補点を使う
        candidates = self.generate_candidate_points()
        
        print(f"[Strategist] 🧠 過去{num_samples}件のデータを分析完了。")
        print(f"[Strategist] 次の最も不確実（怪しい）なターゲットを計算中...")
        probs, uncerts = self.estimator.predict_uncertainty(candidates)
        
        # 不確実性が最も高い候補点を探し出す
        best_idx = np.argmax(uncerts)
        best_point = candidates[best_idx]
        best_uncert = uncerts[best_idx]
        best_prob = probs[best_idx]
        
        print(f"[Strategist] 🎯 精密狙い撃ちフェーズ：最適ターゲットを発見！")
        print(f"  - 予測衝突確率: {best_prob*100:.1f}%")
        print(f"  - 不確実性スコア: {best_uncert:.3f}")
        
        return {
            "dx0": best_point[0],
            "ego_speed": best_point[1],
            "npc_speed": best_point[2],
            "reason": f"Active Learning (Uncertainty: {best_uncert:.3f})"
        }
