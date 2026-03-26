#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import os
import time
import sys
import csv  # CSVモジュールをインポート

def main():
    # ---------------------------------------------------------
    # 1. パスの設定
    # ---------------------------------------------------------
    tool_dir = os.path.expanduser("~/aw-cheaker/Maude-3.5.1/AW-CheckerPy")
    traces_dir = os.path.expanduser("~/simulation_traces")
    
    # 結果を保存するCSVのパス
    results_csv_path = os.path.join(traces_dir, "checker_results.csv")
    
    # ---------------------------------------------------------
    # 2. 環境変数の準備
    # ---------------------------------------------------------
    my_env = os.environ.copy()
    my_env["PWD"] = tool_dir

    print(f"--- 初期化プロセス開始 ---")
    
    if not os.path.exists(tool_dir):
        print(f"[Fatal Error] ディレクトリが見つかりません: {tool_dir}")
        return

    os.chdir(tool_dir)
    print(f"作業ディレクトリを移動しました: {os.getcwd()}")

    if "formal-model" in os.listdir("."):
        print("[OK] formal-model フォルダを確認しました。")
    else:
        print("[Warning] formal-model が見つかりません。")

    # ---------------------------------------------------------
    # 3. CSVファイルの確認と再開位置・統計の復元
    # ---------------------------------------------------------
    stats = {"True": 0, "False": 0, "Error": 0, "Total": 0}
    current_i = 1

    if os.path.exists(results_csv_path):
        print(f"[Info] 既存のCSVファイルを発見しました。履歴を読み込みます: {results_csv_path}")
        try:
            with open(results_csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                last_loop = 0
                for row in reader:
                    try:
                        loop_num = int(row["loop_num"])
                        is_collision = int(row["is_collision"])

                        # 最大のloop_numを記録
                        last_loop = max(last_loop, loop_num)
                        
                        # 過去の統計を復元
                        stats["Total"] += 1
                        if is_collision == 0:
                            stats["True"] += 1
                        elif is_collision == 1:
                            stats["False"] += 1
                        else:
                            stats["Error"] += 1
                    except ValueError:
                        pass  # 空行や不正な文字列をスキップ

            if last_loop > 0:
                current_i = last_loop + 1
                print(f"[Info] 最後の実行結果 (sim{last_loop}) を確認しました。")
                print(f"[Info] sim{current_i} から検証を再開します。")
                print(f"[Info] 復元された統計: Safe={stats['True']} Unsafe={stats['False']} Error={stats['Error']}")
        except Exception as e:
            print(f"[Warning] CSVの読み込みに失敗しました ({e})。sim1から開始します。")
    else:
        print("[Info] 新規CSVファイルを作成します。sim1から検証を開始します。")
        with open(results_csv_path, "w", newline="", encoding="utf-8") as f:
            f.write("loop_num,is_collision\n")

    # ---------------------------------------------------------
    # 4. 監視ループ
    # ---------------------------------------------------------
    try:
        while True:
            filename = f"uturn_test_sim{current_i}.json"
            trace_path = os.path.join(traces_dir, filename)

            # --- 待機 ---
            if not os.path.exists(trace_path):
                msg = f"待機中... Next: {filename} | 統計: Safe={stats['True']} Unsafe={stats['False']} (Error={stats['Error']})"
                print(f"\r{msg}", end="")
                time.sleep(2)
                continue
            
            # --- 実行 ---
            print(f"\n\nDetected: {filename}")
            time.sleep(1) 

            command = [
                "python3",
                "aw_checkerpy.py",
                trace_path,
                '[] ~ collision("ego", "npc1")'
            ]

            try:
                result = subprocess.run(
                    command, 
                    cwd=tool_dir,
                    env=my_env,          
                    capture_output=True, 
                    text=True
                )
                
                output_log = result.stdout
                
                # --- 結果判定と数値化 ---
                is_collision = None 
                
                if "Model checking result: False" in output_log:
                    stats["False"] += 1
                    res_str = "UNSAFE (False) ❌"
                    is_collision = 1  # False = 衝突した = 1
                    
                elif "Model checking result: True" in output_log:
                    stats["True"] += 1
                    res_str = "SAFE (True) ✅"
                    is_collision = 0  # True = 安全だった = 0
                    
                else:
                    stats["Error"] += 1
                    res_str = "ERROR ⚠️"
                    is_collision = -1 # エラーで判定不能
                    print("--- Checker Output ---")
                    print(output_log)
                    print("--- Error Log ---")
                    print(result.stderr)

                # 判定結果をCSVに追記（エラー以外の場合）
                if is_collision in [0, 1]:
                    with open(results_csv_path, "a", newline="", encoding="utf-8") as f:
                        f.write(f"{current_i},{is_collision}\n")
                
                stats["Total"] += 1
                valid = stats["True"] + stats["False"]
                rate = (stats["True"] / valid * 100) if valid > 0 else 0.0

                print(f">>> 結果: {res_str}")
                print(f"====== 統計 (sim1-{current_i}) ======")
                print(f"  安全(True) : {stats['True']}")
                print(f"  違反(False): {stats['False']}")
                print(f"  エラー     : {stats['Error']}")
                print(f"  安全率     : {rate:.1f}%")
                print(f"===================================")

            except Exception as e:
                print(f"実行エラー: {e}")

            current_i += 1

    except KeyboardInterrupt:
        print("\n終了します。")

if __name__ == "__main__":
    main()
