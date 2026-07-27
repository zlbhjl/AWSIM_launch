#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pandas as pd
import re
import os
import numpy as np


def _build_boundary_gap_binning(df, param_names, config):
    grid_size = getattr(config, 'BOUNDARY_GAP_GRID_SIZE', 8)
    bounds = getattr(config, 'PARAM_RANGES', {})

    working_df = df.copy()
    edge_cols = []
    for name in param_names:
        if name not in bounds:
            continue
        low, high = bounds[name]
        bins = np.linspace(low, high, grid_size + 1)
        working_df[f'__bin_{name}'] = pd.cut(
            working_df[name], bins=bins, include_lowest=True, labels=False
        )
        edge_cols.append((name, bins))

    bin_cols = [f'__bin_{name}' for name, _ in edge_cols]
    working_df = working_df.dropna(subset=bin_cols)
    return working_df, edge_cols, bin_cols


def _score_boundary_gap_candidate(row):
    return (
        abs(float(row['collision_ratio']) - 0.5) * -1.0,
        float(row['near_ratio']),
        -int(row['sample_count'])
    )


def _classify_boundary_gap_status(row, config):
    min_samples = getattr(config, 'BOUNDARY_GAP_MIN_SAMPLES', 2)
    max_samples = getattr(config, 'BOUNDARY_GAP_MAX_SAMPLES', 12)
    collision_ratio_range = getattr(config, 'BOUNDARY_GAP_COLLISION_RATIO_RANGE', (0.15, 0.85))

    support = int(row['sample_count'])
    collision_ratio = float(row['collision_ratio'])
    near_ratio = float(row['near_ratio'])
    risky = (
        collision_ratio_range[0] <= collision_ratio <= collision_ratio_range[1]
        or near_ratio >= 0.5
    )

    if support < min_samples:
        return "undersampled"
    if risky and support <= max_samples:
        return "needs_more_data"
    if risky and support > max_samples:
        return "densified"
    return "clarified"


def collect_boundary_gap_cells(df, param_names, config):
    min_samples = getattr(config, 'BOUNDARY_GAP_MIN_SAMPLES', 2)
    max_samples = getattr(config, 'BOUNDARY_GAP_MAX_SAMPLES', 12)
    collision_ratio_range = getattr(config, 'BOUNDARY_GAP_COLLISION_RATIO_RANGE', (0.15, 0.85))
    near_ttc_threshold = getattr(config, 'BOUNDARY_GAP_TTC_THRESHOLD', 1.1)

    clean_df = _clean_dataframe(df, ['c_collision', 'min_ttc'] + param_names)
    if clean_df is None or clean_df.empty:
        return pd.DataFrame()

    working_df = clean_df.copy()
    working_df = working_df[working_df['c_collision'].isin([0, 1])]
    if working_df.empty:
        return pd.DataFrame()

    working_df, edge_cols, bin_cols = _build_boundary_gap_binning(working_df, param_names, config)
    if working_df.empty or not edge_cols:
        return pd.DataFrame()

    rows = []
    grouped = working_df.groupby(bin_cols, dropna=False)
    for bin_key, group_df in grouped:
        if not isinstance(bin_key, tuple):
            bin_key = (bin_key,)

        support = len(group_df)
        collision_ratio = float((group_df['c_collision'] == 1).mean())
        near_ratio = float((group_df['min_ttc'] <= near_ttc_threshold).mean())
        is_candidate = (
            min_samples <= support <= max_samples and
            (
                collision_ratio_range[0] <= collision_ratio <= collision_ratio_range[1]
                or near_ratio >= 0.5
            )
        )

        row = {
            'sample_count': int(support),
            'collision_ratio': collision_ratio,
            'near_ratio': near_ratio,
            'is_candidate': bool(is_candidate),
        }
        for idx, (name, bins) in enumerate(edge_cols):
            bin_index = int(bin_key[idx])
            bin_index = max(0, min(bin_index, len(bins) - 2))
            center = float((bins[bin_index] + bins[bin_index + 1]) / 2.0)
            row[f'__bin_{name}'] = bin_index
            row[name] = center

        row['status'] = _classify_boundary_gap_status(row, config)
        row['priority_score'] = _score_boundary_gap_candidate(row)
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    cell_df = pd.DataFrame(rows)
    cell_df = cell_df.sort_values(
        by=['is_candidate', 'collision_ratio', 'near_ratio', 'sample_count'],
        ascending=[False, True, False, True]
    ).reset_index(drop=True)
    return cell_df


