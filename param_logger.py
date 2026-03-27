#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import csv
import numpy as np

def log_parameters(output_dir: str, file_name: str, loop_num: int, params_dict: dict, reason: str = ""):
    """
    シミュレーションのパラメータをCSVに記録する汎用モジュール。
    AIの選定理由（reason）も記録できるように拡張しました。
    """
    os.makedirs(output_dir, exist_ok=True)
    log_file = os.path.join(output_dir, file_name)
    
    file_exists = os.path.exists(log_file)
    
    # --- [修正箇所] ヘッダーの重複エラー防止 ---
    # params_dict 内に万が一 'reason' というキーが残っていても除外します
    clean_keys = [k for k in params_dict.keys() if k != "reason"]
    
    # loop_num, (設定値各種...), reason の順番でヘッダーを作成
    fieldnames = ["loop_num"] + clean_keys + ["reason"]
    # -------------------------------------------
    
    # 追記モード ("a") でファイルを開く
    with open(log_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        
        # 新規ファイルならヘッダーを書き込む
        if not file_exists:
            writer.writeheader()
        
        # 書き込むデータ行を作成
        row_data = {"loop_num": loop_num}
        for key, value in params_dict.items():
            # --- [修正箇所] データ行の重複防止 ---
            if key == "reason":
                continue  # params_dictの中身のreasonは無視し、関数の引数(reason="")を優先する
            
            # 小数（float）の場合は小数点以下4桁まで残す（AIの細かい「ずらし」を記録するため）
            if isinstance(value, (float, np.float64, np.float32)):
                row_data[key] = f"{value:.4f}" 
            else:
                row_data[key] = value
        
        # 選定理由を追加
        row_data["reason"] = reason
                
        # 1行追記
        writer.writerow(row_data)
