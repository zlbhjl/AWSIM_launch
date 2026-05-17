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
import ray
from datetime import datetime

from redis_cluster.cluster_config import MASTER_IP, RAY_PORT

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
        formulas_config = getattr(cfg, 'FORMULAS', [])
    except ImportError:
        print(f"[Error] configs/{args.type}.py が見つかりません。")
        sys.exit(1)

    tool_dir = "/home/passd/aw-cheaker/Maude-3.5.1/AW-CheckerPy"
    traces_dir = os.environ.get("AW_OUTPUT_DIR", "/home/passd/simulation_traces")
    formulas_path = os.path.join(tool_dir, "formulas.txt")
    dataset_csv_path = os.path.join(traces_dir, f"{args.type}_dataset.csv")
    error_detail_log_path = os.path.join(traces_dir, "checker_errors_detail.log")
    local_history_path = os.path.join(traces_dir, "processed_loops_history.csv")

    # 旧バージョンにあった環境変数の設定（これがないとMaude等が動かない可能性があります）
    my_env = os.environ.copy()
    my_env["PWD"] = tool_dir

    # 分散対応: Rayクラスターの共有ストアに接続
    # [修正] 各号機が自分のIPで正しく接続できるよう _node_ip_address を削除
    ray.init(address=f"{MASTER_IP}:{RAY_PORT}", namespace='awsim_cluster', ignore_reinit_error=True)
    
    is_host_mode = os.environ.get("EXEC_MODE") == "host"

    # --- 新機能: config に FORMULAS が定義されていれば formulas.txt を自動生成/上書き ---
    if formulas_config:
        # [修正] マスターからの同期を待たず、各自が独自のコンテナ内で formulas.txt を生成する
        try:
            with open(formulas_path, "w", encoding="utf-8") as f:
                for formula in formulas_config:
                    f.write(f"{formula}\n")
            print(f"[Info] {formulas_path} を設定ファイルに基づいて生成・上書きしました。")
        except Exception as e:
            print(f"[Warning] formulas.txt の生成に失敗しました (権限エラー等): {e}")

    print("[AW Checker] 共有金庫 (SharedStoreActor) を探しています...")
    for _ in range(10): # 最大約50秒待機
        try:
            shared_store = ray.get_actor("SharedStoreActor")
            print("[AW Checker] 共有金庫に接続しました！")
            break
        except ValueError:
            time.sleep(5)
    else:
        print("[AW Checker] ⚠️ 共有金庫が見つかりませんでした。ローカル保存モードで動作します。")
        shared_store = None

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

    if os.path.exists(dataset_csv_path):
        print(f"[Info] 既存のCSVから履歴を復元します。")
        try:
            with open(dataset_csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        loop_num = int(row["loop_num"])
                        processed_loops.add(loop_num)
                        stats["Total"] += 1
                        
                        # 統計の復元 (result_labels に基づく)
                        has_error = any(str(row.get(label)) == "-1" for label in result_labels)
                        is_unsafe = any(str(row.get(label)) == "1" for label in result_labels)

                        if has_error:
                            stats["Error"] +=1
                        elif is_unsafe:
                            stats["Unsafe"] += 1
                        else:
                            stats["Safe"] +=1
                    except ValueError:
                        pass
            print(f"[Info] 復元完了 - 統計: Safe={stats['Safe']}, Unsafe={stats['Unsafe']}, Error={stats['Error']}")
        except Exception as e:
            print(f"[Warning] 復元失敗: {e}")

    # 分散対応: ローカル履歴ファイルからの復元
    if os.path.exists(local_history_path):
        print(f"[Info] ローカル履歴ファイルから処理済みループを復元します。")
        try:
            with open(local_history_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().isdigit():
                        processed_loops.add(int(line.strip()))
            print(f"[Info] 復元完了 - ローカル履歴から {len(processed_loops)} 件のループ番号を復元しました。")
        except Exception as e:
            print(f"[Warning] ローカル履歴の復元失敗: {e}")

    print(f"--- 監視開始: {traces_dir} ---")

    # ---------------------------------------------------------
    # 3. 監視ループ
    # ---------------------------------------------------------
    try:
        while True:
            # footage.meta.json などを除外するため 'footage' を含まないものだけを対象にする
            json_files = [f for f in os.listdir(traces_dir) if f.endswith('.json') and '_eval_sim' in f and 'footage' not in f]
            
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
                is_timeout_dummy = False
                for _ in range(15):
                    time.sleep(1)
                    try:
                        with open(target_path, 'r', encoding='utf-8') as f:
                            content = f.read().strip()
                            if content == "TIMEOUT":
                                is_timeout_dummy = True
                                is_valid_json = False
                                break # タイムアウト用ダミーファイルなら待たずに即エラー判定
                            json.loads(content)
                        is_valid_json = True
                        break
                    except (json.JSONDecodeError, ValueError):
                        print(".", end="", flush=True)

                if is_timeout_dummy:
                    print(f"\n[スキップ] {target_file} はタイムアウトによりManagerで記録済みです。")
                    processed_loops.add(current_loop)
                    # ローカルの処理済み履歴に記録して次回以降は無視する
                    with open(local_history_path, "a", encoding="utf-8") as f:
                        f.write(f"{current_loop}\n")
                    continue

                if not is_valid_json:
                    print(f"\n[エラー] {target_file} の書き込みが完了しませんでした（JSON破損）。")
                    parsed_row = {"loop_num": current_loop}
                    for item in metric_config:
                        parsed_row[item["header"]] = -1
                    stats["Error"] += 1
                    stats["Total"] += 1
                    
                    # CSV保存処理（continueでスキップされる前に書き込む）
                    # [修正] 共有金庫に結果をマージさせる
                    if shared_store:
                        ray.get(shared_store.log_and_merge_result.remote(args.type, parsed_row))
                    
                    # [追加] コンテナローカルにも結果を保存
                    try:
                        file_exists = os.path.exists(dataset_csv_path)
                        with open(dataset_csv_path, "a", newline="", encoding="utf-8") as f:
                            writer = csv.DictWriter(f, fieldnames=all_headers)
                            if not file_exists or os.path.getsize(dataset_csv_path) == 0:
                                writer.writeheader()
                            writer.writerow({k: parsed_row.get(k, "") for k in all_headers})
                    except PermissionError:
                        print(f"[Warning] ローカルの {dataset_csv_path} に書き込む権限がありません。")
                    
                    processed_loops.add(current_loop)
                    # ローカルの処理済み履歴に記録
                    try:
                        with open(local_history_path, "a", encoding="utf-8") as f:
                            f.write(f"{current_loop}\n")
                    except PermissionError:
                        print(f"[Warning] ローカル履歴 {local_history_path} に書き込む権限がありません。")
                        
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
                            # NPCスタック等の特定の検証項目が1(異常)になった場合は、システムエラーとして扱う
                            if "stuck" in header:
                                has_error = True
                            else:
                                is_any_fail = True
                    else:
                        parsed_row[header] = -1
                        has_error = True
                        # エラーログへの記録
                        if shared_store:
                            ray.get(shared_store.log_error_detail.remote(error_detail_log_path, target_file, header, output_log, error_log))
                        
                        # [追加] コンテナローカルにもエラーログを常に保存
                        try:
                            with open(error_detail_log_path, "a", encoding="utf-8") as ef:
                                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                ef.write(f"[{timestamp}] {target_file} | {header}\nSTDOUT: {output_log}\nSTDERR: {error_log}\n{'-'*30}\n")
                        except PermissionError:
                            print(f"[Warning] エラー詳細を {error_detail_log_path} に書き込む権限がありません。")

                # --- [追加] 論理的矛盾（TTCのすり抜け）の自動補正 ---
                # 衝突(c_collision=1)している場合、すべてのTTC指標は1(違反)にする
                if parsed_row.get("c_collision") == 1:
                    for key in list(parsed_row.keys()):
                        if key.startswith("c_ttc_"):
                            parsed_row[key] = 1
                            is_any_fail = True
                            
                # さらに、厳しいTTC(例: 0.3)が1なら、緩いTTC(例: 1.5)も1に補正する
                ttc_keys = [k for k in parsed_row.keys() if k.startswith("c_ttc_")]
                ttc_keys.sort(key=lambda x: float(x.split("_")[-1]))
                is_violated = False
                for k in ttc_keys:
                    if parsed_row[k] == 1:
                        is_violated = True
                    elif is_violated:
                        parsed_row[k] = 1

                # 統計の更新
                if has_error:
                    stats["Error"] += 1
                    res_str = "ERROR ⚠️ (異常/解析エラー検出)"
                else:
                    if is_any_fail:
                        stats["Unsafe"] += 1
                        res_str = "UNSAFE ❌"
                    else:
                        stats["Safe"] += 1
                        res_str = "SAFE ✅"
                stats["Total"] += 1

                # CSV保存
                # [修正] 共有金庫に結果をマージさせる
                if shared_store:
                    ray.get(shared_store.log_and_merge_result.remote(args.type, parsed_row))
                
                # [追加] コンテナローカルにも結果を保存
                try:
                    file_exists = os.path.exists(dataset_csv_path)
                    with open(dataset_csv_path, "a", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=all_headers)
                        if not file_exists or os.path.getsize(dataset_csv_path) == 0:
                            writer.writeheader()
                        writer.writerow({k: parsed_row.get(k, "") for k in all_headers})
                except PermissionError:
                    print(f"[Warning] ローカルの {dataset_csv_path} に書き込む権限がありません。")
                
                processed_loops.add(current_loop)
                # ローカルの処理済み履歴に記録
                try:
                    with open(local_history_path, "a", encoding="utf-8") as f:
                        f.write(f"{current_loop}\n")
                except PermissionError:
                    print(f"[Warning] ローカル履歴 {local_history_path} に書き込む権限がありません。")

                # --- 旧バージョンのUI ---
                print(f">>> 結果: {res_str}")
                print(f"====== 統計 (Total: {stats['Total']}) ======")
                print(f"  衝突なし: {stats['Safe']} | 衝突あり: {stats['Unsafe']} | エラー: {stats['Error']}")
                print(f"===================================")

    except KeyboardInterrupt:
        print("\n監視を終了します。")

if __name__ == "__main__":
    main()
