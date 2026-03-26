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
        # 次のテスト候補として、空間内にばらまくランダムな点の数
        # 1万個の架空シナリオから、一番「怪しい」ものを1つ選び出します
        self.num_candidates = num_candidates
        self.estimator = SafetyEstimator()

    def generate_candidate_points(self):
        """設定ファイル(uturn_config)の範囲内で、架空のパラメータを大量に生成する"""
        ranges = uturn_config.PARAM_RANGES
        
        # 各パラメータの最小値と最大値を取得
        dx0_min, dx0_max = ranges["dx0"]
        ego_min, ego_max = ranges["ego_speed"]
        npc_min, npc_max = ranges["npc_speed"]

        # 乱数を使って候補点を大量に生成 (num_candidates行 × 3列 の行列)
        candidates = np.column_stack((
            np.random.uniform(dx0_min, dx0_max, self.num_candidates),
            np.random.uniform(ego_min, ego_max, self.num_candidates),
            np.random.uniform(npc_min, npc_max, self.num_candidates)
        ))
        return candidates

    def decide_next_target(self):
        """過去のデータを分析し、次に実行すべき最適なパラメータを決定する"""
        print("\n=== 司令官(Strategist)による次期作戦の立案を開始 ===")
        
        # 1. 統計学者に過去データを学習させる
        success = self.estimator.train()
        
        # もしデータがない、または「衝突(1)」か「安全(0)」のどちらか片方しか起きていない場合
        # （GPCは両方のデータがないと境界線を引けないための安全対策）
        if not success or len(np.unique(self.estimator.model.classes_)) < 2:
            print("[Strategist] ⚠️ 十分なデータがない、または境界線が引けません。")
            print("[Strategist] 👉 探索フェーズ（ランダムサンプリング）を継続します。")
            
            # ランダムに1点だけ選んで返す
            random_candidates = self.generate_candidate_points()
            best_point = random_candidates[0]
            return {
                "dx0": best_point[0],
                "ego_speed": best_point[1],
                "npc_speed": best_point[2],
                "reason": "Random Exploration (データ不足)"
            }

        # 2. 空間内に大量の架空シナリオ（候補点）をばらまく
        candidates = self.generate_candidate_points()
        print(f"[Strategist] 空間内に {self.num_candidates} 個の仮想候補点を展開しました。")
        
        # 3. 統計学者に全候補点の「不確実性」を予測させる
        print("[Strategist] 統計学者(Estimator)が各候補点の不確実性を計算中...")
        probs, uncerts = self.estimator.predict_uncertainty(candidates)
        
        # 4. 不確実性が最も高い（一番AIが迷っている）候補点を探し出す
        best_idx = np.argmax(uncerts)
        best_point = candidates[best_idx]
        best_uncert = uncerts[best_idx]
        best_prob = probs[best_idx]
        
        print(f"[Strategist] 🎯 最適な次期テストターゲットを発見しました！")
        print(f"  - ターゲット予測衝突確率: {best_prob*100:.1f}%")
        print(f"  - ターゲット不確実性    : {best_uncert:.3f} (最大1.0)")
        
        # 辞書形式で結果を返す
        return {
            "dx0": best_point[0],
            "ego_speed": best_point[1],
            "npc_speed": best_point[2],
            "reason": f"Active Learning (Uncertainty: {best_uncert:.3f})"
        }

# =========================================================
# お試し実行用コード
# =========================================================
if __name__ == "__main__":
    strategist = ActiveLearningStrategist(num_candidates=50000)
    
    # 次のターゲットを計算
    start_time = time.time()
    next_target = strategist.decide_next_target()
    elapsed = time.time() - start_time
    
    print("\n==================================================")
    print(" 📢 【司令官からの指示】次はこのパラメータで実行せよ！")
    print("==================================================")
    print(f"  - 他車トリガー距離 (dx0)      : {next_target['dx0']:.2f} m")
    print(f"  - 自車の目標速度 (ego_speed)  : {next_target['ego_speed']:.2f} km/h")
    print(f"  - 他車の目標速度 (npc_speed)  : {next_target['npc_speed']:.2f} km/h")
    print(f"  - 選定理由                    : {next_target['reason']}")
    print("==================================================")
    print(f" (計算にかかった時間: {elapsed:.2f}秒)")
