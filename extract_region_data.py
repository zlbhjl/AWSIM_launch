#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import pandas as pd
import json
import os
import sys
import importlib
import point_extractors

def main():
    parser = argparse.ArgumentParser(description="データセットから特定の領域(Bounds)や条件のデータのみを抽出して出力します。")
    parser.add_argument("--type", type=str, default="uturn", help="シナリオタイプ (例: uturn)")
    parser.add_argument("--dir", type=str, default="~/simulation_traces", help="データセットが存在するディレクトリ")
    parser.add_argument("--bounds", type=str, default=None, help="手動で領域を指定するJSON (例: '{\"dx0\": [15.0, 20.0]}')")
    parser.add_argument("--region", type=str, default="custom", help="抽出条件式 (例: 'emp_safe and jama_safe', 'min_ttc > 1.0 or c_collision == 1')")
    parser.add_argument("--output", type=str, default="extracted_dataset.csv", help="出力するCSVのファイル名")
    
    args = parser.parse_args()
    
    traces_dir = os.path.expanduser(args.dir)
    dataset_file = os.path.join(traces_dir, f"{args.type}_dataset.csv")
    
    if not os.path.exists(dataset_file):
        print(f"[エラー] データセットが見つかりません: {dataset_file}")
        sys.exit(1)
        
    print(f"データセットを読み込み中: {dataset_file}")
    df = pd.read_csv(dataset_file, engine='python', on_bad_lines='skip')
    
    # 数値列として扱うカラムを変換
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors='ignore')

    initial_count = len(df)
    bounds_dict = None
    
    if args.bounds:
        try:
            bounds_dict = json.loads(args.bounds)
        except json.JSONDecodeError as e:
            print(f"[エラー] --bounds 引数のJSONパースに失敗しました: {e}")
            sys.exit(1)
            
    try:
        df = point_extractors.filter_by_region_and_bounds(df, region=args.region, bounds=bounds_dict)
        print(f"抽出条件を適用しました: {len(df)} / {initial_count} 行")
    except Exception as e:
        print(f"[エラー] {e}")
        sys.exit(1)

    if df.empty:
        print("[警告] 指定された条件に合致するデータが1件もありませんでした。ファイルは作成されません。")
    else:
        print("\n=== 抽出データ サマリー ===")
        print(f"データ件数: {len(df)} 件")
        
        if 'c_collision' in df.columns:
            col_count = (df['c_collision'] == 1).sum()
            print(f"衝突回数  : {col_count} 件 ({col_count/len(df)*100:.1f}%)")
            
        if 'min_ttc' in df.columns:
            valid_ttc = df[df['min_ttc'] >= 0]
            if not valid_ttc.empty:
                print(f"平均 TTC  : {valid_ttc['min_ttc'].mean():.3f} 秒")
                print(f"最小 TTC  : {valid_ttc['min_ttc'].min():.3f} 秒")
        print("===========================\n")

        out_path = os.path.join(traces_dir, args.output)
        point_extractors.save_dataframe_to_csv(df, out_path, f"[成功] 抽出したデータを保存しました: {out_path}")

if __name__ == "__main__":
    main()