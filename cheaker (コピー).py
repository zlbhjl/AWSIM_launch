import subprocess
import os
import time
import sys

def main():
    # ---------------------------------------------------------
    # 1. パスの設定
    # ---------------------------------------------------------
    tool_dir = os.path.expanduser("~/aw-cheaker/Maude-3.5.1/AW-CheckerPy")
    traces_dir = os.path.expanduser("~/simulation_traces")
    
    # ---------------------------------------------------------
    # 2. 環境変数の準備 (これが今回の修正のキモです)
    # ---------------------------------------------------------
    # 現在の環境変数をコピー
    my_env = os.environ.copy()
    # PWD (現在地情報) をツールのある場所に強制的に書き換える
    my_env["PWD"] = tool_dir

    print(f"--- 初期化プロセス開始 ---")
    
    if not os.path.exists(tool_dir):
        print(f"[Fatal Error] ディレクトリが見つかりません: {tool_dir}")
        return

    # Pythonプロセス自体の移動
    os.chdir(tool_dir)
    print(f"作業ディレクトリを移動しました: {os.getcwd()}")

    if "formal-model" in os.listdir("."):
        print("[OK] formal-model フォルダを確認しました。")
    else:
        print("[Warning] formal-model が見つかりません。")

    # ---------------------------------------------------------
    # 3. 監視ループ
    # ---------------------------------------------------------
    stats = {"True": 0, "False": 0, "Error": 0, "Total": 0}
    current_i = 1

    try:
        while True:
            filename = f"uturn_test_sim{current_i}.json"
            trace_path = os.path.join(traces_dir, filename)

            # --- 待機 ---
            if not os.path.exists(trace_path):
                msg = f"待機中... Next: {filename} | 統計: Safe={stats['True']} Unsafe={stats['False']} (Error={stats['Error']})"
                print(f"\r{msg}", end="")
                time.sleep(2)
                continue
            
            # --- 実行 ---
            print(f"\n\nDetected: {filename}")
            time.sleep(1) 

            command = [
                "python3",
                "aw_checkerpy.py",
                trace_path,
                '[] ~ collision("ego", "npc1")'
            ]

            try:
                # ★ ここで env=my_env を渡すことで、Maudeに正しい場所を認識させる
                result = subprocess.run(
                    command, 
                    cwd=tool_dir,
                    env=my_env,          # <--- 追加: これでMaudeの勘違いを防ぐ
                    capture_output=True, 
                    text=True
                )
                
                output_log = result.stdout
                
                # --- 結果判定 ---
                if "Model checking result: False" in output_log:
                    stats["False"] += 1
                    res_str = "UNSAFE (False) ❌"
                elif "Model checking result: True" in output_log:
                    stats["True"] += 1
                    res_str = "SAFE (True) ✅"
                else:
                    stats["Error"] += 1
                    res_str = "ERROR ⚠️"
                    print("--- Checker Output ---")
                    print(output_log)
                    print("--- Error Log ---")
                    print(result.stderr)
                
                stats["Total"] += 1
                valid = stats["True"] + stats["False"]
                rate = (stats["True"] / valid * 100) if valid > 0 else 0.0

                print(f">>> 結果: {res_str}")
                print(f"====== 統計 (sim1-{current_i}) ======")
                print(f"  安全(True) : {stats['True']}")
                print(f"  違反(False): {stats['False']}")
                print(f"  エラー     : {stats['Error']}")
                print(f"  安全率     : {rate:.1f}%")
                print(f"===================================")

            except Exception as e:
                print(f"実行エラー: {e}")

            current_i += 1

    except KeyboardInterrupt:
        print("\n終了します。")

if __name__ == "__main__":
    main()
