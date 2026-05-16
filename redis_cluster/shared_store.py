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
        self.param_buffer = {}
        self.output_dir = os.path.expanduser("~/simulation_traces")
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            if not os.access(self.output_dir, os.W_OK):
                print(f"[SharedStoreActor] 🚨 致命的エラー: {self.output_dir} への書き込み権限がありません！")
                print("  -> マスターPCで 'sudo chown -R $USER:$USER ~/simulation_traces' を実行してください。")
        except Exception as e:
            print(f"[SharedStoreActor] 🚨 致命的エラー: {self.output_dir} の作成に失敗しました: {e}")
        # 古いデータを破棄するまでの制限時間（秒）
        # run_managerのタイムアウト(300秒)より余裕を持たせて600秒(10分)に設定
        self.buffer_timeout_sec = 600

    def _cleanup_stale_buffer(self):
        """一定時間経過した古いバッファを削除してメモリリークを防ぐ"""
        now = datetime.now()
        stale_keys = []
        for loop_num, data in self.param_buffer.items():
            if (now - data["timestamp"]).total_seconds() > self.buffer_timeout_sec:
                stale_keys.append(loop_num)
        
        for k in stale_keys:
            print(f"[SharedStoreActor] ⚠️ 警告: loop_num {k} のデータが長時間放置されたため、バッファから破棄されました。")
            self.param_buffer.pop(k, None)

    def log_parameters(self, output_dir: str, file_name: str, loop_num: int, params_dict: dict, reason: str = ""):
        """パラメータをメモリ上のバッファに一時保存する"""
        self._cleanup_stale_buffer()
        self.param_buffer[loop_num] = {
            "params": params_dict,
            "reason": reason,
            "timestamp": datetime.now()
        }

    def _write_to_dataset(self, scenario_name: str, full_row: dict):
        """単一のデータセットCSVに1行を書き込む共通関数"""
        dataset_file = os.path.join(self.output_dir, f"{scenario_name}_dataset.csv")
        
        try:
            file_exists = os.path.exists(dataset_file)
            
            # ヘッダーの順番を一定に保つためソートする
            fieldnames = sorted(full_row.keys())

            with open(dataset_file, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                if not file_exists or os.path.getsize(dataset_file) == 0:
                    writer.writeheader()
                writer.writerow(full_row)
        except PermissionError:
            print(f"[SharedStoreActor] 🚨 権限エラー: {dataset_file} に書き込めません。(Permission denied)")
        except Exception as e:
            print(f"[SharedStoreActor] ❌ 書き込みエラー: {e}")

    def log_and_merge_result(self, scenario_name: str, result_row: dict):
        """結果を受け取り、バッファ内のパラメータと結合してCSVに書き込む"""
        loop_num = result_row.get("loop_num")
        if loop_num is None:
            return

        if loop_num in self.param_buffer:
            buffered_data = self.param_buffer.pop(loop_num)
            params_dict = buffered_data["params"]
            
            # パラメータと結果を結合
            full_row = result_row.copy()
            full_row.update(params_dict)
            full_row["reason"] = buffered_data["reason"]
            
            # 値をフォーマット
            for key, value in full_row.items():
                if isinstance(value, (float, np.float64, np.float32)):
                    full_row[key] = f"{value:.4f}"

            self._write_to_dataset(scenario_name, full_row)
        else:
            # パラメータがバッファにない場合（タイムアウトなどで先にフラッシュされた可能性）
            print(f"[SharedStoreActor] 警告: loop_num {loop_num} のパラメータがバッファに見つかりません。")

    def flush_timeout_task(self, scenario_name: str, loop_num: int, params_dict: dict, reason: str, result_headers: list):
        """タイムアウトしたタスクをエラーとして記録する"""
        # 結果部分を-1で埋める
        result_row = {"loop_num": loop_num}
        for header in result_headers:
            if header != "loop_num":
                result_row[header] = -1

        # パラメータと結合
        full_row = result_row.copy()
        full_row.update(params_dict)
        full_row["reason"] = reason

        # バッファに残っていても削除
        self.param_buffer.pop(loop_num, None)

        self._write_to_dataset(scenario_name, full_row)

    def log_error_detail(self, error_detail_log_path: str, target_file: str, header: str, output_log: str, error_log: str):
        # 出力先は self.output_dir に統一
        error_log_path = os.path.join(self.output_dir, os.path.basename(error_detail_log_path))
        try:
            os.makedirs(os.path.dirname(error_log_path), exist_ok=True)
            with open(error_log_path, "a", encoding="utf-8") as ef:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ef.write(f"[{timestamp}] {target_file} | {header}\nSTDOUT: {output_log}\nSTDERR: {error_log}\n{'-'*30}\n")
        except PermissionError:
            print(f"[SharedStoreActor] 🚨 権限エラー: {error_log_path} にエラー詳細を書き込めません。")
        except Exception as e:
            print(f"[SharedStoreActor] ❌ 書き込みエラー: {e}")