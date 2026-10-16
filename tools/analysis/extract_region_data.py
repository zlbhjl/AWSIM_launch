#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

import point_extractors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="データセットから特定の領域(Bounds)や条件のデータのみを抽出して出力します。"
    )
    parser.add_argument(
        "--type", type=str, default="uturn", help="シナリオタイプ (例: uturn)"
    )
    parser.add_argument(
        "--dir",
        type=str,
        default="~/simulation_traces",
        help="データセットが存在するディレクトリ",
    )
    parser.add_argument(
        "--bounds",
        type=str,
        default=None,
        help="手動で領域を指定するJSON (例: '{\"dx0\": [15.0, 20.0]}')",
    )
    parser.add_argument(
        "--region",
        type=str,
        default="custom",
        help="抽出条件式 (例: 'emp_safe and jama_safe', 'min_ttc > 1.0 or c_collision == 1')",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="extracted_dataset.csv",
        help="出力するCSVのファイル名",
    )
    return parser


def run_extraction(
    scenario_type: str,
    traces_dir: str,
    region: str,
    bounds_json: str | None,
    output_name: str,
) -> dict:
    traces_dir = os.path.expanduser(traces_dir)
    dataset_file = os.path.join(traces_dir, f"{scenario_type}_dataset.csv")

    if not os.path.exists(dataset_file):
        raise FileNotFoundError(f"データセットが見つかりません: {dataset_file}")

    df = pd.read_csv(dataset_file, engine="python", on_bad_lines="skip")
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="ignore")

    initial_count = len(df)
    bounds_dict = None
    if bounds_json:
        try:
            bounds_dict = json.loads(bounds_json)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"--bounds 引数のJSONパースに失敗しました: {exc}"
            ) from exc

    filtered_df = point_extractors.filter_by_region_and_bounds(
        df, region=region, bounds=bounds_dict
    )
    out_path = os.path.join(traces_dir, output_name)

    if not filtered_df.empty:
        point_extractors.save_dataframe_to_csv(
            filtered_df,
            out_path,
            f"[成功] 抽出したデータを保存しました: {out_path}",
        )

    return {
        "dataset_file": dataset_file,
        "initial_count": initial_count,
        "filtered_df": filtered_df,
        "out_path": out_path,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        result = run_extraction(
            scenario_type=args.type,
            traces_dir=args.dir,
            region=args.region,
            bounds_json=args.bounds,
            output_name=args.output,
        )
    except FileNotFoundError as exc:
        print(f"[エラー] {exc}")
        return 1
    except ValueError as exc:
        print(f"[エラー] {exc}")
        return 1
    except Exception as exc:
        print(f"[エラー] {exc}")
        return 1

    print(f"データセットを読み込み中: {result['dataset_file']}")
    print(
        f"抽出条件を適用しました: "
        f"{len(result['filtered_df'])} / {result['initial_count']} 行"
    )

    df = result["filtered_df"]
    if df.empty:
        print(
            "[警告] 指定された条件に合致するデータが1件もありませんでした。"
            "ファイルは作成されません。"
        )
        return 0

    print("\n=== 抽出データ サマリー ===")
    print(f"データ件数: {len(df)} 件")

    if "c_collision" in df.columns:
        col_count = (df["c_collision"] == 1).sum()
        print(f"衝突回数  : {col_count} 件 ({col_count/len(df)*100:.1f}%)")

    if "min_ttc" in df.columns:
        valid_ttc = df[df["min_ttc"] >= 0]
        if not valid_ttc.empty:
            print(f"平均 TTC  : {valid_ttc['min_ttc'].mean():.3f} 秒")
            print(f"最小 TTC  : {valid_ttc['min_ttc'].min():.3f} 秒")
    print("===========================\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
