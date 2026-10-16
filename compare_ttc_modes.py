#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import csv
import argparse
import sys
import importlib

# AW_Kinematics_Extractor のパスを通す
LAUNCH_DIR = os.path.dirname(os.path.abspath(__file__))
if LAUNCH_DIR not in sys.path:
    sys.path.append(LAUNCH_DIR)

# AW_Kinematics_Extractor 内部のモジュール(phase1_parserなど)を直接importできるようにパスを追加
AW_EXTRACTOR_DIR = os.path.join(LAUNCH_DIR, "AW_Kinematics_Extractor")
if AW_EXTRACTOR_DIR not in sys.path:
    sys.path.append(AW_EXTRACTOR_DIR)

from AW_Kinematics_Extractor.main import AWKinematicsPipeline

def main():
    parser = argparse.ArgumentParser(description="Compare min_ttc among maude, cvm, and ctrv modes")
    parser.add_argument("--dir", type=str, default="~/simulation_traces", help="Directory containing JSON logs")
    parser.add_argument("--output", type=str, default="ttc_mode_comparison.csv", help="Output CSV file name")
    parser.add_argument("--type", type=str, default="uturn", help="Scenario type (default: uturn)")
    args = parser.parse_args()

    target_dir = os.path.expanduser(args.dir)
    output_csv = os.path.join(target_dir, args.output)

    if not os.path.exists(target_dir):
        print(f"[Error] Directory not found: {target_dir}")
        sys.exit(1)

    # 設定ファイルから対象NPCを動的に読み込む (将来の複数NPC対応)
    try:
        cfg = importlib.import_module(f"configs.{args.type}")
        target_npcs = getattr(cfg, 'TARGET_NPCS', ["npc1"])
    except ImportError:
        print(f"[Warning] configs/{args.type}.py が見つかりません。デフォルトの target_npcs=['npc1'] を使用します。")
        target_npcs = ["npc1"]

    # '_eval_sim' が含まれるJSONファイルを探す ('footage'や無関係なファイルを除外)
    json_files = []
    for f in os.listdir(target_dir):
        if f.endswith('.json') and '_eval_sim' in f and 'footage' not in f:
            match = re.search(r'sim(\d+)', f)
            if match:
                loop_num = int(match.group(1))
                json_files.append((loop_num, os.path.join(target_dir, f)))

    # sim_NUMBER 順にソート
    json_files.sort(key=lambda x: x[0])

    if not json_files:
        print(f"[Error] No valid JSON files found in {target_dir}")
        sys.exit(1)

    print(f"Found {len(json_files)} JSON files in {target_dir}.")
    print("Starting TTC extraction for 'maude', 'cvm', and 'ctrv' modes...\n")

    # すべてのモードのパイプラインを初期化
    pipeline_maude = AWKinematicsPipeline(mode="maude", target_npcs=target_npcs)
    pipeline_cvm = AWKinematicsPipeline(mode="cvm", target_npcs=target_npcs)
    pipeline_ctrv = AWKinematicsPipeline(mode="ctrv", target_npcs=target_npcs)

    results = []
    
    for loop_num, filepath in json_files:
        filename = os.path.basename(filepath)
        print(f"Processing Sim {loop_num} ({filename})...")
        
        min_ttc_maude = pipeline_maude.get_metrics(filepath).get("min_ttc", float('inf'))
        min_ttc_cvm = pipeline_cvm.get_metrics(filepath).get("min_ttc", float('inf'))
        min_ttc_ctrv = pipeline_ctrv.get_metrics(filepath).get("min_ttc", float('inf'))

        # CVM と CTRV の差分を計算 (直進予測がいかに過剰だったかを見るため)
        diff_cvm_ctrv = ""
        if min_ttc_cvm != float('inf') and min_ttc_ctrv != float('inf'):
            diff_cvm_ctrv = round(abs(min_ttc_cvm - min_ttc_ctrv), 4)

        print(f"  -> Maude: {min_ttc_maude}s | CVM: {min_ttc_cvm}s | CTRV: {min_ttc_ctrv}s | Diff(CVM-CTRV): {diff_cvm_ctrv}")

        results.append({
            "sim_number": loop_num,
            "maude_min_ttc": min_ttc_maude,
            "cvm_min_ttc": min_ttc_cvm,
            "ctrv_min_ttc": min_ttc_ctrv,
            "diff_cvm_ctrv": diff_cvm_ctrv,
            "filename": filename
        })

    # CSV書き込み
    headers = ["sim_number", "maude_min_ttc", "cvm_min_ttc", "ctrv_min_ttc", "diff_cvm_ctrv", "filename"]
    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(results)

    print(f"\n[Success] Comparison completed! Results saved to: {output_csv}")

if __name__ == "__main__":
    main()