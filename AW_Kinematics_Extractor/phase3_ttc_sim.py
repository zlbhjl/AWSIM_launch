import numpy as np
import pandas as pd

class TTCSimulator:
    def __init__(self):
        # Maude (machine.maude) の定数定義に準拠
        self.TTC_BOUND = 5.0  # 予測上限時間 (秒)
        self.DT = 0.1         # 予測の時間刻み (秒)

    def calculate_ttc(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Phase 2で生成された4頂点データと速度ベクトルを用いて、最小TTCを計算する。
        """
        if df.empty or 'ego_box' not in df.columns or 'npc_box' not in df.columns:
            return df
            
        df = df.copy()

        # DataFrameの列(リストのリスト)から、NumPyの3次元配列 (N, 4, 2) に変換
        ego_boxes_init = np.stack(df['ego_box'].values)
        npc_boxes_init = np.stack(df['npc_box'].values)

        # 速度ベクトルの抽出 (N, 2)
        ego_vel = df[['ego_vx', 'ego_vy']].values
        npc_vel = df[['npc_vx', 'npc_vy']].values

        # 全行の初期TTCを「無限大 (np.inf)」で初期化 (5.0秒以内で衝突しない場合は安全とする)
        N = len(df)
        ttc_results = np.full(N, np.inf)

        # まだ衝突していない行のインデックスを管理するマスク（計算の高速化用）
        active_mask = np.ones(N, dtype=bool)

        # Maudeの `estimate-ttc` と同様に、0.0秒から5.0秒まで 0.1秒刻みでシミュレーション
        # 浮動小数点誤差を避けるため、整数インデックスから計算して丸める
        num_steps = int(np.ceil(self.TTC_BOUND / self.DT))
        time_steps = np.round(np.arange(num_steps) * self.DT, 2)

        for t in time_steps:
            if not np.any(active_mask):
                break  # 全車両がすでに衝突した場合はループ終了

            # 【未来位置の予測 (moveVertices相当)】 
            # 位置 = 初期位置 + 速度 * 時間 (アクティブな車両のみ計算)
            curr_ego_boxes = ego_boxes_init[active_mask] + (ego_vel[active_mask, np.newaxis, :] * t)
            curr_npc_boxes = npc_boxes_init[active_mask] + (npc_vel[active_mask, np.newaxis, :] * t)

            # 【衝突判定 (collision相当)】
            # 分離軸定理 (SAT) を用いて、移動後の矩形同士が重なっているかを一括判定
            is_colliding = self._check_collision_sat(curr_ego_boxes, curr_npc_boxes)

            # 衝突が検知された行の元のインデックスを取得
            collided_indices = np.where(active_mask)[0][is_colliding]

            # TTCを記録し、衝突した車両は次ステップ以降の計算対象(active_mask)から外す
            ttc_results[collided_indices] = float(t)
            active_mask[collided_indices] = False

        # 計算結果をDataFrameに追加
        df['ttc'] = ttc_results
        
        return df

    def _check_collision_sat(self, boxes1: np.ndarray, boxes2: np.ndarray) -> np.ndarray:
        """
        分離軸定理 (Separating Axis Theorem: SAT) を用いたベクトル化衝突判定
        boxes1, boxes2: shape (M, 4, 2) の長方形頂点配列
        戻り値: shape (M,) の真偽値配列 (True: 衝突, False: 非衝突)
        """
        M = boxes1.shape[0]
        if M == 0:
            return np.array([], dtype=bool)

        # 各長方形は2つの直交する軸(法線ベクトル)を持つ。それを抽出する。
        # 辺のベクトルを計算: V1 - V0, V2 - V1
        edge1_b1 = boxes1[:, 1, :] - boxes1[:, 0, :]
        edge2_b1 = boxes1[:, 2, :] - boxes1[:, 1, :]
        edge1_b2 = boxes2[:, 1, :] - boxes2[:, 0, :]
        edge2_b2 = boxes2[:, 2, :] - boxes2[:, 1, :]

        # すべての軸をまとめる (M, 4, 2)
        axes = np.stack([edge1_b1, edge2_b1, edge1_b2, edge2_b2], axis=1)

        # ※長方形の場合、法線は辺と同じ向きで計算しても投影結果の重なり判定には影響しないため、
        # 計算量を減らすために辺ベクトルをそのまま分離軸として使用します。

        # 衝突している(True)と仮定して初期化
        collisions = np.ones(M, dtype=bool)

        # 4つの軸すべてについて、投影の重なりをチェックする
        for i in range(4):
            axis = axes[:, i, :] # (M, 2)
            
            # 各頂点 (M, 4, 2) を軸 (M, 2) に内積で投影する
            # einsum('mk,mnk->mn') は各頂点と軸の内積を一括計算
            proj1 = np.einsum('mk,mnk->mn', axis, boxes1) # (M, 4)
            proj2 = np.einsum('mk,mnk->mn', axis, boxes2) # (M, 4)

            # 投影された点の最小値と最大値を取得
            min1, max1 = np.min(proj1, axis=1), np.max(proj1, axis=1)
            min2, max2 = np.min(proj2, axis=1), np.max(proj2, axis=1)

            # どちらかの最大値がもう一方の最小値より小さければ、重なっていない (分離軸が存在する)
            separated = (max1 < min2) | (max2 < min1)

            # 分離軸が1つでも見つかった場合は、衝突していないので False にする
            collisions &= ~separated

        return collisions