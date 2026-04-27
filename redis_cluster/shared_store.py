#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import ray
import os
import csv
import numpy as np
from datetime import datetime

@ray.remote
class SharedStoreActor:
    def __init__(self):
        print("[SharedStoreActor] 共有ストア (金庫番) が起動しました。スレッドセーフな書き込みを管理します。")

    def log_parameters(self, output_dir: str, file_name: str, loop_num: int, params_dict: dict, reason: str = ""):
        os.makedirs(output_dir, exist_ok=True)
        log_file = os.path.join(output_dir, file_name)
        file_exists = os.path.exists(log_file)
        
        clean_keys = [k for k in params_dict.keys() if k != "reason"]
        fieldnames = ["loop_num"] + clean_keys + ["reason"]
        
        with open(log_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            
            row_data = {"loop_num": loop_num}
            for key, value in params_dict.items():
                if key == "reason":
                    continue
                if isinstance(value, (float, np.float64, np.float32)):
                    row_data[key] = f"{value:.4f}" 
                else:
                    row_data[key] = value
            row_data["reason"] = reason
            writer.writerow(row_data)

    def log_checker_result(self, results_csv_path: str, all_headers: list, parsed_row: dict):
        os.makedirs(os.path.dirname(results_csv_path), exist_ok=True)
        file_needs_header = not os.path.exists(results_csv_path) or os.path.getsize(results_csv_path) == 0
        with open(results_csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_headers)
            if file_needs_header: 
                writer.writeheader()
            writer.writerow(parsed_row)

    def log_error_detail(self, error_detail_log_path: str, target_file: str, header: str, output_log: str, error_log: str):
        os.makedirs(os.path.dirname(error_detail_log_path), exist_ok=True)
        with open(error_detail_log_path, "a", encoding="utf-8") as ef:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ef.write(f"[{timestamp}] {target_file} | {header}\nSTDOUT: {output_log}\nSTDERR: {error_log}\n{'-'*30}\n")