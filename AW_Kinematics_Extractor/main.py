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
    def __init__(self, mode="cvm", target_npcs=None):
        self.mode = mode
        self.target_npcs = target_npcs or ["npc1"]
        self.parser = AWKinematicsExtractorPhase1(mode=mode, target_npcs=self.target_npcs)
        self.geom_builder = GeometryBuilder(mode=mode)
        self.ttc_sim = TTCSimulator(mode=mode)

    def run_extraction(self, filepath: str) -> pd.DataFrame:
        """ログファイルから運動学特徴量(TTC等)を抽出し、DataFrameとして返す"""
        df_aligned = self.parser.process_file(filepath)
        if df_aligned.empty:
            return pd.DataFrame()
            
        df_with_boxes = self.geom_builder.calculate_bounding_boxes(df_aligned)
        # [追加] バウンディングボックスを使ってフレームごとの最短距離を計算
        df_with_distances = self.geom_builder.calculate_distances(df_with_boxes)
        
        df_result = self.ttc_sim.calculate_ttc(df_with_distances)
        return df_result

    def get_metrics(self, filepath: str) -> dict:
        """ファイル出力を行わず、最小TTCと最小接近距離を直接計算して返す高速モード"""
        try:
            df_result = self.run_extraction(filepath)
            
            if df_result.empty:
                # Maudeの [owise] ルールを踏襲: 計算不可時は常に安全(TTC=inf, 衝突=0)とする
                return {
                    "min_ttc": float('inf'),
                    "min_distance": float('inf'),
                    "c_collision": 0
                }
                
            min_ttc = float(df_result['ttc'].min()) if 'ttc' in df_result.columns else float('inf')
            min_distance = float(df_result['distance'].min()) if 'distance' in df_result.columns else float('inf')
            
            c_collision = 1 if (min_ttc <= 0.01 or min_distance <= 0.05) else 0
            
            return {
                "min_ttc": min_ttc,
                "min_distance": min_distance,
                "c_collision": c_collision
            }
        except Exception as e:
            # 予期せぬパースエラー発生時もフェイルセーフとして [owise] の結果を返す
            print(f"[Warning] Extraction failed for {filepath}: {e}")
            return {
                "min_ttc": float('inf'),
                "min_distance": float('inf'),
                "c_collision": 0
            }

def process_single_log(filepath: str, output_dir: str, mode="cvm"):
    print(f"[{os.path.basename(filepath)}] Processing started...")

    # パイプラインを実行して結果データを取得
    pipeline = AWKinematicsPipeline(mode=mode)
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
    argv_copy = sys.argv[:]
    run_mode = "cvm"
    
    if "--mode" in argv_copy:
        idx = argv_copy.index("--mode")
        if idx + 1 < len(argv_copy):
            run_mode = argv_copy[idx + 1]
            argv_copy.pop(idx)
            argv_copy.pop(idx)

    if len(argv_copy) > 1:
        if "--min-ttc-only" in argv_copy:
            # 余分な引数を除外してファイル名だけを取得
            target_file = [arg for arg in argv_copy if arg not in ["--min-ttc-only", argv_copy[0]]][0]
            pipeline = AWKinematicsPipeline(mode=run_mode)
            print(pipeline.get_metrics(target_file).get("min_ttc", float('inf')))
        else:
            target_file = argv_copy[1]
            process_single_log(target_file, OUTPUT_DIRECTORY, mode=run_mode)
    else:
        print("実行時に処理対象のJSONファイルを引数に指定してください。")
        print("例: python3 main.py uturn_eval_sim4.json")
        print("最小TTCのみを出力する場合は: python3 main.py uturn_eval_sim4.json --min-ttc-only")
        print("Maude互換モードで実行する場合は: python3 main.py uturn_eval_sim4.json --mode maude")
        
        # 本番用の1000ファイル一括バッチ実行
        # batch_process(INPUT_DIRECTORY, OUTPUT_DIRECTORY)