#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import os
import time
import sys
import csv
import re
import json
import argparse
import importlib
from datetime import datetime

def main():
    # ---------------------------------------------------------
    # 1. 設定読み込み (新バージョンの汎用機能を維持)
    # ---------------------------------------------------------
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", type=str, default="uturn", help="Scenario type")
    args, unknown = parser.parse_known_args()

    try:
        cfg = importlib.import_module(f"configs.{args.type}")
        result_labels = getattr(cfg, 'RESULT_LABELS', [])
    except ImportError:
        print(f"[Error] configs/{args.type}.py が見つかりません。")
        sys.exit(1)

    tool_dir = os.path.expanduser("~/aw-cheaker/Maude-3.5.1/AW-CheckerPy")
    traces_dir = os.path.expanduser("~/simulation_traces")
    results_csv_path = os.path.join(traces_dir, "checker_results.csv")
    formulas_path = os.path.join(tool_dir, "formulas.txt")
    error_detail_log_path = os.path.join(traces_dir, "checker_errors_detail.log")

    # 旧バージョンにあった環境変数の設定（これがないとMaude等が動かない可能性があります）
    my_env = os.environ.copy()
    my_env["PWD"] = tool_dir

    if not os.path.exists(formulas_path):
        print(f"[Error] {formulas_path} が見つかりません。")
        sys.exit(1)

    with open(formulas_path, "r") as f:
        formulas = [line.strip() for line in f if line.strip()]

    metric_config = []
    for i, formula in enumerate(formulas):
        label = result_labels[i] if i < len(result_labels) else f"formula_{i+1}"
        metric_config.append({"formula": formula, "header": label})

    all_headers = ["loop_num"] + [m["header"] for m in metric_config]

    # ---------------------------------------------------------
    # 2. CSVの読み込み・再開位置の特定 (旧バージョンの復元ロジック)
    # ---------------------------------------------------------
    stats = {"Safe": 0, "Unsafe": 0, "Error": 0, "Total": 0}
    processed_loops = set()

    if os.path.exists(results_csv_path):
        print(f"[Info] 既存のCSVから履歴を復元します。")
        try:
            with open(results_csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        loop_num = int(row["loop_num"])
                        processed_loops.add(loop_num)
                        stats["Total"] += 1
                        
                        values = [int(v) for k, v in row.items() if k != "loop_num" and v != ""]
                        if -1 in values:
                            stats["Error"] += 1
                        elif 1 in values:
                            stats["Unsafe"] += 1
                        else:
                            stats["Safe"] += 1
                    except ValueError:
                        pass
            print(f"[Info] 復元完了 - 統計: Safe={stats['Safe']}, Unsafe={stats['Unsafe']}, Error={stats['Error']}")
        except Exception as e:
            print(f"[Warning] 復元失敗: {e}")

    print(f"--- 監視開始: {traces_dir} ---")

    # ---------------------------------------------------------
    # 3. 監視ループ
    # ---------------------------------------------------------
    try:
        while True:
            json_files = [f for f in os.listdir(traces_dir) if f.endswith('.json') and 'test' in f and 'meta' not in f]
            
            new_files = []
            for f in json_files:
                match = re.search(r'sim(\d+)', f)
                if match:
                    loop_num = int(match.group(1))
                    if loop_num not in processed_loops:
                        new_files.append((loop_num, f))
            
            new_files.sort(key=lambda x: x[0])

            if not new_files:
                sys.stdout.write(f"\r待機中... 累計: Safe={stats['Safe']} Unsafe={stats['Unsafe']} Error={stats['Error']}   ")
                sys.stdout.flush()
                time.sleep(2)
                continue

            for current_loop, target_file in new_files:
                target_path = os.path.join(traces_dir, target_file)
                print(f"\n\nDetected: {target_file}")

                # --- 旧バージョンの安全性：JSONパースによる書き込み完了待機 ---
                print("  [待機] JSONデータの書き込み完了を待っています...", end="", flush=True)
                is_valid_json = False
                for _ in range(15):
                    time.sleep(1)
                    try:
                        with open(target_path, 'r', encoding='utf-8') as f:
                            json.load(f)
                        is_valid_json = True
                        break
                    except (json.JSONDecodeError, ValueError):
                        print(".", end="", flush=True)

                if not is_valid_json:
                    print(f"\n[エラー] {target_file} の書き込みが完了しませんでした（JSON破損）。")
                    parsed_row = {"loop_num": current_loop}
                    for item in metric_config:
                        parsed_row[item["header"]] = -1
                    stats["Error"] += 1
                    stats["Total"] += 1
                    processed_loops.add(current_loop)
                    continue
                
                print(" 完了！ 解析を開始します。")

                # --- 修正の核心部：旧バージョンの実行ロジック ---
                # main.py ではなく aw_checkerpy.py を1回だけ呼び出し、結果を一括で抽出する
                command = ["python3", "aw_checkerpy.py", target_path]
                result = subprocess.run(command, cwd=tool_dir, env=my_env, capture_output=True, text=True)
                output_log = result.stdout
                error_log = result.stderr

                parsed_row = {"loop_num": current_loop}
                is_any_fail = False
                has_error = False

                for item in metric_config:
                    formula = item["formula"]
                    header = item["header"]
                    
                    # 旧バージョンの抽出ロジック（正規表現）
                    pattern = re.escape(formula) + r".*?Model checking result: (True|False)"
                    match = re.search(pattern, output_log, re.DOTALL)

                    if match:
                        val = 0 if match.group(1) == "True" else 1
                        parsed_row[header] = val
                        if val == 1:
                            is_any_fail = True
                    else:
                        parsed_row[header] = -1
                        has_error = True
                        # エラーログへの記録
                        with open(error_detail_log_path, "a", encoding="utf-8") as ef:
                            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            ef.write(f"[{timestamp}] {target_file} | {header}\nSTDOUT: {output_log}\nSTDERR: {error_log}\n{'-'*30}\n")

                # 統計の更新
                if has_error:
                    stats["Error"] += 1
                    res_str = "ERROR ⚠️ (-1 検出)"
                else:
                    if is_any_fail:
                        stats["Unsafe"] += 1
                        res_str = "UNSAFE ❌"
                    else:
                        stats["Safe"] += 1
                        res_str = "SAFE ✅"
                stats["Total"] += 1

                # CSV保存
                file_needs_header = not os.path.exists(results_csv_path) or os.path.getsize(results_csv_path) == 0
                with open(results_csv_path, "a", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=all_headers)
                    if file_needs_header: writer.writeheader()
                    writer.writerow(parsed_row)

                processed_loops.add(current_loop)

                # --- 旧バージョンのUI ---
                print(f">>> 結果: {res_str}")
                print(f"====== 統計 (Total: {stats['Total']}) ======")
                print(f"  衝突なし: {stats['Safe']} | 衝突あり: {stats['Unsafe']} | エラー: {stats['Error']}")
                print(f"===================================")

    except KeyboardInterrupt:
        print("\n監視を終了します。")

if __name__ == "__main__":
    main()
