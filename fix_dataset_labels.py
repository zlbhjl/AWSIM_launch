#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import glob
import argparse
import pandas as pd
import re
import json
import subprocess
import concurrent.futures
import multiprocessing

# AWSIM_launch ディレクトリと抽出器へのパスを追加
LAUNCH_DIR = os.path.dirname(os.path.abspath(__file__))
if LAUNCH_DIR not in sys.path:
    sys.path.append(LAUNCH_DIR)

try:
    from redis_cluster import cluster_config
except ImportError:
    cluster_config = None

def sync_code_to_workers():
    if cluster_config is None:
        return
    print("\n[Sync] 司令塔から各ワーカーへ最新の検証プログラム(抽出器など)を同期しています...")
    for node_id, info in cluster_config.CLUSTER_NODES.items():
        if not info.get("enabled", True):
            continue
        ip = info.get("ip")
        user = info.get("user", "passd")
        if ip == cluster_config.MASTER_IP:
            continue
        
        print(f"  -> {info['machine']} ({ip}) へ最新コードを同期中...")
        rsync_cmd = f"rsync -avz --exclude 'simulation_traces*' --exclude '__pycache__' --exclude '*.log' -e 'ssh -o StrictHostKeyChecking=no' {LAUNCH_DIR}/ {user}@{ip}:~/AWSIM_launch/"
        try:
            res = subprocess.run(rsync_cmd, shell=True, capture_output=True, text=True)
            if res.returncode != 0:
                print(f"  -> [警告] {info['machine']} への同期に失敗しました: {res.stderr.strip()}")
        except Exception as e:
            print(f"  -> [警告] 同期実行時にエラー: {e}")

def _process_single_json(args):
    """マルチプロセス実行用の個別解析関数"""
    filepath, mode, metric_config, target_npcs = args
    import os
    import re
    import subprocess
    try:
        from AW_Kinematics_Extractor.main import AWKinematicsPipeline
        pipeline = AWKinematicsPipeline(mode=mode, target_npcs=target_npcs)
        metrics = pipeline.get_metrics(filepath)
    except Exception:
        metrics = {}
        
    min_ttc = metrics.get("min_ttc", -1.0)
    
    # 最小TTCの取得成否に関わらず、Maudeで詳細な論理検証を行う
    if metric_config:
        tool_dir = os.path.expanduser("~/aw-cheaker/Maude-3.5.1/AW-CheckerPy")
        my_env = os.environ.copy()
        my_env["PWD"] = tool_dir
        
        formulas = [item["formula"] for item in metric_config]
        command = ["python3", "aw_checkerpy.py", filepath] + formulas
        
        try:
            result = subprocess.run(command, cwd=tool_dir, env=my_env, capture_output=True, text=True)
            output_log = result.stdout
            
            for item in metric_config:
                formula = item["formula"]
                header = item["header"]
                pattern = re.escape(formula) + r".*?Model checking result: (True|False)"
                match = re.search(pattern, output_log, re.DOTALL)
                if match:
                    metrics[header] = 0 if match.group(1) == "True" else 1
                else:
                    metrics[header] = -1
        except Exception as e:
            pass

    match = re.search(r'sim(\d+)', os.path.basename(filepath))
    if match:
        return int(match.group(1)), metrics
    return None, None

def run_local_analysis(mode, search_dirs, scenario_type):
    """各マシン内でCPUコアをフル活用して並列でJSONを解析する"""
    import importlib
    metric_config = []
    try:
        cfg = importlib.import_module(f"configs.{scenario_type}")
        result_labels = getattr(cfg, 'RESULT_LABELS', [])
        formulas_config = getattr(cfg, 'FORMULAS', [])
        target_npcs = getattr(cfg, 'TARGET_NPCS', ["npc1"])
        for i, formula in enumerate(formulas_config):
            label = result_labels[i] if i < len(result_labels) else f"formula_{i+1}"
            metric_config.append({"formula": formula, "header": label})
    except Exception as e:
        print(f"[Warning] configs/{scenario_type}.py の読み込みに失敗: {e}")
        
    AW_EXTRACTOR_DIR = os.path.join(LAUNCH_DIR, "AW_Kinematics_Extractor")
    if AW_EXTRACTOR_DIR not in sys.path:
        sys.path.append(AW_EXTRACTOR_DIR)
        
    results = {}
    tasks = []
    
    for d in search_dirs:
        d = os.path.expanduser(d)
        if not os.path.isdir(d): continue
        for f in os.listdir(d):
            if f.endswith('.json') and '_eval_sim' in f and 'footage' not in f:
                filepath = os.path.join(d, f)
                tasks.append((filepath, mode, metric_config, target_npcs))
                
    if tasks:
        num_workers = max(1, multiprocessing.cpu_count() - 1)
        with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as executor:
            for loop_num, metrics in executor.map(_process_single_json, tasks):
                if loop_num is not None and metrics is not None:
                    results[loop_num] = metrics
    return results

