awchecker.py変更点と維持したポイント
維持した機能:

while True によるファイルの継続監視機能。

CSVを読み取って続きから再開する Resume機能。

ターミナル上でのリアルタイムな 統計表示（Safe/Unsafe）。

新しく追加・改善した機能:

多項目パース: formulas.txt にある複数の判定を、正規表現で個別に抜き出すロジックを統合しました。

ワイドCSV形式: c_ttc_1.5 などの列にそれぞれの結果を保存するようにしました。

将来の拡張性: METRIC_CONFIG のリストを書き換えるだけで、CSVの列や検証項目を自由に変更できます。


estimator.py
引数 target_column の追加:
train メソッドの引数に target_column を追加しました。これにより、strategist.py 側から estimator.train(param_path, res_path, target_column="c_ttc_1.2") のように、「今日AIに勉強させる科目」を自由に指示できるようになります。

不純物のフィルタリング（.isin([0, 1])）:
前回の awchecker.py の修正で、パース失敗時に -1 を記録するようにしました。AIがこの -1 を「安全と危険の中間」などと勘違いしないよう、純粋な 0（合格）と 1（違反）のデータだけを抽出して学習させます。

特徴量の自動クレンジング（startswith("c_")）:
ワイド形式のCSVには複数の結果列（c_collision, c_ttc_1.5 など）が含まれます。AIが「他の結果列をカンニング」して予測するのを防ぐため、c_ で始まる列をすべて自動的に除外（drop）し、純粋なシミュレーションパラメータ（自車の速度など）だけを入力値 X として扱います。



strategist.py
今回の修正の最大の眼目は、**「AIがデータの蓄積状況に合わせて、自分で学習する対象（ターゲット）の難易度を切り替える機能」**の追加です。

元のコードから具体的に以下の4点を大きく修正・追加しました。

1. 優先順位表（ターゲットリスト）の追加
__init__ 内に、self.target_priorities というリストを追加しました。

上から順に c_collision (衝突) → c_ttc_0.7 (回避不能) → c_ttc_1.2 (危険) → c_ttc_1.5 (ヒヤリハット) と並べています。

AIは常に「この中で一番厳しい基準」の境界線を狙おうとします。

2. レーダー機能：get_best_target メソッドの新設
過去のデータ（CSV）を分析して、「いま学習可能な最も厳しいターゲットはどれか」を探し出す専用の関数を作りました。

条件: AIが境界線を引くには「合格(0)」と「違反(1)」の両方のデータが最低1件ずつ必要です。

動作: 優先順位表を上からチェックし、「違反が1件以上見つかった一番厳しい基準」を返します（例：衝突は0件だが、TTC 1.2s違反なら見つかった場合は c_ttc_1.2 を返す）。

3. 指揮系統の変更（decide_next_target の改修）
元のコードは、何も考えずに self.estimator.train() を呼び出していました。これを以下の順序に書き直しました。

まずCSV全体を見て、get_best_target でターゲットを決める。

決まったターゲットを self.estimator.train(target_column=best_target) としてEstimatorに「今日の学習科目」として渡す。

ターゲットが見つからない（すべて超安全だった）場合のみ、Sobol探索を延長する。

4. ログとReasonの明確化
出力するメッセージや CSV に書き込まれる reason 列に、**「いまAIがどの基準の境界線を狙っているか」**を明記するようにしました。

例: [Strategist] 🎯 'c_ttc_1.2' の境界線ターゲットを発見

例: reason: Active Learning (c_ttc_1.2, Uncert: 0.982)

この修正がもたらす変化（ストーリー）
元のシステムでは、100回シミュレーションして一度もぶつからなかった場合、AIは**「何も学ぶことがない。お手上げだ」**とSobol（ランダム）探索を続けるしかありませんでした。

修正後のシステムでは、100回走って衝突が0件でも、**「衝突はしなかったけど、TTC 1.5秒の基準を破ったギリギリの走行が3件あったぞ。よし、まずは『どうすればTTC 1.5秒を破れるか』を学習して、その境界線を攻めよう」**と自ら足がかりを見つけます。
そして 1.5秒の境界を攻めているうちに 1.2秒違反が見つかり、やがて 0.7秒違反、最終的には「衝突」の境界線へと、AIが自ら危険な領域を深掘りしていくようになります。

