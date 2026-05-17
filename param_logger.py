#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import csv
import numpy as np
import ray
import importlib

# 理論安全領域計算モジュールのインポート
try:
    from theoretical_calculator import TheoreticalSafetyCalculator
except ImportError:
    TheoreticalSafetyCalculator = None

# 計算クラスのインスタンスをキャッシュ（毎ループ初期化するのを防ぐ）
_calculator_instance = None

def log_parameters(output_dir: str, file_name: str, loop_num: int, params_dict: dict, reason: str = ""):
    """
    シミュレーションのパラメータをCSVに記録する汎用モジュール。
    AIの選定理由（reason）も記録できるように拡張しました。
    """
    # どのコンピュータ（コンテナ）で実行されたか識別するIDを取得
    worker_id = os.environ.get("ROS_DOMAIN_ID", "master")
    is_host_mode = os.environ.get("EXEC_MODE") == "host"
    log_dict = params_dict.copy()
    log_dict["worker_id"] = worker_id

    # --- 新規追加部分: 理論値の算出と結合 ---
    global _calculator_instance
    if TheoreticalSafetyCalculator and all(k in log_dict for k in ["dx0", "ego_speed", "npc_speed"]):
        if _calculator_instance is None:
            # configを動的に読み込む（ファイル名からシナリオ名を推測: 例 'uturn_parameters.csv' -> 'uturn'）
            scenario_name = file_name.replace("_parameters.csv", "")
            cfg = None
            try:
                cfg = importlib.import_module(f"configs.{scenario_name}")
            except ImportError:
                pass
            
            _calculator_instance = TheoreticalSafetyCalculator(cfg)
            
        # アプローチA, B 両方の計算結果を取得
        theory_results = _calculator_instance.evaluate(
            log_dict["dx0"], 
            log_dict["ego_speed"], 
            log_dict["npc_speed"]
        )
        log_dict.update(theory_results)  # 結果をログにマージ
    # ----------------------------------------

    # 分散対応: Actorが立ち上がっていれば、共有ストア経由で安全に書き込む
    # [修正] ホストモードの制限を解除し、21号機もAI用の共有金庫にパラメータを預ける
    if ray.is_initialized():
        try:
            store = ray.get_actor("SharedStoreActor")
            ray.get(store.log_parameters.remote(output_dir, file_name, loop_num, log_dict, reason))
            return
        except ValueError:
            pass # 見つからない場合はローカルの直接書き込みにフォールバック

    os.makedirs(output_dir, exist_ok=True)
    log_file = os.path.join(output_dir, file_name)
    
    file_exists = os.path.exists(log_file)
    
    # --- [修正箇所] ヘッダーの重複エラー防止 ---
    # params_dict 内に万が一 'reason' というキーが残っていても除外します
    clean_keys = [k for k in log_dict.keys() if k != "reason"]
    
    # loop_num, (設定値各種...), reason の順番でヘッダーを作成
    fieldnames = ["loop_num"] + clean_keys + ["reason"]
    # -------------------------------------------
    
    # 追記モード ("a") でファイルを開く
    try:
        with open(log_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            
            # 新規ファイルまたは空ファイルならヘッダーを書き込む
            if not file_exists or os.path.getsize(log_file) == 0:
                writer.writeheader()
            
            # 書き込むデータ行を作成
            row_data = {"loop_num": loop_num}
            for key, value in log_dict.items():
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
    except PermissionError:
        print(f"\n[ParamLogger] 🚨 権限エラー: {log_file} に書き込めません。(Permission denied)")
    except Exception as e:
        print(f"\n[ParamLogger] ❌ パラメータの記録に失敗しました: {e}")