def worker_mode(mode, worker_dirs, scenario_type):
    """[ワーカー用処理] コンテナ内で実行され、結果だけをJSON文字列として標準出力に返す"""
    results = run_local_analysis(mode, worker_dirs, scenario_type)
    print("===WORKER_RESULT_START===")
    print(json.dumps(results))
    print("===WORKER_RESULT_END===")
    sys.exit(0)

def parse_worker_output(stdout):
    """ワーカーから返ってきた標準出力の中から結果のJSONだけを抜き取る"""
    match = re.search(r'===WORKER_RESULT_START===\n(.*?)\n===WORKER_RESULT_END===', stdout, re.DOTALL)
    if match:
        try:
            raw_dict = json.loads(match.group(1))
            return {int(k): v for k, v in raw_dict.items()}
        except Exception:
            return {}
    return {}

def get_results_from_node(node_info, scenario_type, mode, suffix=""):
    """各ノードに対してSSH経由で一時コンテナを立ち上げ、並列解析を実行させる"""
    if not node_info.get("enabled", True):
        return {}
        
    ip = node_info.get("ip")
    user = node_info.get("user", "passd")
    machine = node_info.get("machine", "Unknown")
    c_info = node_info.get("container", {})
    c_name = c_info.get("name", "sim_worker")
    c_user = c_info.get("user", "passd")
    c_image = c_info.get("image", "autoware_internal:2026")
    c_home = c_info.get("workspace", "/home/passd")
    
    if ip == cluster_config.MASTER_IP:
        local_traces_dir1 = f"~/simulation_traces_{c_name}{suffix}"
        local_traces_dir2 = f"~/simulation_traces_host{suffix}"
        dirs_str = f"{local_traces_dir1},{local_traces_dir2}"
        print(f"  -> {machine} (自機) でローカル解析を実行中 (対象: {dirs_str})...")
        cmd = f"python3 {LAUNCH_DIR}/fix_dataset_labels.py --worker_mode --mode {mode} --worker_dirs {dirs_str} --type {scenario_type}"
        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            metrics = parse_worker_output(res.stdout)
            print(f"  -> [OK] {machine}: {len(metrics)} 件の解析結果を受信しました")
            if len(metrics) == 0 and res.stderr:
                print(f"      [警告] {machine} のエラー出力:\n{res.stderr.strip()}")
            return metrics
        except Exception as e:
            print(f"  -> [エラー] マスター機での解析に失敗: {e}")
            return {}
    else:
        remote_traces_dir = f"~/simulation_traces_{c_name}{suffix}"
        print(f"  -> {machine} ({ip}) に一時コンテナを立ち上げて並列解析を実行中 ({remote_traces_dir})...")
        # --rm を付けつつ、元のコンテナ設定に合わせて環境変数 HOME や共有メモリサイズを指定
        docker_cmd = (
            f"docker run --rm --user {c_user} --shm-size=32gb -e HOME={c_home} -v ~/AWSIM_launch:{c_home}/AWSIM_launch "
            f"-v {remote_traces_dir}:{c_home}/simulation_traces {c_image} "
            f"python3 {c_home}/AWSIM_launch/fix_dataset_labels.py --worker_mode --mode {mode} --worker_dirs {c_home}/simulation_traces --type {scenario_type}"
        )
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", f"{user}@{ip}", docker_cmd]
        
        try:
            res = subprocess.run(ssh_cmd, capture_output=True, text=True)
            metrics = parse_worker_output(res.stdout)
            print(f"  -> [OK] {machine}: {len(metrics)} 件の解析結果を受信しました")
            if len(metrics) == 0 and res.stderr:
                print(f"      [警告] {machine} のエラー出力:\n{res.stderr.strip()}")
            return metrics
        except Exception as e:
            print(f"  -> [エラー] {machine} での解析に失敗: {e}")
            return {}

