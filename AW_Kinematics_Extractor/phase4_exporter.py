import os
import json
import numpy as np
import pandas as pd

class ResultExporter:
    def __init__(self, output_dir="output_results"):
        self.output_dir = output_dir
        # 出力先ディレクトリが存在しなければ作成
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def export_time_series_csv(self, df: pd.DataFrame, original_filename: str):
        """
        時系列データをCSVとして出力する。
        多次元配列（バウンディングボックスの頂点等）はCSVを重くするため除外し、
        外部ツールで分析しやすいフラットな数値データのみを抽出して保存する。
        """
        if df.empty or 'ttc' not in df.columns:
            return None

        # 出力用のカラムを厳選
        export_columns = [
            'timestamp', 
            'ego_x', 'ego_y', 'ego_yaw', 'ego_vx', 'ego_vy',
            'npc_id', 'npc_x', 'npc_y', 'npc_yaw', 'npc_vx', 'npc_vy',
            'ttc'
        ]
        
        # 存在するカラムのみを抽出
        valid_columns = [col for col in export_columns if col in df.columns]
        df_export = df[valid_columns].copy()

        # inf (無限大: 衝突なし) を、CSVで扱いやすいように 999.0 などの安全な数値に置換
        # ※ 次の検証ツールの仕様に合わせて変更してください
        df_export['ttc'] = df_export['ttc'].replace(np.inf, 999.0)

        # ファイル名の生成 (例: uturn_eval_sim4.json -> uturn_eval_sim4_timeseries.csv)
        base_name = os.path.splitext(os.path.basename(original_filename))[0]
        output_path = os.path.join(self.output_dir, f"{base_name}_timeseries.csv")

        df_export.to_csv(output_path, index=False)
        return output_path

    def export_summary_json(self, df: pd.DataFrame, original_filename: str):
        """
        シミュレーション全体を通してのメタデータ（最小TTCなど）を抽出し、
        SMC(統計的モデルチェッキング)の報酬関数等で使いやすいJSONとして出力する。
        """
        if df.empty or 'ttc' not in df.columns:
            return None

        # シミュレーション全体での最小TTCを検索
        min_ttc_val = df['ttc'].min()

        summary_data = {
            "source_file": original_filename,
            "total_frames": int(df['timestamp'].nunique()),
            "total_npcs_involved": int(df['npc_id'].nunique()),
            "min_ttc": float(min_ttc_val) if min_ttc_val != np.inf else "Infinity",
            "critical_moment": None
        }

        # 衝突の危険があった（TTCが無限大ではない）場合、その瞬間の詳細を記録
        if min_ttc_val != np.inf:
            # 最小TTCを記録した行を取得（複数ある場合は最初の1つ）
            critical_row = df[df['ttc'] == min_ttc_val].iloc[0]
            
            summary_data["critical_moment"] = {
                "timestamp": float(critical_row['timestamp']),
                "npc_id": str(critical_row['npc_id']),
                "ego_speed": float(np.hypot(critical_row['ego_vx'], critical_row['ego_vy'])),
                "npc_speed": float(np.hypot(critical_row['npc_vx'], critical_row['npc_vy']))
            }

        base_name = os.path.splitext(os.path.basename(original_filename))[0]
        output_path = os.path.join(self.output_dir, f"{base_name}_summary.json")

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(summary_data, f, indent=4)

        return output_path