def _point_to_boundary_gap_key(point, param_names, config):
    bounds = getattr(config, 'PARAM_RANGES', {})
    grid_size = getattr(config, 'BOUNDARY_GAP_GRID_SIZE', 8)
    key = []
    for name in param_names:
        if name not in bounds:
            continue
        low, high = bounds[name]
        bins = np.linspace(low, high, grid_size + 1)
        raw_index = np.digitize([float(point[name])], bins[1:-1], right=False)[0]
        bin_index = int(max(0, min(raw_index, len(bins) - 2)))
        key.append(bin_index)
    return tuple(key)


def summarize_boundary_gap_progress(df, param_names, config, target_points=None):
    cell_df = collect_boundary_gap_cells(df, param_names, config)
    if cell_df.empty:
        return {
            "total_cells": 0,
            "candidate_cells": 0,
            "needs_more_data_cells": 0,
            "densified_cells": 0,
            "clarified_cells": 0,
            "target_cells": [],
            "cell_df": cell_df,
        }

    key_cols = [f'__bin_{name}' for name in param_names if f'__bin_{name}' in cell_df.columns]
    summary = {
        "total_cells": int(len(cell_df)),
        "candidate_cells": int(cell_df['is_candidate'].sum()),
        "needs_more_data_cells": int((cell_df['status'] == 'needs_more_data').sum()),
        "densified_cells": int((cell_df['status'] == 'densified').sum()),
        "clarified_cells": int((cell_df['status'] == 'clarified').sum()),
        "target_cells": [],
        "cell_df": cell_df,
    }

    if not target_points:
        return summary

    indexed = {
        tuple(int(row[col]) for col in key_cols): row
        for _, row in cell_df.iterrows()
    }
    target_rows = []
    for point in target_points:
        key = _point_to_boundary_gap_key(point, param_names, config)
        row = indexed.get(key)
        if row is None:
            target_rows.append({
                **{name: float(point[name]) for name in param_names if name in point},
                "sample_count": 0,
                "collision_ratio": np.nan,
                "near_ratio": np.nan,
                "status": "missing",
                "is_candidate": False,
            })
            continue

        target_rows.append({
            **{name: float(row[name]) for name in param_names if name in row},
            "sample_count": int(row['sample_count']),
            "collision_ratio": float(row['collision_ratio']),
            "near_ratio": float(row['near_ratio']),
            "status": row['status'],
            "is_candidate": bool(row['is_candidate']),
        })

    summary["target_cells"] = target_rows
    return summary

def _clean_dataframe(df, required_cols):
    """指定された列が存在するかチェックし、数値に変換して欠損値を除外する共通処理"""
    if df is None or df.empty:
        return None
    for col in required_cols:
        if col not in df.columns:
            return None
    
    clean_df = df.copy()
        
    for col in required_cols:
        clean_df[col] = pd.to_numeric(clean_df[col], errors='coerce')
    return clean_df.dropna(subset=required_cols)

def _extract_points(df, param_names, max_cases=None):
    """データフレームからパラメータの辞書リストを抽出する共通処理"""
    extracted_points = []
    for _, row in df.iterrows():
        point = {name: float(row[name]) for name in param_names if name in row}
        if len(point) == len(param_names) and point not in extracted_points:
            extracted_points.append(point)
        if max_cases is not None and len(extracted_points) >= max_cases:
            break
    return extracted_points

def extract_jama_edge(df, param_names, config):
    print("[Extractor] 🔍 JAMAエッジ探索モード: 過去のデータセットから人間の安全境界に近い事故を抽出します...")
    clean_df = _clean_dataframe(df, ['c_collision', 'theory_margin_a_human'] + param_names)
    if clean_df is None:
        print("[Extractor] ⚠️ データセットが存在しないか、必要な列がありません。")
        return []
    edge_df = clean_df[(clean_df['c_collision'] == 1) & (clean_df['theory_margin_a_human'] > 0.0)]
    return _extract_points(edge_df, param_names)

