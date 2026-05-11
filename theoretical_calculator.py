#!/usr/bin/env python3
# -*- coding: utf-8 -*-

class TheoreticalSafetyCalculator:
    def __init__(self, config=None):
        # configからJAMAプロファイルを読み込む。なければデフォルト値を使用。
        self.profiles = getattr(config, 'JAMA_PROFILES', None)
        if not self.profiles:
            self.profiles = {
                "human": {"t_delay": 0.75, "t_jerk": 0.6, "a_max": 7.58},
                "ai_aeb": {"t_delay": 0.1, "t_jerk": 0.1, "a_max": 8.33}
            }

    def evaluate(self, dx0: float, ego_speed: float, npc_speed: float) -> dict:
        """
        アプローチA（壁想定）とアプローチB（NPC前進考慮）の両方のマージンとゾーンを計算する
        """
        # 時速(km/h)から秒速(m/s)へ変換
        v0_ego = ego_speed / 3.6
        v_npc = npc_speed / 3.6

        def calc_profile(profile):
            t_delay = profile["t_delay"]
            t_jerk = profile["t_jerk"]
            a_max = profile["a_max"]

            # ゼロ除算防止（a_max または t_jerk が 0 に設定された場合のフェイルセーフ）
            if a_max <= 0:
                return float('inf'), float('inf') # 止まれない
            if t_jerk <= 0:
                t_jerk = 1e-5 # 極小値に丸めてゼロ除算を回避

            # 1. 空走距離と時間
            d_delay = v0_ego * t_delay

            # 2. 立ち上がり中の距離と残存速度
            v_drop_jerk = 0.5 * a_max * t_jerk
            v1 = v0_ego - v_drop_jerk
            
            if v1 < 0:
                # 立ち上がり中に完全に停止してしまう場合
                t_stop = (2 * v0_ego * t_jerk / a_max) ** 0.5
                d_jerk = v0_ego * t_stop - (1/6) * (a_max / t_jerk) * t_stop**3
                d_braking = 0.0
                t_total = t_delay + t_stop
            else:
                # 立ち上がり完了後、最大減速に移行する場合
                d_jerk = v0_ego * t_jerk - (1/6) * a_max * t_jerk**2
                # 3. 最大減速中の距離と時間
                d_braking = (v1 ** 2) / (2 * a_max)
                t_braking = v1 / a_max
                t_total = t_delay + t_jerk + t_braking

            d_total = d_delay + d_jerk + d_braking
            return d_total, t_total

        # 人間とAIの停止距離(d)と停止までにかかる時間(t)を計算
        d_human, t_human = calc_profile(self.profiles["human"])
        d_ai, t_ai = calc_profile(self.profiles["ai_aeb"])

        def determine_zone(margin_human, margin_ai):
            # margin >= 0 を「安全（停止可能）」として判定
            if margin_human >= 0 and margin_ai >= 0:
                return "A" # 両方安全
            elif margin_human < 0 and margin_ai >= 0:
                return "B" # AIのみ安全
            elif margin_human < 0 and margin_ai < 0:
                return "C" # 両方危険
            else:
                return "D" # 特殊ケース（人間のみ安全）

        # === アプローチA (壁として最悪ケースを想定) ===
        margin_a_human = dx0 - d_human
        margin_a_ai = dx0 - d_ai
        zone_a = determine_zone(margin_a_human, margin_a_ai)

        # === アプローチB (自車が止まるまでにNPCが前進するボーナス距離を考慮) ===
        margin_b_human = (dx0 + (v_npc * t_human)) - d_human
        margin_b_ai = (dx0 + (v_npc * t_ai)) - d_ai
        zone_b = determine_zone(margin_b_human, margin_b_ai)

        results = {
            "theory_d_total_human": round(d_human, 4),
            "theory_d_total_ai": round(d_ai, 4),
            "theory_margin_a_human": round(margin_a_human, 4),
            "theory_margin_a_ai": round(margin_a_ai, 4),
            "theory_zone_a": zone_a,
            "theory_margin_b_human": round(margin_b_human, 4),
            "theory_margin_b_ai": round(margin_b_ai, 4),
            "theory_zone_b": zone_b
        }

        return results