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
    
    # 記録項目の整理
    # loop_num, (設定値各種...), reason の順番でヘッダーを作成
    fieldnames = ["loop_num"] + list(params_dict.keys()) + ["reason"]
    
    # 追記モード ("a") でファイルを開く
    with open(log_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        
        # 新規ファイルならヘッダーを書き込む
        if not file_exists:
            writer.writeheader()
        
        # 書き込むデータ行を作成
        row_data = {"loop_num": loop_num}
        for key, value in params_dict.items():
            # 小数（float）の場合は小数点以下3桁まで残す（AIの細かい「ずらし」を記録するため）
            if isinstance(value, (float, np.float64, np.float32)):
                row_data[key] = f"{value:.4f}" # AIの微細な調整を追うために精度を上げました
            else:
                row_data[key] = value
        
        # 選定理由を追加
        row_data["reason"] = reason
                
        # 1行追記
        writer.writerow(row_data)
