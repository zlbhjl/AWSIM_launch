import numpy as np
import pandas as pd

class GeometryBuilder:
    def __init__(self):
        # このクラスは状態を持たなくなりました。
        # オフセットとサイズは、Phase 1でDataFrameに列として追加されることを前提とします。
        # これにより、このクラスはステートレスな計算ヘルパーとして機能します。
        pass

    def calculate_bounding_boxes(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        全フレームの自車・他車の4頂点座標を一括で計算する（NumPyベクトル演算）
        """
        if df.empty:
            return df
            
        # Phase 1でオフセットとサイズの列が追加されていることを前提とする
        required_cols = [
            'ego_offset_x', 'ego_offset_y', 'npc_offset_x', 'npc_offset_y'
        ]
        if not all(col in df.columns for col in required_cols):
            raise ValueError(f"DataFrame is missing required offset columns. Found: {df.columns.tolist()}")

        df = df.copy()

        # 1. 自車 (Ego) の4頂点計算
        ego_boxes = self._compute_rotated_corners(
            x=df['ego_x'].values,
            y=df['ego_y'].values,
            yaw=df['ego_yaw'].values,
            length=df['ego_length'].values,
            width=df['ego_width'].values,
            offset_x=df['ego_offset_x'].values,
            offset_y=df['ego_offset_y'].values
        )
        # DataFrameの新しい列として追加 (shape: [N, 4, 2])
        df['ego_box'] = list(ego_boxes)

        # 2. 他車 (NPC) の4頂点計算
        npc_boxes = self._compute_rotated_corners(
            x=df['npc_x'].values,
            y=df['npc_y'].values,
            yaw=df['npc_yaw'].values,
            length=df['npc_length'].values,
            width=df['npc_width'].values,
            offset_x=df['npc_offset_x'].values,
            offset_y=df['npc_offset_y'].values
        )
        df['npc_box'] = list(npc_boxes)

        return df

    def _compute_rotated_corners(self, x, y, yaw, length, width, offset_x, offset_y):
        """
        ベクトル化された回転矩形の計算ロジック (forループなしで全行を瞬時に計算)
        戻り値: shape(N, 4, 2) の三次元NumPy配列 [データ数, 4つの角, X/Y座標]
        """
        # JSONから抽出された角度は度数法(Degree)のため、ラジアンに変換する
        yaw = np.deg2rad(yaw)

        # Maudeの `frontLeftPoint`, `backRightPoint` 等のロジックの完全再現
        
        # 車両の中心座標を計算 (オフセットを足す)
        cx = x + offset_x * np.cos(yaw) - offset_y * np.sin(yaw)
        cy = y + offset_x * np.sin(yaw) + offset_y * np.cos(yaw)

        # 中心からの距離（縦・横の半分）
        dx = length / 2.0
        dy = width / 2.0

        # 回転前の4頂点座標 (ローカル座標系)
        # [Front-Left, Front-Right, Back-Right, Back-Left]
        corners_local = np.stack([
            np.stack([ dx,  dy], axis=-1),
            np.stack([ dx, -dy], axis=-1),
            np.stack([-dx, -dy], axis=-1),
            np.stack([-dx,  dy], axis=-1)
        ], axis=1) # shape: (N, 4, 2)

        # 回転行列の準備 (全フレーム分を一括生成)
        cos_y = np.cos(yaw)
        sin_y = np.sin(yaw)
        
        # rotation_matrices の shape: (N, 2, 2)
        # Nはデータ行数。各行に対して2x2の回転行列を作る
        rotation_matrices = np.stack([
            np.stack([cos_y, -sin_y], axis=-1),
            np.stack([sin_y,  cos_y], axis=-1)
        ], axis=-2)

        # 行列の掛け算で一気に回転させる: (N, 2, 2) x (N, 4, 2)^T -> (N, 4, 2)
        rotated_corners = np.einsum('nij,nkj->nki', rotation_matrices, corners_local)

        # 車両の中心座標(cx, cy)を全頂点に足す (グローバル座標系への変換)
        centers = np.stack([cx, cy], axis=-1)[:, np.newaxis, :] # shape: (N, 1, 2)
        final_boxes = rotated_corners + centers

        return final_boxes