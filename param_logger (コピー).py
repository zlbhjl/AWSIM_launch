#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import csv

def log_parameters(output_dir: str, file_name: str, loop_num: int, params_dict: dict):
    """
    シミュレーションのパラメータをCSVに記録する汎用モジュール。
    渡されたパラメータ（辞書）のキーを自動的に読み取ってCSVのヘッダーを作成します。
    シナリオが変わってパラメータが増減しても、このコードは変更不要です。
    """
    os.makedirs(output_dir, exist_ok=True)
    log_file = os.path.join(output_dir, file_name)
    
    file_exists = os.path.exists(log_file)
    
    # "loop_num" を先頭にし、残りの項目名を辞書のキーから自動取得する
    fieldnames = ["loop_num"] + list(params_dict.keys())
    
    # 追記モード ("a") でファイルを開く
    with open(log_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        
        # ファイルが新規作成された場合のみ、1行目にヘッダー（項目名）を書き込む
        if not file_exists:
            writer.writeheader()
        
        # 書き込むデータ行を作成
        row_data = {"loop_num": loop_num}
        for key, value in params_dict.items():
            # 小数（float）の場合は見やすく小数点以下2桁に丸める
            if isinstance(value, float):
                row_data[key] = f"{value:.2f}"
            else:
                row_data[key] = value
                
        # 1行追記
        writer.writerow(row_data)
