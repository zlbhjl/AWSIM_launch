import json
import numpy as np
import pandas as pd

class AWKinematicsExtractorPhase1:
    def __init__(self):
        # Maude (kinematic.maude / machine.maude) に準拠した定数
        self.VEHICLE_CLASS_MIN = 0
        self.VEHICLE_CLASS_MAX = 6
        self.CLASSIFICATION_THRESHOLD = 0.1  # classificationの確率閾値 (Maude準拠)
        self.TIME_TOLERANCE_SEC = 0.05       # タイムアライメント許容誤差 (50ms)
        
        # 欠損時のデフォルト車両サイズ (gtSizes相当)
        self.DEFAULT_EGO_LENGTH = 4.8
        self.DEFAULT_EGO_WIDTH = 1.8
        self.DEFAULT_NPC_LENGTH = 4.5
        self.DEFAULT_NPC_WIDTH = 1.8

    def _parse_sizes_and_offsets(self, json_data):
        """JSON内の groundtruth_size から車両サイズとオフセット(gtCenters)を抽出する"""
        size_info = {}
        sizes_list = json_data.get("groundtruth_size", {}).get("vehicle_sizes", [])
        for entry in sizes_list:
            name = entry.get("name")
            if not name: continue
            size = entry.get("size", {})
            center = entry.get("center", {})
            size_info[name] = {
                "length": float(size.get("x", 0.0)),
                "width": float(size.get("y", 0.0)),
                "offset_x": float(center.get("x", 0.0)),
                "offset_y": float(center.get("y", 0.0))
            }
        return size_info

    def extract_ego_data(self, json_data, size_info):
        """自車(Ego)のデータを抽出し、DataFrame化する"""
        records = []
        # aw_checkerpy.py の構造に合わせてキーを groundtruth_kinematic に変更
        frames = json_data.get("groundtruth_kinematic", [])
        
        # Egoのサイズとオフセットを取得（欠損時はデフォルト値）
        # .get() でキーが完全に存在しない場合の None 対策を強化
        ego_info = size_info.get("ego", {})
        len_raw = ego_info.get("length", 0.0)
        wid_raw = ego_info.get("width", 0.0)
        ego_length = len_raw if len_raw > 0.0 else self.DEFAULT_EGO_LENGTH
        ego_width = wid_raw if wid_raw > 0.0 else self.DEFAULT_EGO_WIDTH
        ego_offset_x = float(ego_info.get("offset_x", 0.0))
        ego_offset_y = float(ego_info.get("offset_y", 0.0))

        for frame in frames:
            t = frame.get("timestamp")
            if t is None: continue
            
            # .get() でキー欠損による KeyError を防ぐ（Defensive Extraction）
            pose = frame.get("groundtruth_ego", {}).get("pose", {})
            pos = pose.get("position", {})
            rot = pose.get("rotation", {})
            twist = frame.get("groundtruth_ego", {}).get("twist", {})
            lin = twist.get("linear", {})
            
            # 位置と角度
            yaw_rad = np.radians(float(rot.get("z", 0.0)))
            
            # 【重要】速度がローカル座標系である場合を考慮し、グローバル座標系(Map)の速度に変換する
            v_local_x = float(lin.get("x", 0.0))
            v_local_y = float(lin.get("y", 0.0))
            v_global_x = v_local_x * np.cos(yaw_rad) - v_local_y * np.sin(yaw_rad)
            v_global_y = v_local_x * np.sin(yaw_rad) + v_local_y * np.cos(yaw_rad)

            records.append({
                "timestamp": float(t),
                "ego_x": float(pos.get("x", 0.0)),
                "ego_y": float(pos.get("y", 0.0)),
                # READMEの要件通り、度数法 (Degrees) から ラジアン (Radians) に変換
                "ego_yaw": yaw_rad, 
                "ego_vx": v_global_x,
                "ego_vy": v_global_y,
                "ego_length": ego_length,
                "ego_width": ego_width,
                "ego_offset_x": ego_offset_x,
                "ego_offset_y": ego_offset_y
            })
            
        df = pd.DataFrame(records)
        return df.sort_values("timestamp") if not df.empty else df

    def extract_npc_data(self, json_data):
        """他車(NPC)の認識データを抽出し、フィルタリングしてDataFrame化する"""
        records = []
        # aw_checkerpy.py に合わせ perception_objects をベースにする
        frames = json_data.get("perception_objects", [])
        
        for frame in frames:
            t = frame.get("timestamp")
            if t is None: continue
            
            objs = frame.get("objects", [])
            for obj in objs:
                prob = obj.get("existence_prob", 0.0)
                
                # classification がリストである可能性を考慮して先頭要素を取得
                classes = obj.get("classification", [])
                cls_id = 0
                cls_prob = 0.0
                if isinstance(classes, list) and len(classes) > 0:
                    # "label"キーがあればそれを、なければ"id"を取得
                    cls_id = classes[0].get("label", classes[0].get("id", 0))
                    cls_prob = float(classes[0].get("probability", 0.0))
                elif isinstance(classes, dict):
                    cls_id = classes.get("label", classes.get("id", 0))
                    cls_prob = float(classes.get("probability", 0.0))
                
                # サニティチェック: 車両クラス(1〜6) ＆ 確率閾値以上のみを通過
                if not (self.VEHICLE_CLASS_MIN <= cls_id <= self.VEHICLE_CLASS_MAX):
                    continue
                if cls_prob < self.CLASSIFICATION_THRESHOLD:
                    continue
                    
                obj_id = obj.get("id", "unknown")
                pose = obj.get("pose", {})
                pos = pose.get("position", {})
                rot = pose.get("rotation", {})
                lin = obj.get("twist", {}).get("linear", {})
                dims = obj.get("shape", {}).get("size", {})
                
                # 寸法のサニティチェック（異常値はデフォルトで上書き）
                length = float(dims.get("x", 0.0))
                width = float(dims.get("y", 0.0))
                if length <= 0.0: length = self.DEFAULT_NPC_LENGTH
                if width <= 0.0: width = self.DEFAULT_NPC_WIDTH
                
                # 位置と角度
                yaw_rad = np.radians(float(rot.get("z", 0.0)))
                
                # NPC速度のグローバル座標系への変換
                v_local_x = float(lin.get("x", 0.0))
                v_local_y = float(lin.get("y", 0.0))
                v_global_x = v_local_x * np.cos(yaw_rad) - v_local_y * np.sin(yaw_rad)
                v_global_y = v_local_x * np.sin(yaw_rad) + v_local_y * np.cos(yaw_rad)

                records.append({
                    "timestamp": float(t),
                    "npc_id": str(obj_id),
                    "npc_x": float(pos.get("x", 0.0)),
                    "npc_y": float(pos.get("y", 0.0)),
                    "npc_yaw": yaw_rad,
                    "npc_vx": v_global_x,
                    "npc_vy": v_global_y,
                    "npc_length": length,
                    "npc_width": width,
                    "npc_offset_x": 0.0, # 認識オブジェクトは通常バウンディングボックス中心
                    "npc_offset_y": 0.0
                })
                
        df = pd.DataFrame(records)
        return df.sort_values("timestamp") if not df.empty else df

    def process_file(self, filepath):
        """1つのJSONを読み込み、時間同期されたフラットなDataFrameを返す"""
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
                
            size_info = self._parse_sizes_and_offsets(data)
            df_ego = self.extract_ego_data(data, size_info)
            df_npc = self.extract_npc_data(data)
            
            if df_ego.empty or df_npc.empty:
                return pd.DataFrame() # 比較対象がいない場合は空を返す
                
            # タイムアライメント: DataFrameのフラット化のため、各NPCの認識時刻に対して最も近い自車(Ego)の状態を紐付ける
            # tolerance(許容誤差50ms)を超えるものはロストとして結合しない
            df_aligned = pd.merge_asof(
                df_npc,           # 左側 (N行)
                df_ego,           # 右側 (1行)
                on="timestamp", 
                tolerance=self.TIME_TOLERANCE_SEC,
                direction="nearest"
            )
            
            # Egoデータが紐付かなかった（許容時間を超えた）NPCの行を削除
            df_aligned = df_aligned.dropna(subset=["ego_x"]).reset_index(drop=True)
            
            return df_aligned
            
        except Exception as e:
            print(f"[Error] Phase1 Failed on {filepath}: {e}")
            return pd.DataFrame()

# 実行テスト用
if __name__ == "__main__":
    extractor = AWKinematicsExtractorPhase1()
    # df_result = extractor.process_file("uturn_eval_sim4.json")
    # print(df_result.head())