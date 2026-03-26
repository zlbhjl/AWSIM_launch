#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import time
import os
import signal
import glob
import sys
from dataclasses import dataclass
from typing import List, Tuple, Optional

# ==============================================================================
# 設定ファイルの読み込み (ディレクトリ分離対応)
# ==============================================================================
LAUNCH_DIR = os.path.dirname(os.path.abspath(__file__))
if LAUNCH_DIR not in sys.path:
    sys.path.append(LAUNCH_DIR)
import uturn_config

# ★ 汎用ロガーと、新しく作った司令官(Strategist)を読み込む
from param_logger import log_parameters
from strategist import ActiveLearningStrategist

# ==============================================================================
# 1. コンフィグレーション
# ==============================================================================
HOME = os.path.expanduser("~")
SETUP_BASH = os.path.join(HOME, "autoware/install/setup.bash")

REPEAT_COUNT = uturn_config.REPEAT_COUNT
OUTPUT_DIR = os.path.join(HOME, "simulation_traces")
FILE_PATTERN = "uturn_test_*.json"
TIMEOUT_SEC = 300  # タイムアウト時間
INTERVAL_SEC = 1

# システムの定期リフレッシュ間隔（10回ごとに再起動）
REFRESH_INTERVAL = 10

@dataclass
class Task:
    name: str
    work_dir: str
    command: str
    delay: int = 2
    source_setup: bool = False

# 【A】システム常駐（AWSIM + Autoware + Monitor）
INFRA_TASKS = [
    Task(
        name="AWSIM Labs",
        work_dir=os.path.join(HOME, "awsim_labs"),
        command="./awsim_labs.x86_64 -noise false",
        delay=15
    ),
    Task(
        name="Autoware",
        work_dir=os.path.join(HOME, "autoware"),
        command=(
            "ros2 launch autoware_launch e2e_simulator.launch.xml "
            "vehicle_model:=awsim_labs_vehicle "
            "sensor_model:=awsim_labs_sensor_kit "
            f"map_path:={HOME}/autoware_map/nishishinjuku_autoware_map "
            "launch_vehicle_interface:=true"
        ),
        delay=20,
        source_setup=True
    ),
    Task(
        name="Runtime Monitor",
        work_dir=os.path.join(HOME, "AW-Runtime-Monitor"),
        command=(
            f"python3 main.py -o {os.path.join(OUTPUT_DIR, 'uturn_test')} "
            "-n {sim_num}"
        ),
        delay=5,
        source_setup=True
    ),
]

# ==============================================================================
# 2. プロセスマネージャー
# ==============================================================================

