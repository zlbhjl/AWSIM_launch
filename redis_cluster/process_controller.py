#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
プロセス制御モジュール。

AWSIM/Autoware/RuntimeMonitor/AWChecker の起動・終了・監視、
Xvfb仮想ディスプレイ設定など、OSベッタリな処理をカプセル化する。
"""

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class InfraTask:
    """起動するインフラタスクの定義"""
    name: str
    work_dir: str
    command: str
    delay: int = 2
    source_setup: bool = False
    resident: bool = False


class ProcessController:
    """AWSIM/Autoware等のプロセス起動・終了を管理する"""

    def __init__(self, headless_mode: bool = False):
        self.infra_procs: List[Tuple[str, subprocess.Popen]] = []
        self.resident_procs: List[Tuple[str, subprocess.Popen]] = []
        self.client_proc: Optional[subprocess.Popen] = None
        self.xvfb_proc: Optional[subprocess.Popen] = None
        self.headless_mode = headless_mode

    def setup_xvfb(self) -> bool:
        """ヘッドレスモード用のXvfb仮想ディスプレイを起動する"""
        if not self.headless_mode:
            return False
        print("\n[ProcessCtrl] 🖥️ ヘッドレスモード (Xvfb) を有効化します。")
        os.system("pkill -9 -f 'Xvfb :199' > /dev/null 2>&1")
        try:
            self.xvfb_proc = subprocess.Popen(
                ["Xvfb", ":199", "-screen", "0", "1920x1080x24"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            os.environ["DISPLAY"] = ":199"
            os.environ["VK_ICD_FILENAMES"] = "/usr/share/vulkan/icd.d/nvidia_icd.json"
            time.sleep(2)
            print("[ProcessCtrl] Xvfb起動完了")
            return True
        except Exception as e:
            print(f"[ProcessCtrl] Xvfb起動失敗: {e}")
            return False

    def build_command(self, task: InfraTask, sim_num: int, setup_bash: str) -> str:
        """タスクのコマンド文字列を組み立てる"""
        cmd = task.command.replace("{sim_num}", str(sim_num))
        if task.source_setup:
            cmd = f"source {setup_bash} && {cmd}"
        return cmd

    def start_process(self, task: InfraTask, sim_num: int, output_dir: str,
                      setup_bash: str) -> Optional[subprocess.Popen]:
        """1つのインフラタスクを起動する"""
        full_cmd = self.build_command(task, sim_num, setup_bash)
        print(f"  [起動] {task.name} ... ", end="", flush=True)

        out_target = subprocess.DEVNULL
        if task.name == "AWSIM Labs":
            out_target = open(os.path.join(output_dir, "awsim.log"), "w")
        elif task.name == "Autoware":
            out_target = open(os.path.join(output_dir, "autoware.log"), "w")
        elif task.name == "AW Checker (Safety Evaluator)":
            out_target = open(os.path.join(output_dir, "awchecker_error.log"), "w")

        try:
            proc = subprocess.Popen(
                ["/bin/bash", "-i", "-c", full_cmd],
                cwd=task.work_dir,
                preexec_fn=os.setsid,
                stdout=out_target,
                stderr=out_target
            )
            print(f"OK (PID: {proc.pid}) -> {task.delay}秒待機")
            if task.delay > 0:
                time.sleep(task.delay)
            return proc
        except Exception as e:
            print(f"失敗: {e}")
            return None

    def start_all_infra(self, infra_tasks: List[InfraTask], output_dir: str,
                        setup_bash: str, first_boot: bool = False) -> None:
        """全インフラタスクを起動する"""
        print("\n--- システムインフラ起動 ---")
        for task in infra_tasks:
            if task.resident:
                if not any(name == task.name for name, _ in self.resident_procs):
                    p = self.start_process(task, 1, output_dir, setup_bash)
                    if p:
                        self.resident_procs.append((task.name, p))
            else:
                p = self.start_process(task, 1, output_dir, setup_bash)
                if p:
                    self.infra_procs.append((task.name, p))

        if first_boot:
            print("  [System] 初回起動: AutowareマップロードとDDS確立を追加待機 (40秒)...")
            time.sleep(40)

    def launch_scenario(self, cmd: str, work_dir: str) -> None:
        """シナリオ実行コマンドを起動する（既存のクライアントは殺す）"""
        self.kill_client()
        print(f"  [入力] Runner コマンド実行")
        try:
            self.client_proc = subprocess.Popen(
                ["/bin/bash", "-i", "-c", cmd],
                cwd=work_dir,
                preexec_fn=os.setsid,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception as e:
            print(f"  [エラー] コマンド送信失敗: {e}")

    def _send_signal(self, proc: subprocess.Popen, name: str, sig: int) -> None:
        if proc is None or proc.poll() is not None:
            return
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, sig)
        except Exception:
            pass

    def kill_client(self) -> None:
        """クライアントプロセスを強制終了する"""
        if self.client_proc:
            self._send_signal(self.client_proc, "Script Client", signal.SIGKILL)
            self.client_proc = None

    def kill_all_processes(self, kill_resident: bool = False) -> None:
        """全プロセスを段階的に停止する（SIGINT → SIGKILL）"""
        print("\n=== システム停止処理 ===")
        if self.client_proc:
            self._send_signal(self.client_proc, "Script Client", signal.SIGINT)
        for name, proc in reversed(self.infra_procs):
            self._send_signal(proc, name, signal.SIGINT)
        if kill_resident:
            for name, proc in reversed(self.resident_procs):
                self._send_signal(proc, name, signal.SIGINT)

        time.sleep(3)

        if self.client_proc:
            self._send_signal(self.client_proc, "Script Client", signal.SIGKILL)
        for name, proc in reversed(self.infra_procs):
            self._send_signal(proc, name, signal.SIGKILL)
        if kill_resident:
            for name, proc in reversed(self.resident_procs):
                self._send_signal(proc, name, signal.SIGKILL)
            self.resident_procs = []

        self.client_proc = None
        self.infra_procs = []

        if kill_resident and self.xvfb_proc:
            self._send_signal(self.xvfb_proc, "Xvfb", signal.SIGKILL)
            self.xvfb_proc = None

    def kill_infra(self) -> None:
        """インフラプロセスだけを停止（residentは残す）"""
        self.kill_all_processes(kill_resident=False)
        self._force_cleanup_os()

    def _force_cleanup_os(self) -> None:
        """OS上の残存プロセスと共有メモリを掃除する"""
        print("  [徹底掃除] 残存プロセスと共有メモリを浄化中...")
        targets = ["awsim_labs.x86_64", "run_scenario.py", "component_container",
                   "rviz2", "autoware", "ros2"]
        for target in targets:
            os.system(f"pkill -15 -f {target} > /dev/null 2>&1")
        time.sleep(1)
        for target in targets:
            os.system(f"pkill -9 -f {target} > /dev/null 2>&1")
        os.system("ros2 daemon stop > /dev/null 2>&1")
        os.system("rm -f /dev/shm/ros2* > /dev/null 2>&1")
        os.system("rm -f /dev/shm/fastrtps* > /dev/null 2>&1")

    def cleanup_all(self) -> None:
        """全プロセスを完全に停止・クリーンアップする"""
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        self.kill_all_processes(kill_resident=True)
        self._force_cleanup_os()
        print("=== 全工程終了 ===")

    def count_target_files(self, output_dir: str, file_pattern: str) -> int:
        """出力ファイル数をカウントする"""
        import glob
        search_path = os.path.join(output_dir, file_pattern)
        all_files = glob.glob(search_path)
        valid_files = [f for f in all_files if "footage" not in os.path.basename(f)]
        return len(valid_files)
