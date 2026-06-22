#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ホストワーカー管理モジュール。

--with_host_worker オプションで指定された際に、
21号機（マスター機）上で直接 run_manager.py を子プロセスとして起動・管理する。
"""

import json
import os
import subprocess
import sys


class HostWorkerManager:
    """ホストワーカーの起動・停止を管理する"""

    def __init__(self):
        self.proc = None
        self.log_path = None

    def start(self, scenario_name, run_mode, ext_mode, dkw_region,
              focus_points=None, dkw_bounds=None,
              dkw_pure_smc=False, dkw_simultaneous=False, headless_host=False):
        """
        ホストワーカーをバックグラウンドで起動する。

        Args:
            scenario_name: シナリオタイプ (uturn, cutin, ...)
            run_mode: 実行モード
            ext_mode: 運動学抽出モード (cvm/ctrv/maude)
            dkw_region: DKW評価領域
            focus_points: フォーカスポイント (JSON文字列 or None)
            dkw_bounds: DKWバウンド (JSON文字列 or None)
            dkw_pure_smc: 純粋SMCモード
            dkw_simultaneous: 同時保証モード
            headless_host: ヘッドレスモード
        """
        if self.proc is not None and self.proc.poll() is None:
            print("[HostWorker] 既にホストワーカーが起動しています。")
            return

        print("\n[HostWorker] ホストモードのワーカー(21号機)をバックグラウンドで起動します...")

        env = os.environ.copy()
        env["ROS_DOMAIN_ID"] = "21"
        env["EXEC_MODE"] = "host"

        log_dir = os.path.expanduser("~/simulation_traces_host")
        os.makedirs(log_dir, exist_ok=True)
        self.log_path = os.path.join(log_dir, "host_worker_console.log")
        log_file = open(self.log_path, "w")

        cmd = [
            "python3", "-u", "run_manager.py",
            "--type", scenario_name,
            "--mode", run_mode,
            "--ext_mode", ext_mode,
            "--dkw_region", dkw_region,
        ]
        if focus_points:
            cmd.extend(["--focus_points", json.dumps(focus_points)])
        if dkw_bounds:
            cmd.extend(["--dkw_bounds", json.dumps(dkw_bounds)])
        if dkw_pure_smc:
            cmd.append("--dkw_pure_smc")
        if dkw_simultaneous:
            cmd.append("--dkw_simultaneous")
        if headless_host:
            cmd.append("--headless")

        self.proc = subprocess.Popen(
            cmd, env=env, stdout=log_file, stderr=subprocess.STDOUT
        )
        print(f"[HostWorker] 起動完了 (PID: {self.proc.pid})")
        print(f"[HostWorker] コンソール出力は {self.log_path} に記録されます。")

    def stop(self, timeout=5):
        """ホストワーカーを安全に停止する"""
        if self.proc is None or self.proc.poll() is not None:
            return

        print("\n[HostWorker] ホストワーカープロセスを終了しています...")
        self.proc.terminate()
        try:
            self.proc.wait(timeout=timeout)
            print(f"[HostWorker] 正常終了しました。")
        except subprocess.TimeoutExpired:
            print(f"[HostWorker] タイムアウトのため強制終了します。")
            self.proc.kill()
            self.proc.wait()
        finally:
            self.proc = None

    @property
    def is_running(self):
        """プロセスが実行中かどうか"""
        return self.proc is not None and self.proc.poll() is None

    @property
    def returncode(self):
        """プロセスが終了している場合の終了コード"""
        if self.proc is None:
            return None
        return self.proc.poll()
