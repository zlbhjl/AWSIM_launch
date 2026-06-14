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

# --- 修正: モジュール検索パスの追加 ---
LAUNCH_DIR = os.path.dirname(os.path.abspath(__file__))
if LAUNCH_DIR not in sys.path:
    sys.path.append(LAUNCH_DIR)

# AW_Kinematics_Extractor 内部のモジュール(phase1_parserなど)を直接importできるようにパスを追加
AW_EXTRACTOR_DIR = os.path.join(LAUNCH_DIR, "AW_Kinematics_Extractor")
if AW_EXTRACTOR_DIR not in sys.path:
    sys.path.append(AW_EXTRACTOR_DIR)

from redis_cluster.cluster_config import MASTER_IP, RAY_PORT

# --- 新規追加: 高速な運動学抽出器のインポート ---
try:
    from AW_Kinematics_Extractor.main import AWKinematicsPipeline
except ImportError as e:
    print(f"[Warning] AW_Kinematics_Extractor のインポートに失敗しました: {e}")
    AWKinematicsPipeline = None

def main():
    # ---------------------------------------------------------
    # 1. 設定読み込み (新バージョンの汎用機能を維持)
    # ---------------------------------------------------------
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", type=str, default="uturn", help="Scenario type")
    parser.add_argument("--ext_mode", type=str, default="cvm", help="Kinematics Extractor Mode (cvm/ctrv/maude)")
    args, unknown = parser.parse_known_args()

    try:
        cfg = importlib.import_module(f"configs.{args.type}")
        result_labels = getattr(cfg, 'RESULT_LABELS', [])
        formulas_config = getattr(cfg, 'FORMULAS', [])
        target_npcs = getattr(cfg, 'TARGET_NPCS', ["npc1"])
        invalid_conditions = getattr(cfg, 'INVALID_CONDITIONS', {})
    except ImportError:
        print(f"[Error] configs/{args.type}.py が見つかりません。")
        sys.exit(1)

    tool_dir = "/home/passd/aw-cheaker/Maude-3.5.1/AW-CheckerPy"
    traces_dir = os.environ.get("AW_OUTPUT_DIR", "/home/passd/simulation_traces")
    formulas_path = os.path.join(tool_dir, "formulas.txt")
    dataset_csv_path = os.path.join(traces_dir, f"{args.type}_dataset.csv")
    base_dataset_csv_path = os.path.join(traces_dir, f"{args.type}_dataset_base.csv")
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

    all_headers = ["loop_num", "min_ttc", "min_distance"] + [m["header"] for m in metric_config]

    # ---------------------------------------------------------
    # 2. CSVの読み込み・再開位置の特定 (旧バージョンの復元ロジック)
    # ---------------------------------------------------------
    stats = {"Safe": 0, "Unsafe": 0, "Error": 0, "Total": 0}
    processed_loops = set()

    for csv_path in [base_dataset_csv_path, dataset_csv_path]:
        if os.path.exists(csv_path):
            print(f"[Info] 既存のCSV ({os.path.basename(csv_path)}) から履歴を復元します。")
            try:
                with open(csv_path, "r", encoding="utf-8") as f:
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
                                stats["Error"] += 1
                            elif is_unsafe:
                                stats["Unsafe"] += 1
                            else:
                                stats["Safe"] += 1
                        except ValueError:
                            pass
                print(f"[Info] 復元完了 - 統計: Safe={stats['Safe']}, Unsafe={stats['Unsafe']}, Error={stats['Error']}")
            except Exception as e:
                print(f"[Warning] {os.path.basename(csv_path)} の復元失敗: {e}")

    # 分散対応: ローカル履歴ファイルからの復元
    if os.path.exists(local_history_path):
        try:
            with open(local_history_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.isdigit():
                        processed_loops.add(int(line))
        except Exception as e:
            print(f"[Warning] ローカル履歴の読み込み失敗: {e}")


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

                try:
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
                        parsed_row = {"loop_num": current_loop, "min_ttc": -1, "min_distance": -1}
                        for item in metric_config:
                            parsed_row[item["header"]] = -1
                        stats["Error"] += 1
                        stats["Total"] += 1
                        
                        # CSV保存処理
                        if shared_store:
                            ray.get(shared_store.log_and_merge_result.remote(args.type, parsed_row))
                        else:
                            try:
                                file_exists = os.path.exists(dataset_csv_path)
                                headers = all_headers
                                if file_exists and os.path.getsize(dataset_csv_path) > 0:
                                    with open(dataset_csv_path, "r", encoding="utf-8") as f:
                                        existing_headers = next(csv.reader(f), None)
                                        if existing_headers: headers = existing_headers
                                        
                                with open(dataset_csv_path, "a", newline="", encoding="utf-8") as f:
                                    writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore", restval="")
                                    if not file_exists or os.path.getsize(dataset_csv_path) == 0:
                                        writer.writeheader()
                                    writer.writerow(parsed_row)
                            except PermissionError:
                                print(f"[Warning] ローカルの {dataset_csv_path} に書き込む権限がありません。")
                        
                        processed_loops.add(current_loop)
                        try:
                            with open(local_history_path, "a", encoding="utf-8") as f:
                                f.write(f"{current_loop}\n")
                        except PermissionError:
                            pass
                        continue
                    
                    print(" 完了！ 解析を開始します。")

                    parsed_row = {"loop_num": current_loop}
                    is_any_fail = False
                    has_error = False

                    metrics = {}
                    min_ttc = -1.0
                    min_distance = -1.0
                    min_ttb = -1.0
                    z_margin = -1.0
                    
                    # 1. AWKinematicsPipeline で最小TTCと最小距離のみを抽出する
                    if AWKinematicsPipeline is not None:
                        try:
                            pipeline = AWKinematicsPipeline(mode=args.ext_mode, target_npcs=target_npcs)
                            kinematics_metrics = pipeline.get_metrics(target_path)
                            min_ttc = kinematics_metrics.get("min_ttc", -1.0)
                            min_distance = kinematics_metrics.get("min_distance", -1.0)
                            min_ttb = kinematics_metrics.get("min_ttb", -1.0)
                            z_margin = kinematics_metrics.get("z_margin", -1.0)
                            print(f"  [高速抽出] 最小TTC: {min_ttc} 秒 | 最小距離: {min_distance:.4f} m | 最小TTB: {min_ttb:.4f} 秒 | 総合マージン: {z_margin:.4f}")
                        except Exception as e:
                            print(f"  [エラー] AWKinematicsPipelineでの抽出に失敗しました: {e}")

                    parsed_row["min_ttc"] = min_ttc if min_ttc != -1.0 else ""
                    parsed_row["min_distance"] = min_distance if min_distance != -1.0 else ""
                    parsed_row["min_ttb"] = min_ttb if min_ttb != -1.0 else ""
                    parsed_row["z_margin"] = z_margin if z_margin != -1.0 else ""

                    # 2. すべての指標について Maude (aw_checkerpy.py) を呼び出して厳密な論理検証を行う
                    print(f"  [Maude検証] すべての指標({len(metric_config)}件)を厳密に論理検証します...")
                    command = ["python3", "aw_checkerpy.py", target_path]
                    result = subprocess.run(command, cwd=tool_dir, env=my_env, capture_output=True, text=True)
                    output_log = result.stdout
                    error_log = result.stderr

                    for item in metric_config:
                        formula = item["formula"]
                        header = item["header"]
                        pattern = re.escape(formula) + r".*?Model checking result: (True|False)"
                        match = re.search(pattern, output_log, re.DOTALL)

                        if match:
                            metrics[header] = 0 if match.group(1) == "True" else 1
                        else:
                            metrics[header] = -1
                            has_error = True
                            if shared_store:
                                ray.get(shared_store.log_error_detail.remote(error_detail_log_path, target_file, header, output_log, error_log))

                    # 4. 最終的な結果の統合と判定
                    for item in metric_config:
                        header = item["header"]
                        # Maudeが計算できたものはその値を、失敗したものは -1 を記録する
                        val = int(metrics.get(header, -1))
                        
                        if header == "c_collision" and val == 1:
                            parsed_row["min_distance"] = 0.0
                            
                        parsed_row[header] = val
                        if val == 1:
                            is_any_fail = True

                    if parsed_row.get("c_collision") == 1:
                        for key in list(parsed_row.keys()):
                            if key.startswith("c_ttc_"):
                                parsed_row[key] = 1
                                is_any_fail = True
                                
                    ttc_keys = [k for k in parsed_row.keys() if k.startswith("c_ttc_")]
                    ttc_keys.sort(key=lambda x: float(x.split("_")[-1]))
                    is_violated = False
                    for k in ttc_keys:
                        if parsed_row[k] == 1:
                            is_violated = True
                        elif is_violated:
                            parsed_row[k] = 1

                    # --- [追加] 設定ファイルに基づく無効化条件のチェック ---
                    for inv_key, inv_val in invalid_conditions.items():
                        if parsed_row.get(inv_key) == inv_val:
                            has_error = True
                            break

                    if has_error:
                        stats["Error"] += 1
                        res_str = "ERROR ⚠️ (異常/無効化条件検出)"
                        # エラー時はすべての結果を -1 に上書きする
                        parsed_row["min_ttc"] = -1
                        parsed_row["min_distance"] = -1
                        parsed_row["min_ttb"] = -1
                        parsed_row["z_margin"] = -1
                        for item in metric_config:
                            parsed_row[item["header"]] = -1
                    else:
                        if is_any_fail:
                            stats["Unsafe"] += 1
                            res_str = "UNSAFE ❌"
                        else:
                            stats["Safe"] += 1
                            res_str = "SAFE ✅"
                    stats["Total"] += 1

                    if shared_store:
                        ray.get(shared_store.log_and_merge_result.remote(args.type, parsed_row))
                    else:
                        try:
                            file_exists = os.path.exists(dataset_csv_path)
                            headers = all_headers
                            if file_exists and os.path.getsize(dataset_csv_path) > 0:
                                with open(dataset_csv_path, "r", encoding="utf-8") as f:
                                    existing_headers = next(csv.reader(f), None)
                                    if existing_headers: headers = existing_headers
                                    
                            with open(dataset_csv_path, "a", newline="", encoding="utf-8") as f:
                                writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore", restval="")
                                if not file_exists or os.path.getsize(dataset_csv_path) == 0:
                                    writer.writeheader()
                                writer.writerow(parsed_row)
                        except PermissionError:
                            pass
                    
                    processed_loops.add(current_loop)
                    try:
                        with open(local_history_path, "a", encoding="utf-8") as f:
                            f.write(f"{current_loop}\n")
                    except PermissionError:
                        pass

                    print(f">>> 結果: {res_str}")
                    print(f"====== 統計 (Total: {stats['Total']}) ======")
                    print(f"  衝突なし: {stats['Safe']} | 衝突あり: {stats['Unsafe']} | エラー: {stats['Error']}")
                    print(f"===================================")
                    
                except Exception as e:
                    import traceback
                    print(f"\n[Fatal Error] {target_file} の処理中に予期せぬエラーが発生しクラッシュを回避しました: {e}")
                    traceback.print_exc()
                    
                    # エラーで落ちた場合も、後続が止まらないようにエラー結果として記録・バッファ削除を行う
                    parsed_row = {"loop_num": current_loop, "min_ttc": -1, "min_distance": -1, "min_ttb": -1, "z_margin": -1}
                    for item in metric_config:
                        parsed_row[item["header"]] = -1
                    stats["Error"] += 1
                    stats["Total"] += 1
                    
                    if shared_store:
                        try:
                            ray.get(shared_store.log_and_merge_result.remote(args.type, parsed_row))
                        except Exception:
                            pass
                            
                    processed_loops.add(current_loop)
                    try:
                        with open(local_history_path, "a", encoding="utf-8") as f:
                            f.write(f"{current_loop}\n")
                    except PermissionError:
                        pass
                    continue

    except KeyboardInterrupt:
        print("\n監視を終了します。")

if __name__ == "__main__":
    main()