class ProcessManager:
    def __init__(self):
        self.infra_procs: List[Tuple[str, subprocess.Popen]] = []
        self.client_proc: Optional[subprocess.Popen] = None
        self.strategist = ActiveLearningStrategist(num_candidates=10000)

    def _build_command(self, task: Task, sim_num: int) -> str:
        cmd = task.command.replace("{sim_num}", str(sim_num))
        if task.source_setup:
            cmd = f"source {SETUP_BASH} && {cmd}"
        return cmd

    def _start_process(self, task: Task, sim_num: int) -> Optional[subprocess.Popen]:
        full_cmd = self._build_command(task, sim_num)
        print(f"  [起動] {task.name} ... ", end="", flush=True)
        try:
            proc = subprocess.Popen(
                full_cmd, cwd=task.work_dir, shell=True,
                executable="/bin/bash", preexec_fn=os.setsid
            )
            print(f"OK (PID: {proc.pid}) -> {task.delay}秒待機")
            if task.delay > 0:
                time.sleep(task.delay)
            return proc
        except Exception as e:
            print(f"失敗: {e}")
            return None

    def _run_trigger_once(self, task: Task, sim_num: int):
        self.kill_client()
        full_cmd = self._build_command(task, sim_num)
        print(f"  [入力] {task.name} コマンド実行")
        try:
            self.client_proc = subprocess.Popen(
                full_cmd, cwd=task.work_dir, shell=True,
                executable="/bin/bash", preexec_fn=os.setsid
            )
        except Exception as e:
            print(f"  [エラー] コマンド送信失敗: {e}")

    # ==============================================================================
    # ★ 強化版キル処理: 全員に同時にシグナルを送り、待機時間を大幅に短縮！
    # ==============================================================================
    def _send_signal(self, proc: subprocess.Popen, name: str, sig: int):
        if proc is None or proc.poll() is not None:
            return
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, sig)
            action = "停止要求" if sig == signal.SIGINT else "強制終了"
            print(f"  [{action}] {name} (PID: {proc.pid})")
        except Exception:
            pass

    def kill_all_processes(self):
        print("\n=== システム全停止処理を開始 ===")
        # 1. 全部隊に同時にSIGINT（優しく終了）を送信
        if self.client_proc:
            self._send_signal(self.client_proc, "Script Client", signal.SIGINT)
        for name, proc in reversed(self.infra_procs):
            self._send_signal(proc, name, signal.SIGINT)
        
        # 全員に送った後で「1回だけ」待つ（これでシャットダウンが3秒で済む）
        print("  [待機] ソフトウェアの終了処理を待っています(3秒)...")
        time.sleep(3)

        # 2. まだ生きていたらSIGKILL（強制終了）を送信
        if self.client_proc:
            self._send_signal(self.client_proc, "Script Client", signal.SIGKILL)
        for name, proc in reversed(self.infra_procs):
            self._send_signal(proc, name, signal.SIGKILL)

        self.client_proc = None
        self.infra_procs = []

    def kill_client(self):
        if self.client_proc:
            self._send_signal(self.client_proc, "Script Client", signal.SIGKILL)
            self.client_proc = None

    # ==============================================================================
    # ★ 徹底浄化メソッド：10回ごとの再起動でも、Ctrl+Cでも、必ずここを通って完全に掃除する
    # ==============================================================================
    def _force_cleanup_os(self):
        print("  [徹底掃除] 残存プロセスと通信メモリ(ゴミ)を完全浄化中...")
        
        # 撃ち漏らしをゼロにするため、関連キーワードを網羅
        targets = [
            "awsim_labs.x86_64", 
            "run_scenario.py",
            "component_container", 
            "rviz2",
            "autoware",  
            "ros2"       
        ]

        # 1段階目: 優しく終了
        for target in targets:
            os.system(f"pkill -15 -f {target} > /dev/null 2>&1")
        time.sleep(2)
        
        # 2段階目: ゾンビを強制終了
        for target in targets:
            os.system(f"pkill -9 -f {target} > /dev/null 2>&1")
            
        # ★ ここが最重要！ ROS 2デーモンと共有メモリ（見えないゴミ）の完全破壊
        os.system("ros2 daemon stop > /dev/null 2>&1")
        os.system("rm -f /dev/shm/ros2* > /dev/null 2>&1")
        os.system("rm -f /dev/shm/fastrtps* > /dev/null 2>&1")
        time.sleep(1)

    def kill_infra(self):
        # まずプロセス群を終了させ、その後にOSレベルで徹底浄化（メモリ破壊含む）
        self.kill_all_processes()
        self._force_cleanup_os()

    def count_target_files(self):
        search_path = os.path.join(OUTPUT_DIR, FILE_PATTERN)
        all_files = glob.glob(search_path)
        valid_files = [f for f in all_files if "footage" not in os.path.basename(f)]
        return len(valid_files)

    def execute(self):
        print("=== テスト自動化システム（Active Learning 搭載版） ===")
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        current_sim_idx = self.count_target_files()
        
        if current_sim_idx > 0:
            print(f"\n  [復帰] 既存のデータ({current_sim_idx}件)を発見しました。")
            print(f"  [復帰] 続き(Loop {current_sim_idx + 1})から再開します！")

        while current_sim_idx < REPEAT_COUNT:
            sim_num = current_sim_idx + 1

            if not self.infra_procs:
                print(f"\n=== Phase 1: システム起動 (Start from sim_num={sim_num}) ===")
                for task in INFRA_TASKS:
                    p = self._start_process(task, sim_num)
                    if p: self.infra_procs.append((task.name, p))
            
            while current_sim_idx < REPEAT_COUNT:
                current_loop_num = current_sim_idx + 1
                
                print(f"\n---------------------------------------------")
                print(f" Loop {current_loop_num} / {REPEAT_COUNT} 開始")
                print(f"---------------------------------------------")

                target_json = os.path.join(OUTPUT_DIR, f"uturn_test_sim{current_loop_num}.json")

                if os.path.exists(target_json):
                    os.remove(target_json)
                    print("前回の残骸ファイルをクリアしました")
                
                print("  [頭脳] 司令官(Strategist)に次期作戦を要求中...")
                next_target = self.strategist.decide_next_target()
                
                r_dx0 = next_target["dx0"]
                r_ego_speed = next_target["ego_speed"]
                r_npc_speed = next_target["npc_speed"]
                
                print(f"  [司令官の指示] 理由: {next_target['reason']}")
                print(f"  [実行パラメータ] 距離: {r_dx0:.2f}m | 自車: {r_ego_speed:.1f}km/h | 他車: {r_npc_speed:.1f}km/h")

                current_params = {
                    "dx0": r_dx0,
                    "ego_speed": r_ego_speed,
                    "npc_speed": r_npc_speed
                }
                csv_filename = f"{uturn_config.SCENARIO_TYPE}_parameters.csv"
                log_parameters(OUTPUT_DIR, csv_filename, current_loop_num, current_params)

                dynamic_cmd = (
                    f"python3 run_scenario.py "
                    f"--type {uturn_config.SCENARIO_TYPE} "
                    f"--dx0 {r_dx0:.2f} "
                    f"--ego_speed {r_ego_speed:.2f} "
                    f"--npc_speed {r_npc_speed:.2f}"
                )

                dynamic_client_task = Task(
                    name="Dynamic Scenario Runner",
                    work_dir=LAUNCH_DIR,
                    command=dynamic_cmd,
                    delay=0,
                    source_setup=True
                )

                self._run_trigger_once(dynamic_client_task, current_loop_num)

                print(f"  >>> 実行中... (Monitorの検知待ち, Timeout: {TIMEOUT_SEC}s)")
                start_wait = time.time()
                is_timeout = False
                
                while True:
                    time.sleep(2)

                    if os.path.exists(target_json):
                        print(f"成功: {os.path.basename(target_json)} の新規生成を確認！")
                        print("  [待機] 審判(awcheaker)の判定と結果記録を待ちます...")
                        time.sleep(5) 
                        break 

                    if time.time() - start_wait > TIMEOUT_SEC:
                        print(f"  [警告] タイムアウト ({TIMEOUT_SEC}秒)")
                        is_timeout = True
                        break 

                if is_timeout:
                    print(f"  !!! 失敗 (Loop {current_loop_num}): タイムアウトのためやり直します !!!")
                    self.kill_infra() 
                    break 
                else:
                    print("  [完了] シナリオ正常終了")
                    self.kill_client()
                    current_sim_idx += 1
                    
                    if current_sim_idx % REFRESH_INTERVAL == 0 and current_sim_idx < REPEAT_COUNT:
                        print(f"\n  [定期リフレッシュ] {current_sim_idx}回のテストが完了しました。")
                        print("  システムの安定稼働のため、インフラを再起動します。")
                        self.kill_infra()
                        break 
                    
                    time.sleep(INTERVAL_SEC)

    def cleanup_all(self):
        # 掃除中にCtrl+Cを連打されても無視する（防御）
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        print("\n=== 全工程終了 ===")
        self.kill_infra()
        print("完了。全てのソフトウェアを完全に閉じました。")

if __name__ == "__main__":
    manager = ProcessManager()
    try:
        manager.execute()
    except KeyboardInterrupt:
        print("\n[!] ユーザーによる中断 (Ctrl+C)")
    except Exception as e:
        print(f"\n[!] 予期せぬエラーが発生しました: {e}")
    finally:
        # どんなエラーで落ちても、絶対にここを通って掃除する
        manager.cleanup_all()
        time.sleep(1)
        sys.exit(1)
