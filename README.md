1. 静的インポートから「動的ロード」への転換
これまでは、コードの冒頭で import uturn_config と記述されていたため、Uターン以外の設定を読み込むことができませんでした。

修正内容: importlib を導入しました。

効果: 実行時に python3 run_manager.py --type cutin と入力するだけで、プログラムが自動的に cutin_config.py を探し出し、その中身を cfg という変数に格納して利用できるようになります。

2. データ保存場所と検索パターンの「名前空間化」
以前のコードでは、出力ファイル名や検索パターンに "uturn" という文字列が直接書き込まれていました。これでは、別のシナリオを走らせてもデータが混ざったり、上書きされたりする危険がありました。

修正内容: "uturn_test_*.json" のような固定値を、f"{SCENARIO_NAME}_test_*.json" のように変数化しました。

効果: cutin シナリオを実行すれば cutin_test_sim1.json が生成され、swerve なら swerve_test_sim1.json が生成されるようになります。過去の uturn のデータを汚さずに、新しい実験を並行して行えます。

3. コマンドライン引数の「自動生成」
run_scenario.py を呼び出す際、これまでは dx0, ego_speed, npc_speed という特定の引数を手動で組み立てていました。

修正内容: AI（Strategist）が提案したパラメータ辞書をループで回し、--{キー} {値} の形式でコマンドライン引数を自動生成するようにしました。

効果: 将来的に cutin シナリオで「割り込みの角度（angle）」などの新しいパラメータが増えたとしても、run_manager.py 側のコードを一切いじることなく、そのまま AI が新しいパラメータを制御して実行できるようになります。


estimator.py

1. インポート部分の修正
特定のシナリオ設定（uturn_config）への依存を無くします。

修正前: import uturn_config

修正後: 削除（設定オブジェクトは実行時に run_manager.py から渡されるため、ここでは不要になります）

2. __init__ メソッドの引数と初期化
クラスの初期化時に「どのシナリオか」と「その設定内容」を受け取れるように拡張します。

修正箇所:

Python
# 修正前
def __init__(self, traces_dir="~/simulation_traces"):
    self.param_file = os.path.join(self.traces_dir, "uturn_parameters.csv")
    self.feature_names = list(uturn_config.PARAM_RANGES.keys())

# 修正後
def __init__(self, scenario_name, config, traces_dir="~/simulation_traces"):
    self.config = config  # 渡された設定を保持
    self.param_file = os.path.join(self.traces_dir, f"{scenario_name}_parameters.csv") # 動的に決定
    self.feature_names = list(self.config.PARAM_RANGES.keys()) # self.config から取得
3. データ読み込みとクリーンアップの汎用化
パラメータ名がシナリオごとに変わっても、自動で対応できるようにします。

修正箇所: load_and_merge_data メソッド内

ファイル存在確認: self.param_file が動的に変わっているため、エラーメッセージに具体的なファイル名を表示するように変更します。

欠損値削除の対象: self.feature_names（動的に取得したリスト）を使用しているため、ここは元のスマートな実装 のままで汎用性が保たれます。

4. 学習処理 (train) のログ出力
どの項目の学習を行っているかを明確にします。

修正箇所: print 文

修正後: print(f"[Estimator] {len(X)}件のデータで学習中... (項目: {self.feature_names})") のように、現在の学習対象をログに出すようにします。


strategist.py

1. パラメータ生成の自動化 (generate_candidate_points)
以前は dx0, ego_speed, npc_speed と手書きしていましたが、ここが一番短くなっています。

修正前: 3つの変数（dx0_min, ego_min, npc_min）を個別に作ってから結合。

修正後: for 文を使って、設定ファイルにある項目を自動的にスキャンして結合するようにしました。

これなら将来パラメータが5個に増えても、このコードを書き換える必要はありません。

2. ランダム抽出の効率化 (get_fresh_random_point)
以前は 3つの数値をタプル (dx0, ego, npc) で返していましたが、ここもスマートになっています。

修正内容: 内包表記（{name: ... for name, r in ranges.items()}）を使用。

メリット: run_manager.py が「どの値がどのパラメータか」を迷わなくて済むよう、最初から名前付きの辞書形式で返すようにしました。

3. 次期作戦の決定ロジック (decide_next_target)
返却するデータの作り方が変わりました。

修正前: return {"dx0": dx0, "ego_speed": ego, ...} と手動で辞書を作成。

修正後: result = {name: best_point[i] for i, name in enumerate(param_names)}

AIが見つけた「一番怪しい数値のリスト」に、設定ファイルから取ってきた「パラメータ名」を自動でガッチャンコしています。


strategist.pyのランダムアルゴリズムをSobolに変更