これにより、探索の効率が劇的に向上するはずです。

1. run_manager.py: 初期化時の古いCSVの削除（超重要）
現在の run_manager.py は、起動時に過去の *.json ファイルの数を数えて続きから再開する仕様になっています。しかし、checker_results.csv や uturn_parameters.csv の古いデータが残っていると、AIの学習に矛盾が生じます（例：前回はTTC 1.5sの式が無かったのに、今回はある、など）。

修正案: スクリプト起動時に、ユーザーに「新規開始か、続きからか」を尋ねるか、少なくとも古いログファイルをクリーンアップする処理を入れるべきです。

Python
# run_manager.py の execute() 関数の冒頭付近に追加
    def execute(self):
        print(f"=== 自動化システム [{SCENARIO_NAME.upper()} モード] ===")
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        # --- [追加] 古いCSVファイルのクリーンアップ確認 ---
        csv_param = os.path.join(OUTPUT_DIR, f"{SCENARIO_NAME}_parameters.csv")
        csv_result = os.path.join(OUTPUT_DIR, "checker_results.csv")
        
        # JSONファイルの数で新規か再開かを判断
        current_sim_idx = self.count_target_files()
        
        if current_sim_idx == 0:
            print("[System] 新規実行を検出しました。古いCSVログを初期化します。")
            if os.path.exists(csv_param): os.remove(csv_param)
            if os.path.exists(csv_result): os.remove(csv_result)
        else:
            print(f"[System] 既存のデータ (sim1 〜 sim{current_sim_idx}) から再開します。")
        # -----------------------------------------------
        
        while current_sim_idx < REPEAT_COUNT:
2. run_manager.py: current_params の抽出順序（論理バグ回避）
execute ループ内の以下の処理に、小さな論理バグの種があります。

Python
# 現在のコード
next_target = self.strategist.decide_next_target()
current_params = {k: v for k, v in next_target.items() if k != 'reason'} #
csv_filename = f"{SCENARIO_NAME}_parameters.csv"
log_parameters(OUTPUT_DIR, csv_filename, current_loop_num, current_params, reason=next_target.get('reason', "")) #
log_parameters 関数は、渡された params_dict のキーを使ってCSVのヘッダーを作ります。もしここで current_params に reason が含まれていないと、ヘッダーの順番や内容が狂う可能性があります。

修正案: log_parameters には元の next_target の中身をそのまま渡し、コマンド生成時（シミュレータに渡す時）だけ reason を除外するように順序を入れ替えます。

Python
# run_manager.py の execute() 内の修正
                # 1. Strategist に次期作戦を要求
                next_target = self.strategist.decide_next_target()
                reason_str = next_target.pop("reason", "") # reasonを抜き取って変数に退避
                
                # 2. パラメータの記録 (reasonは別引数で渡す)
                csv_filename = f"{SCENARIO_NAME}_parameters.csv"
                log_parameters(OUTPUT_DIR, csv_filename, current_loop_num, next_target, reason=reason_str)

                # 3. 引数の動的生成 (この時点で next_target には数値パラメータしか残っていない)
                param_args = " ".join([f"--{k} {v:.2f}" for k, v in next_target.items()])
                dynamic_cmd = f"python3 run_scenario.py --type {SCENARIO_NAME} {param_args}"
3. param_logger.py: fieldnames の重複防止
param_logger.py の fieldnames 生成部分で、引数 reason が重複してエラーになる可能性があります。

Python
# 現在のコード
fieldnames = ["loop_num"] + list(params_dict.keys()) + ["reason"] #
もし params_dict の中に既に reason というキーが残っていた場合、CSVのヘッダーに reason が2つ出来てしまい、DictWriter がエラーを吐きます。

修正案: 先ほどの run_manager.py の修正（pop を使う）を行えばこの問題は自然に解決しますが、念のためロガー側でも安全対策を入れておくと完璧です。

Python
# param_logger.py の修正
    # params_dict から 'reason' キーが混ざっていたら除外してキーリストを作る
    clean_keys = [k for k in params_dict.keys() if k != "reason"]
    fieldnames = ["loop_num"] + clean_keys + ["reason"]









