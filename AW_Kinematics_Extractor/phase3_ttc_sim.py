import numpy as np
import pandas as pd

class TTCSimulator:
    def __init__(self, mode="cvm"):
        self.mode = mode
        # Maude (machine.maude) の定数定義に準拠
        self.TTC_BOUND = 5.0  # 予測上限時間 (秒)
        self.DT = 0.1         # 予測の時間刻み (秒)

    def _compute_rotated_corners(self, cx, cy, yaw, length, width):
        """
        中心座標とYaw角から4つの頂点を再計算するヘルパー（CTRV用）
        """
        dx = length / 2.0
        dy = width / 2.0
        corners_local = np.stack([
            np.stack([ dx,  dy], axis=-1),
            np.stack([ dx, -dy], axis=-1),
            np.stack([-dx, -dy], axis=-1),
            np.stack([-dx,  dy], axis=-1)
        ], axis=1)

        cos_y = np.cos(yaw)
        sin_y = np.sin(yaw)
        rotation_matrices = np.stack([
            np.stack([cos_y, -sin_y], axis=-1),
            np.stack([sin_y,  cos_y], axis=-1)
        ], axis=-2)

        rotated_corners = np.einsum('nij,nkj->nki', rotation_matrices, corners_local)
        centers = np.stack([cx, cy], axis=-1)[:, np.newaxis, :]
        return rotated_corners + centers

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

        # CTRV用の情報（中心座標、初期Yaw、Yaw rate、サイズ）
        if self.mode == "ctrv":
            ego_cx = df['ego_x'].values + df['ego_offset_x'].values * np.cos(df['ego_yaw'].values) - df['ego_offset_y'].values * np.sin(df['ego_yaw'].values)
            ego_cy = df['ego_y'].values + df['ego_offset_x'].values * np.sin(df['ego_yaw'].values) + df['ego_offset_y'].values * np.cos(df['ego_yaw'].values)
            npc_cx = df['npc_x'].values + df['npc_offset_x'].values * np.cos(df['npc_yaw'].values) - df['npc_offset_y'].values * np.sin(df['npc_yaw'].values)
            npc_cy = df['npc_y'].values + df['npc_offset_x'].values * np.sin(df['npc_yaw'].values) + df['npc_offset_y'].values * np.cos(df['npc_yaw'].values)
            
            ego_yaw_init = df['ego_yaw'].values
            npc_yaw_init = df['npc_yaw'].values
            ego_yaw_rate = df['ego_yaw_rate'].values
            npc_yaw_rate = df['npc_yaw_rate'].values
            
            ego_size = (df['ego_length'].values, df['ego_width'].values)
            npc_size = (df['npc_length'].values, df['npc_width'].values)

        # 全行の初期TTCを「無限大 (np.inf)」で初期化 (5.0秒以内で衝突しない場合は安全とする)
        N = len(df)
        ttc_results = np.full(N, np.inf)

        # まだ衝突していない行のインデックスを管理するマスク（計算の高速化用）
        active_mask = np.ones(N, dtype=bool)

        # Maudeの `estimate-ttc` と同様に、0.0秒から5.0秒まで 0.1秒刻みでシミュレーション
        # 浮動小数点誤差を避けるため、整数インデックスから計算して丸める
        # [修正] np.arange は終端を含まないため、+1 して確実に 5.0秒目 (t=5.0) も評価させる
        num_steps = int(np.ceil(self.TTC_BOUND / self.DT)) + 1
        time_steps = np.round(np.arange(num_steps) * self.DT, 2)

        for t in time_steps:
            if not np.any(active_mask):
                break  # 全車両がすでに衝突した場合はループ終了

            if self.mode == "ctrv":
                # CTRV: 等旋回モデルによる未来位置と角度の計算
                def calc_ctrv_pos(cx, cy, yaw, yaw_rate, vel, L, W, mask):
                    # [修正] 車が向いている方向への速度成分(内積)を取ることで、
                    # バック(後退)している時もマイナスの速度として正しく未来予測する
                    v_mag = vel[mask, 0] * np.cos(yaw[mask]) + vel[mask, 1] * np.sin(yaw[mask])
                    omega = yaw_rate[mask]
                    is_straight = np.abs(omega) < 1e-4
                    
                    new_yaw = yaw[mask] + omega * t
                    
                    new_cx = cx[mask] + np.where(is_straight, vel[mask, 0] * t, (v_mag / np.where(is_straight, 1.0, omega)) * (np.sin(new_yaw) - np.sin(yaw[mask])))
                    new_cy = cy[mask] + np.where(is_straight, vel[mask, 1] * t, -(v_mag / np.where(is_straight, 1.0, omega)) * (np.cos(new_yaw) - np.cos(yaw[mask])))
                    
                    # 角度が変わるため、中心座標から4つの頂点を再計算する
                    return self._compute_rotated_corners(new_cx, new_cy, new_yaw, L[mask], W[mask])

                curr_ego_boxes = calc_ctrv_pos(ego_cx, ego_cy, ego_yaw_init, ego_yaw_rate, ego_vel, ego_size[0], ego_size[1], active_mask)
                curr_npc_boxes = calc_ctrv_pos(npc_cx, npc_cy, npc_yaw_init, npc_yaw_rate, npc_vel, npc_size[0], npc_size[1], active_mask)
                
            else:
                # CVM / Maude 共通の等速直線モデル
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
        
        # --- [追加] TTB (Time-To-Brake: ブレーキ猶予時間) の計算 ---
        # TTB = TTC - 停止に必要な時間 (空走時間 + 制動時間)
        # ここではJAMAプロファイルの人間(Human)のデフォルト値を用いて概算
        v_ego = np.hypot(df['ego_vx'].values, df['ego_vy'].values)
        t_req = 0.75 + (v_ego / 7.58)  # 空走時間(0.75s) + 制動時間(速度 / 減速度)
        
        ttb_results = ttc_results - t_req
        ttb_results[ttc_results == np.inf] = np.inf  # 衝突しない場合はTTBも無限大
        df['ttb'] = ttb_results

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

        # 衝突している(True)と仮定して初期化
        collisions = np.ones(M, dtype=bool)

        # 4つの軸すべてについて、投影の重なりをチェックする
        for i in range(4):
            axis = axes[:, i, :] # (M, 2)
            
            # 各頂点 (M, 4, 2) を軸 (M, 2) に内積で投影する
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