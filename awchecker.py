#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import os
import time
import sys
import csv
import re

def main():
    # ---------------------------------------------------------
    # 1. パスの設定
    # ---------------------------------------------------------
    tool_dir = os.path.expanduser("~/aw-cheaker/Maude-3.5.1/AW-CheckerPy")
    traces_dir = os.path.expanduser("~/simulation_traces")
    results_csv_path = os.path.join(traces_dir, "checker_results.csv")

    # 検証項目とCSV列名の定義 (将来ここを増減させるだけでOK)
    METRIC_CONFIG = [
        {"formula": '[] ttc("npc1") >= 1.5', "header": "c_ttc_1.5"},
        {"formula": '[] ttc("npc1") >= 1.2', "header": "c_ttc_1.2"},
        {"formula": '[] ttc("npc1") >= 0.7', "header": "c_ttc_0.7"},
        {"formula": '[] ~ collision("ego", "npc1")', "header": "c_collision"},
        {"formula": '[] pos-diff("ego", "npc1") >= 4.0', "header": "c_dist_4.0"}
    ]
    all_headers = ["loop_num"] + [m["header"] for m in METRIC_CONFIG]

    # ---------------------------------------------------------
    # 2. 初期チェック
    # ---------------------------------------------------------
    my_env = os.environ.copy()
    my_env["PWD"] = tool_dir

    if not os.path.exists(tool_dir):
        print(f"[Fatal Error] ディレクトリが見つかりません: {tool_dir}")
        return

    os.chdir(tool_dir)
    print(f"--- 初期化開始: {os.getcwd()} ---")

    # ---------------------------------------------------------
    # 3. CSVの読み込み・再開位置の特定 (元の機能を継承)
    # ---------------------------------------------------------
    stats = {"Safe": 0, "Unsafe": 0, "Error": 0, "Total": 0}
    current_i = 1

    if os.path.exists(results_csv_path):
        print(f"[Info] 既存のCSVから履歴を復元します。")
        try:
            with open(results_csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                last_loop = 0
                for row in reader:
                    try:
                        loop_num = int(row["loop_num"])
                        # 代表として衝突判定(c_collision)を統計に使用
                        is_collision = int(row.get("c_collision", -1))
                        last_loop = max(last_loop, loop_num)
                        stats["Total"] += 1
                        if is_collision == 0: stats["Safe"] += 1
                        elif is_collision == 1: stats["Unsafe"] += 1
                        else: stats["Error"] += 1
                    except: pass
            if last_loop > 0:
                current_i = last_loop + 1
                print(f"[Info] sim{current_i} から再開します。統計: Safe={stats['Safe']}, Unsafe={stats['Unsafe']}")
        except Exception as e:
            print(f"[Warning] 復元失敗: {e}")
    else:
        # 新規作成時にヘッダーを書き込む
        with open(results_csv_path, "w", newline="", encoding="utf-8") as f:
            f.write(",".join(all_headers) + "\n")

    # ---------------------------------------------------------
    # 4. 監視ループ (アダプター化された実行部)
    # ---------------------------------------------------------
    try:
        while True:
            filename = f"uturn_test_sim{current_i}.json"
            trace_path = os.path.join(traces_dir, filename)

            if not os.path.exists(trace_path):
                msg = f"待機中... Next: {filename} | 累計統計: Safe={stats['Safe']} Unsafe={stats['Unsafe']}"
                print(f"\r{msg}", end="")
                time.sleep(2)
                continue
            
            print(f"\n\nDetected: {filename}")
            
            # AW-CheckerPy 実行 (第3引数を渡さず formulas.txt を使用)
            command = ["python3", "aw_checkerpy.py", trace_path]
            try:
                result = subprocess.run(command, cwd=tool_dir, env=my_env, capture_output=True, text=True)
                output_log = result.stdout
                
                # --- 多項目解析 (Parser) ---
                parsed_row = {"loop_num": current_i}
                is_any_fail = False # 統計表示用のフラグ

                for item in METRIC_CONFIG:
                    pattern = re.escape(item["formula"]) + r".*?Model checking result: (True|False)"
                    match = re.search(pattern, output_log, re.DOTALL)
                    
                    if match:
                        val = 0 if match.group(1) == "True" else 1
                        parsed_row[item["header"]] = val
                        if item["header"] == "c_collision" and val == 1:
                            is_any_fail = True
                    else:
                        parsed_row[item["header"]] = -1

                # 統計更新
                if parsed_row.get("c_collision") == 1: stats["Unsafe"] += 1
                elif parsed_row.get("c_collision") == 0: stats["Safe"] += 1
                else: stats["Error"] += 1
                stats["Total"] += 1

                # --- CSV追記 (ワイド形式) ---
                with open(results_csv_path, "a", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=all_headers)
                    writer.writerow(parsed_row)

                # 結果表示
                res_str = "UNSAFE ❌" if is_any_fail else "SAFE ✅"
                print(f">>> 結果: {res_str}")
                print(f"====== 統計 (Total: {stats['Total']}) ======")
                print(f"  衝突なし: {stats['Safe']} | 衝突あり: {stats['Unsafe']} | エラー: {stats['Error']}")
                print(f"===================================")

            except Exception as e:
                print(f"解析エラー: {e}")

            current_i += 1

    except KeyboardInterrupt:
        print("\n監視を終了します。")

if __name__ == "__main__":
    main()