def extract_ttc_edge(df, param_names, config):
    ttc_threshold = getattr(config, 'TTC_EDGE_THRESHOLD', 1.5)
    print(f"[Extractor] 🔍 TTCエッジ探索モード: 人間なら安全領域でTTC {ttc_threshold}秒以下のニアミスを抽出します...")
    clean_df = _clean_dataframe(df, ['c_collision', 'min_ttc', 'theory_margin_a_human'] + param_names)
    if clean_df is None:
        print("[Extractor] ⚠️ データセットが存在しないか、必要な列がありません。")
        return []
    edge_df = clean_df[(clean_df['c_collision'] == 0) & (clean_df['min_ttc'] <= ttc_threshold) & (clean_df['theory_margin_a_human'] > 0.0)]
    return _extract_points(edge_df, param_names)

def extract_worst_ttc(df, param_names, config):
    num_worst_cases = getattr(config, 'WORST_TTC_CASES', 10)
    print(f"[Extractor] 🔍 最悪TTC探索モード: 安全領域(衝突なし)内でTTCが最悪の{num_worst_cases}件を抽出し、集中検証します...")
    clean_df = _clean_dataframe(df, ['c_collision', 'min_ttc'] + param_names)
    if clean_df is None:
        print("[Extractor] ⚠️ データセットが存在しないか、必要な列がありません。")
        return []
    safe_df = clean_df[(clean_df['c_collision'] == 0) & (clean_df['min_ttc'] >= 0.0)]
    worst_df = safe_df.sort_values(by='min_ttc', ascending=True)
    return _extract_points(worst_df, param_names, max_cases=num_worst_cases)

def extract_verify_consistency(df, param_names, config):
    ttc_threshold = getattr(config, 'TTC_EDGE_THRESHOLD', 1.5)
    num_cases = getattr(config, 'WORST_TTC_CASES', 10)
    print(f"[Extractor] 🔍 一貫性検証モード: 過去データからTTC {ttc_threshold}秒以下のニアミスを上位 {num_cases} 件抽出し、自動反復テストを開始します...")
    clean_df = _clean_dataframe(df, ['c_collision', 'min_ttc'] + param_names)
    if clean_df is None:
        print("[Extractor] ⚠️ データセットが存在しないか、必要な列がありません。")
        return []
    safe_df = clean_df[(clean_df['c_collision'] == 0) & (clean_df['min_ttc'] >= 0.0) & (clean_df['min_ttc'] <= ttc_threshold)]
    worst_df = safe_df.sort_values(by='min_ttc', ascending=True)
    return _extract_points(worst_df, param_names, max_cases=num_cases)

def extract_boundary_gap(df, param_names, config):
    max_cases = getattr(config, 'BOUNDARY_GAP_MAX_CASES', 12)

    print(
        "[Extractor] 🔍 境界ギャップ探索モード: "
        "危険寄りだがサンプルの薄い領域を自動抽出します..."
    )
    cell_df = collect_boundary_gap_cells(df, param_names, config)
    if cell_df.empty:
        print("[Extractor] ⚠️ データセットが存在しないか、必要な列がありません。")
        return []

    extracted_points = []
    candidate_df = cell_df[cell_df['is_candidate']].copy()
    for _, row in candidate_df.head(max_cases).iterrows():
        point = {name: float(row[name]) for name in param_names if name in row}
        support = int(row['sample_count'])
        collision_ratio = float(row['collision_ratio'])
        near_ratio = float(row['near_ratio'])
        if point not in extracted_points:
            extracted_points.append(point)
            print(
                f"[Extractor]   -> point={point} | samples={support} "
                f"| collision_ratio={collision_ratio:.2f} | near_ratio={near_ratio:.2f}"
            )
        if len(extracted_points) >= max_cases:
            break

    if not extracted_points:
        print("[Extractor] ⚠️ 境界が薄い候補セルは見つかりませんでした。")
    return extracted_points