def fix_dataset(csv_path, all_metrics):
    if not os.path.exists(csv_path):
        print(f"[Info] {csv_path} は存在しないためスキップします。")
        return

    print(f"\n[Process] {os.path.basename(csv_path)} の修復を開始します...")
    
    # 文字列として読み込む（小数点のフォーマット等を壊さないため）
    df = pd.read_csv(csv_path, dtype=str)
    
    # 修正対象のラベル列を見つける
    target_headers = [col for col in df.columns if col == "c_collision" or col.startswith("c_ttc_")]
    if not target_headers:
        print("  -> 修正対象のラベル列(c_collision, c_ttc_*)が見つかりません。")
        return

    updated_count = 0
    missing_json_count = 0

    for idx, row in df.iterrows():
        try:
            loop_num = int(float(row['loop_num']))
        except (ValueError, TypeError):
            continue

        if loop_num not in all_metrics:
            missing_json_count += 1
            continue

        metrics = all_metrics[loop_num]
        min_ttc = metrics.get("min_ttc", -1.0)
            
        df.at[idx, 'min_ttc'] = str(min_ttc)
        df.at[idx, 'min_distance'] = str(metrics.get("min_distance", -1.0))

        # 各ラベルについて最新の抽出器の判定をダイレクトに適用
        for header in target_headers:
            if header in metrics:
                val = int(metrics[header])
            else:
                val = -1
            
            # 物理的衝突の場合は距離を0に補正
            if header == "c_collision" and val == 1:
                df.at[idx, 'min_distance'] = "0.0"

            df.at[idx, header] = str(val)

        updated_count += 1

    # 新しい名前で保存 (元のファイルを上書きしない)
    base_name, ext = os.path.splitext(csv_path)
    output_path = f"{base_name}_fixed{ext}"
    
    df.to_csv(output_path, index=False)
    print(f"[Success] 修復されたデータを {os.path.basename(output_path)} として保存しました！")
    print(f"  -> 修復したデータ: {updated_count} 件")
    if missing_json_count > 0:
        print(f"  -> JSONファイルが見つからずスキップしたデータ: {missing_json_count} 件")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="過去の誤ったラベルを最新の抽出ロジックで再計算・修復します")
    parser.add_argument("--type", type=str, default="uturn", help="Scenario type (default: uturn)")
    parser.add_argument("--dir", type=str, default="~/simulation_traces", help="Directory containing dataset CSVs")
    parser.add_argument("--mode", type=str, default="cvm", help="Kinematics Extractor Mode (default: cvm)")
    parser.add_argument("--worker_mode", action="store_true", help="Internal use only")
    parser.add_argument("--worker_dirs", type=str, default="~/simulation_traces", help="Internal use only")
    args = parser.parse_args()

    if args.worker_mode:
        worker_dirs = args.worker_dirs.split(",")
        worker_mode(args.mode, worker_dirs, args.type)

    traces_dir = os.path.expanduser(args.dir).rstrip('/')
    dataset_csv_path = os.path.join(traces_dir, f"{args.type}_dataset.csv")
    base_dataset_csv_path = os.path.join(traces_dir, f"{args.type}_dataset_base.csv")

    base_name = os.path.basename(traces_dir)
    suffix = ""
    if base_name.startswith("simulation_traces_shared"):
        suffix = base_name[len("simulation_traces_shared"):]
    elif base_name.startswith("simulation_traces"):
        suffix = base_name[len("simulation_traces"):]

    print("=== データセットラベル修復ツール ===")
    print(f"シナリオ: {args.type}")
    print(f"対象ディレクトリ: {traces_dir}")
    print(f"抽出モード: {args.mode}")
    print("====================================")

    # ワーカー側で最新の抽出器を使えるように、実行前にコードを全台同期
    sync_code_to_workers()

    print("\n[分散解析] 各号機に一時コンテナを立ち上げ、並列で解析を実行させます...")
    all_metrics = {}
    
    if cluster_config is not None:
        nodes = [node for node in cluster_config.CLUSTER_NODES.values() if node.get("enabled", True)]
        
        # マルチスレッドで全ワーカーにSSHコマンドを同時に発行する
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(nodes)) as executor:
            futures = {executor.submit(get_results_from_node, node, args.type, args.mode, suffix): node for node in nodes}
            
            for future in concurrent.futures.as_completed(futures):
                try:
                    res = future.result()
                    all_metrics.update(res)
                except Exception as e:
                    print(f"  -> [エラー] 取得に失敗: {e}")
    else:
        print("  -> クラスター設定が見つかりません。ローカルのみで実行します。")
        search_dirs = [f"~/simulation_traces_sim_worker_21{suffix}", f"~/simulation_traces_host{suffix}"]
        all_metrics = run_local_analysis(args.mode, search_dirs, args.type)

    # base(過去分)と現在のCSVの両方を修復
    fix_dataset(base_dataset_csv_path, all_metrics)
    fix_dataset(dataset_csv_path, all_metrics)
    
    print("\nすべての修復作業が完了しました。")