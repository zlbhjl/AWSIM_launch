import json
import numpy as np
import pandas as pd

class AWKinematicsExtractorPhase1:
    def __init__(self, mode="cvm", target_npcs=None):
        self.mode = mode
        self.target_npcs = [npc.lower() for npc in target_npcs] if target_npcs else ["npc1"]
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
        
        # Egoのサイズとオフセットを取得（Maudeの仕様再現のため "ego" キーに完全決め打ち）
        # ※ AWSIMの出力名が "ego" 以外の場合、オフセットは適用されません。詳細はREADMEを参照。
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
            
            raw_z = float(rot.get("z", 0.0))
            # 開発者の回答により、JSONの角度は Degrees(度数) であることが確定。
            # NumPyの三角関数で扱うため、モードに関わらず常にラジアンに変換する。
            yaw_rad = np.radians(raw_z)
            
            v_local_x = float(lin.get("x", 0.0))
            v_local_y = float(lin.get("y", 0.0))
            
            # 角速度(yaw_rate)もDegrees(度/秒)で記録されているため、
            # CTRVモードでの計算(yaw + omega * t)が破綻しないようラジアン/秒に変換する
            yaw_rate = np.radians(float(twist.get("angular", {}).get("z", 0.0)))

            if self.mode == "maude":
                # 旧Maudeの仕様再現: 開発者の回答の通り、意図的な1D簡略化(X軸スライド)を再現
                v_global_x = v_local_x
                v_global_y = v_local_y
            else:
                # cvm/ctrvモード: Uターン等に対応するため、正確な2Dグローバルベクトルに変換
                v_global_x = v_local_x * np.cos(yaw_rad) - v_local_y * np.sin(yaw_rad)
                v_global_y = v_local_x * np.sin(yaw_rad) + v_local_y * np.cos(yaw_rad)

            records.append({
                "timestamp": float(t),
                "ego_x": float(pos.get("x", 0.0)),
                "ego_y": float(pos.get("y", 0.0)),
                "ego_yaw": yaw_rad, 
                "ego_vx": v_global_x,
                "ego_vy": v_global_y,
                "ego_yaw_rate": yaw_rate,
                "ego_length": ego_length,
                "ego_width": ego_width,
                "ego_offset_x": ego_offset_x,
                "ego_offset_y": ego_offset_y
            })
            
        df = pd.DataFrame(records)
        return df.sort_values("timestamp") if not df.empty else df

    def extract_npc_data(self, json_data, size_info):
        """他車(NPC)の真値(GroundTruth)データを抽出し、DataFrame化する"""
        records = []
        # Maudeの ttc() 判定に合わせて groundtruth_kinematic をベースにする
        frames = json_data.get("groundtruth_kinematic", [])
        
        for frame in frames:
            t = frame.get("timestamp")
            if t is None: continue
            
            npcs = frame.get("groundtruth_vehicles", [])
            for npc in npcs:
                obj_id = npc.get("name", "unknown")
                
                # Configで指定された評価対象のNPCのみを抽出し、それ以外（混入したego等）は除外する
                if obj_id.lower() not in self.target_npcs:
                    continue
                    
                pose = npc.get("pose", {})
                pos = pose.get("position", {})
                rot = pose.get("rotation", {})
                twist = npc.get("twist", {})
                lin = twist.get("linear", {})
                
                # サイズとオフセットを取得（欠損時はデフォルト値）
                npc_info = size_info.get(obj_id, {})
                len_raw = npc_info.get("length", 0.0)
                wid_raw = npc_info.get("width", 0.0)
                length = len_raw if len_raw > 0.0 else self.DEFAULT_NPC_LENGTH
                width = wid_raw if wid_raw > 0.0 else self.DEFAULT_NPC_WIDTH
                offset_x = float(npc_info.get("offset_x", 0.0))
                offset_y = float(npc_info.get("offset_y", 0.0))
                
                if length <= 0.0: length = self.DEFAULT_NPC_LENGTH
                if width <= 0.0: width = self.DEFAULT_NPC_WIDTH
                
                raw_z = float(rot.get("z", 0.0))
                yaw_rad = np.radians(raw_z)
                
                v_local_x = float(lin.get("x", 0.0))
                v_local_y = float(lin.get("y", 0.0))
                
                # こちらも同様にラジアン/秒に変換
                yaw_rate = np.radians(float(twist.get("angular", {}).get("z", 0.0)))

                if self.mode == "maude":
                    # 旧Maudeの仕様再現: 意図的な1D簡略化(X軸スライド)を再現
                    v_global_x = v_local_x
                    v_global_y = v_local_y
                else:
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
                    "npc_yaw_rate": yaw_rate,
                    "npc_length": length,
                    "npc_width": width,
                    "npc_offset_x": offset_x,
                    "npc_offset_y": offset_y
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
            df_npc = self.extract_npc_data(data, size_info)
            
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
            
            if self.mode == "maude":
                # Maude と同じ 0.1秒刻みのサンプリングを適用
                return self._downsample_to_maude_rate(df_aligned)
            else:
                # デフォルト: サンプリングせず、全フレームの細かいログをそのまま検査
                return df_aligned
            
        except Exception as e:
            print(f"[Error] Phase1 Failed on {filepath}: {e}")
            return pd.DataFrame()

    def _downsample_to_maude_rate(self, df: pd.DataFrame, time_step=0.1) -> pd.DataFrame:
        """Maude (aw_checkerpy.py) と同じ 0.1 秒刻みのサンプリングをエミュレートする"""
        if df.empty:
            return df
            
        # aw_checkerpy.py の start_time / end_time の計算ロジックを再現
        start_time = round(df['timestamp'].iloc[0] + 0.05, 1)
        end_time = round(df['timestamp'].iloc[-1] - 0.05, 1)
        
        target_timestamps = np.arange(start_time, end_time + 1e-5, time_step)
        
        sampled_indices = []
        for t in target_timestamps:
            diffs = np.abs(df['timestamp'] - t)
            min_diff = diffs.min()
            if min_diff <= (time_step / 2):
                sampled_indices.append(diffs.idxmin())
                
        # 順番を保ちつつ重複を削除して間引かれた DataFrame を返す
        sampled_indices = sorted(list(set(sampled_indices)))
        return df.loc[sampled_indices].reset_index(drop=True)

# 実行テスト用
if __name__ == "__main__":
    extractor = AWKinematicsExtractorPhase1()
    # df_result = extractor.process_file("uturn_eval_sim4.json")
    # print(df_result.head())