def filter_by_region_and_bounds(df, region="custom", bounds=None):
    """MACROSを用いた条件式(region)および矩形範囲(bounds)でデータを抽出・フィルタリングする"""
    if df is None or df.empty:
        return df

    filtered_df = df.copy()

    if region and region != "custom":
        MACROS = {
            "emp_safe": "(c_collision == 0)",
            "jama_safe": "(theory_margin_a_human >= 0.0)",
            "intersect_safe": "((c_collision == 0) and (theory_margin_a_human >= 0.0))",
            "union_safe": "((c_collision == 0) or (theory_margin_a_human >= 0.0))"
        }
        query_str = region
        for key, val in MACROS.items():
            query_str = re.sub(rf'\b{key}\b', val, query_str)
            
        try:
            # 評価指標やパラメータの列は強制的に数値化する。純粋な文字列の列は保護する
            string_cols = {'reason', 'theory_zone_a', 'theory_zone_b', 'worker_id', 'filename', 'npc_id'}
            for col in filtered_df.columns:
                if col not in string_cols:
                    # errors='coerce' により、空文字("")等の不正な値はNaNになり安全に計算できる
                    # .loc を使って明示的に代入することで SettingWithCopyWarning を防ぐ
                    filtered_df.loc[:, col] = pd.to_numeric(filtered_df[col], errors='coerce')
            # スライスの警告を防ぐため、query抽出後に明示的にコピーを作成する
            filtered_df = filtered_df.query(query_str).copy()
        except Exception as e:
            raise ValueError(f"条件式の評価に失敗しました: {e}")

    if bounds:
        for col, (min_val, max_val) in bounds.items():
            if col in filtered_df.columns:
                # .loc を使って明示的に代入することで SettingWithCopyWarning を防ぐ
                filtered_df.loc[:, col] = pd.to_numeric(filtered_df[col], errors='coerce')
                filtered_df = filtered_df[(filtered_df[col] >= min_val) & (filtered_df[col] <= max_val)]

    return filtered_df

def classify_consistency(df, param_names, target_metric='min_ttc', threshold=0.2, min_repeats=2):
    """指定されたターゲット指標の標準偏差に基づいて、確実なリスク(Consistent)と偶然のリスク(Stochastic)に分類する"""
    if df is None or df.empty:
        return pd.DataFrame(), pd.DataFrame()
        
    df = df.copy() # [追加] スライスの警告を防ぐためにコピーを作成

    # [追加] 完全にエラーとなったデータ (c_collision == -1) を事前に計算から除外する
    if 'c_collision' in df.columns:
        df = df[~df['c_collision'].isin([-1, "-1", -1.0])]

    for col in param_names + [target_metric]:
        if col in df.columns:
            df.loc[:, col] = pd.to_numeric(df[col], errors='coerce')

    grouped = df.groupby(param_names)
    consistent_dfs, stochastic_dfs = [], []

    for _, group_df in grouped:
        if len(group_df) < min_repeats: continue
        
        valid_data = group_df[target_metric].dropna()
        # 物理的に0未満にならない指標のみマイナス値を弾く
        if target_metric in ['min_ttc', 'min_distance', 'z_margin']:
            valid_data = valid_data[valid_data >= 0]
            
        if len(valid_data) < 2: continue
        if valid_data.std() <= threshold:
            consistent_dfs.append(group_df)
        else:
            stochastic_dfs.append(group_df)

    df_consistent = pd.concat(consistent_dfs) if consistent_dfs else pd.DataFrame(columns=df.columns)
    df_stochastic = pd.concat(stochastic_dfs) if stochastic_dfs else pd.DataFrame(columns=df.columns)
    
    return df_consistent, df_stochastic

def save_dataframe_to_csv(df, output_path, success_msg=None):
    """データフレームをCSVとして安全に保存する共通処理"""
    if df is not None and not df.empty:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        df.to_csv(output_path, index=False)
        if success_msg:
            print(success_msg)
        return True
    return False

EXTRACTORS = {
    "jama_edge": extract_jama_edge,
    "ttc_edge": extract_ttc_edge,
    "worst_ttc": extract_worst_ttc,
    "verify_consistency": extract_verify_consistency,
    "boundary_gap": extract_boundary_gap,
}
