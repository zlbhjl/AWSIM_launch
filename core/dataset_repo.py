#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
データセット永続化アクセスモジュール。

シミュレーション結果のCSVファイルへの読み書き、過去データの復元・退避を担当する。
ファイルI/Oをこのレイヤーに閉じ込めることで、変更やテストを容易にする。
"""

import csv
import os
import shutil


class DatasetRepository:
    """データセットCSVファイルへのアクセスを提供する"""

    def __init__(self, scenario_name, traces_dir="~/simulation_traces"):
        self.scenario_name = scenario_name
        self.traces_dir = os.path.expanduser(traces_dir)
        self.base_csv = os.path.join(self.traces_dir, f"{scenario_name}_dataset_base.csv")
        self.dataset_csv = os.path.join(self.traces_dir, f"{scenario_name}_dataset.csv")

    def restore_base_dataset(self, resume_from):
        """
        --resume_from で指定された過去データを _base.csv として復元する。

        Args:
            resume_from: 復元元ディレクトリのパス (None の場合は何もしない)

        Returns:
            bool: 復元を行ったかどうか
        """
        if not resume_from:
            return False

        src_csv = os.path.expanduser(
            f"{resume_from}/{self.scenario_name}_dataset.csv"
        )
        if not os.path.exists(src_csv):
            print(f"[Fatal] 復元元のデータセットが見つかりません: {src_csv}")
            raise FileNotFoundError(f"Dataset not found: {src_csv}")

        os.makedirs(self.traces_dir, exist_ok=True)
        shutil.copy2(src_csv, self.base_csv)
        print(f"[Dataset] 📂 過去の退避データ ({src_csv}) を復元しました。")
        return True

    def get_last_loop_num(self):
        """
        全データセット(_base.csv + _dataset.csv)から最大ループ番号を取得する。

        Returns:
            int: 最大ループ番号（データがない場合は0）
        """
        return self._max_loop_from_files([self.base_csv, self.dataset_csv])

    def get_last_loop_num_from_current(self):
        """
        今回の実行分(_dataset.csv)のみから最大ループ番号を取得する。
        dkw_fixed モード等で resume_from の過去データを除外したい場合に使用する。

        Returns:
            int: 最大ループ番号（データがない場合は0）
        """
        return self._max_loop_from_files([self.dataset_csv])

    @staticmethod
    def _max_loop_from_files(csv_paths):
        """指定されたCSVファイル群から最大の loop_num を取得する"""
        last_loop = 0
        for csv_path in csv_paths:
            if not os.path.exists(csv_path):
                continue
            try:
                with open(csv_path, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        try:
                            loop_num = int(row.get("loop_num", 0))
                            if loop_num > last_loop:
                                last_loop = loop_num
                        except (ValueError, TypeError):
                            pass
            except Exception:
                pass
        return last_loop
