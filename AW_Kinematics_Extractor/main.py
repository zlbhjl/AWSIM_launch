import os
import glob
import sys
import pandas as pd
from phase1_parser import AWKinematicsExtractorPhase1  # (Phase1のクラス名をインポート)
from phase2_geometry import GeometryBuilder
from phase3_ttc_sim import TTCSimulator
from phase4_exporter import ResultExporter

class AWKinematicsPipeline:
    """
    AW-Kinematics-Extractor全体の処理をカプセル化するインターフェース。
    ファイル出力を介さずに、計算結果を直接メモリ上で取得・受け渡しするためのクラスです。
    """
    def __init__(self):
        self.parser = AWKinematicsExtractorPhase1()
        self.geom_builder = GeometryBuilder()
        self.ttc_sim = TTCSimulator()

    def run_extraction(self, filepath: str) -> pd.DataFrame:
        """ログファイルから運動学特徴量(TTC等)を抽出し、DataFrameとして返す"""
        df_aligned = self.parser.process_file(filepath)
        if df_aligned.empty:
            return pd.DataFrame()
            
        df_with_boxes = self.geom_builder.calculate_bounding_boxes(df_aligned)
        df_result = self.ttc_sim.calculate_ttc(df_with_boxes)
        return df_result

def process_single_log(filepath: str, output_dir: str):
    print(f"[{os.path.basename(filepath)}] Processing started...")

    # パイプラインを実行して結果データを取得
    pipeline = AWKinematicsPipeline()
    df_result = pipeline.run_extraction(filepath)
    
    if df_result.empty:
        print(f"[{os.path.basename(filepath)}] No valid NPC data found. Skipped.")
        return

    # [Phase 4] 結果を外部ツール向けにCSV/JSONで出力
    exporter = ResultExporter(output_dir=output_dir)
    csv_path = exporter.export_time_series_csv(df_result, filepath)
    json_path = exporter.export_summary_json(df_result, filepath)

    print(f"[{os.path.basename(filepath)}] Success! TTC Data exported.")
    # print(f"  -> CSV: {csv_path}\n  -> JSON: {json_path}")

def batch_process(input_dir: str, output_dir: str):
    """ディレクトリ内の全JSONファイルを一括処理する"""
    target_files = glob.glob(os.path.join(input_dir, "*.json"))
    print(f"Found {len(target_files)} simulation logs.")
    
    for filepath in target_files:
        process_single_log(filepath, output_dir)

if __name__ == "__main__":
    # --- 実行設定 ---
    INPUT_DIRECTORY = "./input_logs"     # AWSIMの生JSONが入っているフォルダ
    OUTPUT_DIRECTORY = "./output_results" # 結果を出力するフォルダ
    
    # コマンドライン引数が渡された場合はそれを処理する
    if len(sys.argv) > 1:
        target_file = sys.argv[1]
        process_single_log(target_file, OUTPUT_DIRECTORY)
    else:
        print("実行時に処理対象のJSONファイルを引数に指定してください。")
        print("例: python3 main.py uturn_eval_sim4.json")
        
        # 本番用の1000ファイル一括バッチ実行
        # batch_process(INPUT_DIRECTORY, OUTPUT_DIRECTORY)