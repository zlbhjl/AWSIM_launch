#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import pandas as pd
import numpy as np
import os
import sys
import importlib

# パスの追加
LAUNCH_DIR = os.path.dirname(os.path.abspath(__file__))
if LAUNCH_DIR not in sys.path:
    sys.path.append(LAUNCH_DIR)

from estimator import SafetyEstimator
import point_extractors

def analyze_and_print_dkw(title, df, estimator, target_metric, q, delta, epsilon):
    """指定されたデータフレームに対してDKW評価を行い、結果を整形して表示する"""
    print(f"\n--- {title} (データ件数: {len(df)}) ---")

    summary = estimator.evaluate_and_summarize_dkw(
        target_column=target_metric, df=df, q=q, delta=delta, epsilon=epsilon
    )
    
    if summary["status"] == "error":
        print(f"  -> {summary['message']}")
    else:
        print(f"  - ワースト{q*100:.0f}%の推定値: {summary['estimate']:.3f}")
        print(f"  - 信頼区間 (信頼水準 {(1-delta)*100:.0f}%): [{summary['lower_bound']:.3f}, {summary['upper_bound']:.3f}]")
        print(f"  - 信頼区間幅: {summary['interval_width']:.3f} (目標: <= {epsilon})")

def analyze_and_print_dkw_simultaneous(title, df, estimator, target_metrics, q, delta, epsilon):
    print(f"\n--- {title} (データ件数: {len(df)}) ---")
    summary = estimator.evaluate_and_summarize_dkw_multiple(
        target_columns=target_metrics, df=df, q=q, delta_total=delta, epsilon=epsilon
    )
    if summary["status"] == "error":
        print(f"  -> {summary['message']}")
    else:
        print(f"  [同時保証] 全体エラー予算 {delta*100:.1f}% を {len(target_metrics)} 指標に分割 (個別信頼水準 {(1 - delta/len(target_metrics))*100:.2f}%)")
        for metric, res in summary["metrics"].items():
            print(f"  👉 [{metric}] ワースト{q*100:.0f}%推定: {res['estimate']:.3f} | 区間: [{res['lower_bound']:.3f}, {res['upper_bound']:.3f}] (幅: {res['interval_width']:.3f})")

def main():
    parser = argparse.ArgumentParser(description="反復テストデータからTTCの安定性を評価し、分類ごとにDKW評価を実行します。")
    parser.add_argument("--type", type=str, default="uturn", help="シナリオタイプ (例: uturn)")
    parser.add_argument("--dir", type=str, default="~/simulation_traces", help="データセットが存在するディレクトリ")
    parser.add_argument("--region", type=str, default="custom", help="評価対象を絞り込む条件式 (例: 'emp_safe and min_ttc < 1.5')")
    parser.add_argument("--consistency_threshold", type=float, default=0.2, help="TTCの標準偏差がこの値以下の場合「確実」と分類する閾値")
    parser.add_argument("--min_repeats", type=int, default=3, help="分析対象とする最低反復回数")
    parser.add_argument("--dkw_simultaneous", action="store_true", help="ボンフェローニ補正を用いた複数指標の同時保証評価を行う")
    
    args = parser.parse_args()

    # --- 1. 設定とデータ読み込み ---
    try:
        config_module = importlib.import_module(f"configs.{args.type}")
        param_names = list(config_module.PARAM_RANGES.keys())
    except ImportError:
        print(f"[エラー] 設定ファイル configs/{args.type}.py が見つかりません。")
        sys.exit(1)

    traces_dir = os.path.expanduser(args.dir)
    dataset_file = os.path.join(traces_dir, f"{args.type}_dataset.csv")
    if not os.path.exists(dataset_file):
        print(f"[エラー] データセットが見つかりません: {dataset_file}")
        sys.exit(1)
        
    print(f"データセットを読み込み中: {dataset_file}")
    df = pd.read_csv(dataset_file, engine='python', on_bad_lines='skip')
    
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors='ignore')

    initial_count = len(df)
    
    try:
        df = point_extractors.filter_by_region_and_bounds(df, region=args.region)
        print(f"条件 '{args.region}' で抽出しました: {len(df)} / {initial_count} 行")
    except Exception as e:
        print(f"[エラー] {e}"); sys.exit(1)
    
    if df.empty: print("[警告] 指定された条件に合致するデータがありません。"); sys.exit(1)

    target_metric = getattr(config_module, 'DKW_TARGET_METRIC', 'min_ttc')
    print(f"\nパラメータでグループ化し、指標 '{target_metric}' の標準偏差 <= {args.consistency_threshold} で分類します...")
    
    df_consistent, df_stochastic = point_extractors.classify_consistency(
        df, param_names, target_metric=target_metric, threshold=args.consistency_threshold, min_repeats=args.min_repeats
    )
    print(f"分類完了: 確実な領域={len(df_consistent)}件, 偶然の領域={len(df_stochastic)}件")
    
    # --- [追加] 分類されたデータを個別のCSVとして保存 ---
    out_path_consistent = os.path.join(traces_dir, f"{args.type}_consistent_risk.csv")
    point_extractors.save_dataframe_to_csv(df_consistent, out_path_consistent, f"💾 確実なリスク領域のデータを保存しました: {out_path_consistent}")
    out_path_stochastic = os.path.join(traces_dir, f"{args.type}_stochastic_risk.csv")
    point_extractors.save_dataframe_to_csv(df_stochastic, out_path_stochastic, f"💾 偶然のリスク領域のデータを保存しました: {out_path_stochastic}")

    # --- 4. 分類ごとにDKW評価を実行 ---
    estimator = SafetyEstimator(args.type, config_module)
    if args.dkw_simultaneous:
        target_metrics = getattr(config_module, 'DKW_TARGET_METRICS', ['min_ttc', 'min_distance'])
        dkw_params = { "target_metrics": target_metrics, "q": 0.05, "delta": getattr(config_module, 'DKW_TOTAL_DELTA', 0.05), "epsilon": getattr(config_module, 'DKW_TARGET_EPSILON', 0.15) }
        analyze_and_print_dkw_simultaneous("確実なリスク領域 (Consistent Risk)", df_consistent, estimator, **dkw_params)
        analyze_and_print_dkw_simultaneous("偶然のリスク領域 (Stochastic Risk)", df_stochastic, estimator, **dkw_params)
    else:
        dkw_params = { "target_metric": target_metric, "q": 0.05, "delta": getattr(config_module, 'DKW_TOTAL_DELTA', 0.05), "epsilon": getattr(config_module, 'DKW_TARGET_EPSILON', 0.15) }
        analyze_and_print_dkw("確実なリスク領域 (Consistent Risk)", df_consistent, estimator, **dkw_params)
        analyze_and_print_dkw("偶然のリスク領域 (Stochastic Risk)", df_stochastic, estimator, **dkw_params)

if __name__ == "__main__":
    main()