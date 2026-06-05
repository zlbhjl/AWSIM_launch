#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import numpy as np
import pandas as pd
from scipy.stats import qmc  # Sobol配列生成用
from estimator import SafetyEstimator
from redis_cluster import cluster_config
import point_extractors

try:
    from theoretical_calculator import TheoreticalSafetyCalculator
except ImportError:
    TheoreticalSafetyCalculator = None

class ActiveLearningStrategist:
    def __init__(self, scenario_name, config, num_candidates=10000, focus_points=None, run_mode="explore", dkw_bounds=None, dkw_region="custom"):
        self.scenario_name = scenario_name
        self.config = config
        self.num_candidates = num_candidates
        self.run_mode = run_mode
        self.dkw_bounds = dkw_bounds
        self.dkw_region = dkw_region
        
        self.estimator = SafetyEstimator(scenario_name, config)
        self.param_names = list(self.config.PARAM_RANGES.keys())
        self.dim = len(self.param_names)

        # --- 設定ファイル(config)から戦略パラメータを動的に取得 ---
        self.target_priorities = getattr(self.config, 'TARGET_PRIORITIES', [])
        self.INITIAL_EXPLORATION_LIMIT = getattr(self.config, 'INITIAL_EXPLORATION_LIMIT', 100)
        self.MIN_SAMPLES = getattr(self.config, 'MIN_SAMPLES', 500)
        self.MAX_SAMPLES = getattr(self.config, 'MAX_SAMPLES', 2000)

        # [変更] フェーズ移行・終了条件の新しいパラメータ
        self.STABILITY_REFERENCE_POINTS = getattr(self.config, 'STABILITY_REFERENCE_POINTS', 2000)
        self.STABILITY_HISTORY_LENGTH = getattr(self.config, 'STABILITY_HISTORY_LENGTH', 50)
        self.STABILITY_HYSTERESIS = getattr(self.config, 'STABILITY_HYSTERESIS', (0.40, 0.60))
        self.STABILITY_SHIFT_THRESHOLD = getattr(self.config, 'STABILITY_SHIFT_THRESHOLD', 0.01)
        self.STABILITY_REQUIRED_STREAK = getattr(self.config, 'STABILITY_REQUIRED_STREAK', 3)
        self.STEP2_MAX_EXPLORATION = getattr(self.config, 'STEP2_MAX_EXPLORATION', 500)
        self.MARGIN_RANGE = getattr(self.config, 'MARGIN_RANGE', (0.3, 0.48))
        self.MARGIN_MAX_UNCERTAINTY = getattr(self.config, 'MARGIN_MAX_UNCERTAINTY', 0.05)
        
        # コマンドライン引数で渡された focus_points を使用 (Configに依存しない)
        self.FOCUS_POINTS = focus_points
        self.FOCUS_NOISE = getattr(self.config, 'FOCUS_NOISE', 0.05)

        # --- [変更] 抽出ロジックを外部モジュールに委譲 ---
        if self.run_mode in point_extractors.EXTRACTORS:
            extractor_func = point_extractors.EXTRACTORS[self.run_mode]
            df = self.estimator.load_dataset()
            extracted_points = extractor_func(df, self.param_names, self.config)
            if extracted_points:
                self.FOCUS_POINTS = extracted_points
                print(f"[Strategist] 🎯 {len(self.FOCUS_POINTS)} 件のターゲットポイントを自動設定しました。")
            else:
                print(f"[Strategist] ⚠️ 条件に合致するポイントはありませんでした。")

        # --- [追加] Sequential-DKW (SMC) 単独証明モード用のパラメータ ---
        if self.run_mode == "dkw":
            self.dkw_stage = 1
            self.dkw_sobol_index = 0  # [修正] DKW専用の乱数インデックスを新設し、歯抜けを防ぐ
            self.dkw_base_samples = getattr(self.config, 'DKW_BASE_SAMPLES', 50)     # 基本サンプル数 n
            self.dkw_total_delta = getattr(self.config, 'DKW_TOTAL_DELTA', 0.05)       # 最終的な信頼水準 (例: 95%)
            self.dkw_target_epsilon = getattr(self.config, 'DKW_TARGET_EPSILON', 0.15) # 求める精度 (信頼区間の幅)
            self.dkw_target_metric = getattr(self.config, 'DKW_TARGET_METRIC', 'min_ttc')
            
            # --- [追加] 過去データから安全領域(Bounds)を自動計算 ---
            if self.dkw_region != "custom":
                df = self.estimator.load_dataset()
                if df is not None and not df.empty:
                    try:
                        f_df = point_extractors.filter_by_region_and_bounds(df, region=self.dkw_region)
                        if not f_df.empty:
                            self.dkw_bounds = {col: [float(f_df[col].min()), float(f_df[col].max())] for col in self.param_names if col in f_df.columns}
                            print(f"[Strategist] 📊 DKW証明モード: 抽出条件 '{self.dkw_region}' に基づきサンプリング領域を自動算出しました -> {self.dkw_bounds}")
                        else:
                            print(f"[Strategist] ⚠️ 指定された条件('{self.dkw_region}')に該当するデータがありません。")
                    except Exception as e:
                        print(f"[Strategist] ⚠️ 領域計算に失敗しました: {e}")
                else:
                    print(f"[Strategist] ⚠️ データセットが存在しないため、領域の自動算出ができません。")
            # --------------------------------------------------------

            if self.dkw_region == "custom" and self.dkw_bounds:
                print(f"[Strategist] 📊 DKW証明モード: 手動で領域を限定して評価します {self.dkw_bounds}")
            elif not self.dkw_bounds:
                print(f"[Strategist] 📊 DKW証明モード: 空間全体の一様サンプリングによる厳密な統計的保証を行います。")

        # アクティブなサンプリング範囲を決定 (DKWモードでの範囲指定があれば上書き)
        self.active_bounds = {}
        for name in self.param_names:
            if self.run_mode == "dkw" and self.dkw_bounds and name in self.dkw_bounds:
                self.active_bounds[name] = self.dkw_bounds[name]
            else:
                self.active_bounds[name] = self.config.PARAM_RANGES[name]

        # 状態管理変数
        self.reference_points = self.generate_candidate_points(num=self.STABILITY_REFERENCE_POINTS)
        self.stability_history = []
        self.stability_streak = 0
        self.step2_exploration_count = 0
        self.current_phase = "STEP3" if self.run_mode == "margin" else "STEP1"
        self.dispatched_task_count = 0
        
        # [追加] AIの重い処理を減らすためのタスクキャッシュ機構
        self.task_cache = []
        # 稼働中のマシン(ワーカー)数を動的にカウントし、その2倍を1回の推論で作るバッチサイズ(補充量)とする
        self.worker_count = sum(1 for node in cluster_config.CLUSTER_NODES.values() if node.get("enabled", True))
        self.CACHE_SIZE = self.worker_count * 2
        
        # フォーカスモードの反復テスト用独立カウンタ（過去のループ数に依存しないようにする）
        self.focus_exact_test_count = 0
        
        # [追加] エラーの無限リカバリー(再試行ループ)を防ぐための記録
        self.last_recovered_loop = 0

    def get_sobol_point(self, index):
        sampler = qmc.Sobol(d=self.dim, scramble=True, seed=42)
        # [修正] 毎回巨大な配列を生成する計算爆発を防ぎ、O(1) で高速に指定インデックスの点を取得する
        sampler.fast_forward(int(index))
        sample = sampler.random(n=1)[0]
        
        point_dict = {name: self.active_bounds[name][0] + sample[i] * (self.active_bounds[name][1] - self.active_bounds[name][0]) 
                      for i, name in enumerate(self.param_names)}
        return point_dict

    def get_best_target(self, df):
        if df is None or len(df) == 0: return None
        for target in self.target_priorities:
            if target in df.columns:
                v = df[df[target].isin([0, 1])]
                if 1 in v[target].values and 0 in v[target].values: return target
        return None

    def _evaluate_boundary_stability(self):
        mean, _ = self.estimator.predict_uncertainty(self.reference_points)
        if mean is None: return False, 0.0

        states = np.full(mean.shape, -1)
        states[mean < self.STABILITY_HYSTERESIS[0]] = 0
        states[mean > self.STABILITY_HYSTERESIS[1]] = 1

        self.stability_history.append(states)
        if len(self.stability_history) > self.STABILITY_HISTORY_LENGTH:
            self.stability_history.pop(0)

        if len(self.stability_history) < self.STABILITY_HISTORY_LENGTH:
            return False, 0.0

        oldest = self.stability_history[0]
        newest = self.stability_history[-1]

        flips = np.sum(((oldest == 0) & (newest == 1)) | ((oldest == 1) & (newest == 0)))
        shift_rate = flips / self.STABILITY_REFERENCE_POINTS

        if shift_rate < self.STABILITY_SHIFT_THRESHOLD:
            self.stability_streak += 1
        else:
            self.stability_streak = 0

        is_stable = self.stability_streak >= self.STABILITY_REQUIRED_STREAK
        return is_stable, shift_rate

    def decide_next_target(self):
        # --- [追加] 抽出モードで対象が見つからなかった場合、探索をせずに即時終了する ---
        if self.run_mode in point_extractors.EXTRACTORS and not self.FOCUS_POINTS:
            self._print_final_report(self.dispatched_task_count, self.run_mode, f"抽出対象のポイントが見つからなかったため、処理を終了します。")
            return {"system_command": "stop", "reason": f"No target points found for mode '{self.run_mode}'"}

        # キャッシュされたタスクがあればAIの重い計算をスキップして即座に返す
        if self.task_cache:
            self.dispatched_task_count += 1
            return self.task_cache.pop(0)

        # [追加] ログ出力用のプレフィックス
        log_prefix = "[FOCUS] " if self.FOCUS_POINTS else ""
            
        df_dataset = self.estimator.load_dataset()
        
        # --- [追加] エラーの崖っぷち探索: 新しいエラーが発生していたら少しずらしてリカバリー検証する ---
        if df_dataset is not None and 'loop_num' in df_dataset.columns:
            # 文字列混入によるエラーを防ぎつつ、-1 (タイムアウトや異常) のデータを安全に探す
            # [修正] min_ttc列がまだ存在しない初期段階の KeyError を防ぐ
            min_ttc_numeric = pd.to_numeric(df_dataset.get('min_ttc', pd.Series(np.nan, index=df_dataset.index)), errors='coerce')
            # [修正] 列が存在しない場合のフェイルセーフで、元のdfと同じ長さの空Seriesを生成してクラッシュを防ぐ
            c_collision_numeric = pd.to_numeric(df_dataset.get('c_collision', pd.Series(np.nan, index=df_dataset.index)), errors='coerce')
            
            error_mask = (min_ttc_numeric == -1) | (c_collision_numeric == -1)
            new_errors = df_dataset[error_mask & (df_dataset['loop_num'] > self.last_recovered_loop)]
            
            if not new_errors.empty:
                recovery_points = []
                for _, err_row in new_errors.iterrows():
                    shifted_point = {}
                    for name in self.param_names:
                        rng = self.config.PARAM_RANGES[name][1] - self.config.PARAM_RANGES[name][0]
                        # 3%の微小ノイズを加えて少しずらす (シミュレータのクラッシュ回避)
                        noise = np.random.normal(0, rng * 0.03) 
                        try:
                            val = float(err_row[name]) + noise
                        except (ValueError, TypeError, KeyError):
                            # CSVのズレ等で文字列が入っている場合は安全な中央値を使用する
                            val = (self.config.PARAM_RANGES[name][0] + self.config.PARAM_RANGES[name][1]) / 2.0 + noise
                        val = np.clip(val, self.config.PARAM_RANGES[name][0], self.config.PARAM_RANGES[name][1])
                        shifted_point[name] = val
                    shifted_point["reason"] = f"{log_prefix}Error Recovery (Shifted from Loop {int(err_row['loop_num'])})"
                    recovery_points.append(shifted_point)
                
                # リカバリー済みの最大ループ番号を更新し、同じエラーを何度も再試行するのを防ぐ
                self.last_recovered_loop = int(new_errors['loop_num'].max())
                
                # リカバリーポイントをキャッシュの先頭に追加して優先実行させる
                self.task_cache.extend(recovery_points)
                self.dispatched_task_count += 1
                return self.task_cache.pop(0)
        # --------------------------------------------------------------------------------------

        # [修正] 行数ではなく、CSVに記録されている最大のループ番号と同期させる（データ欠損対策）
        if df_dataset is not None and 'loop_num' in df_dataset.columns:
            max_loop_val = df_dataset['loop_num'].max()
            # [修正] データが空で NaN が返ってきた場合の ValueError を防ぐ
            if pd.notna(max_loop_val):
                max_loop = int(max_loop_val)
                if self.dispatched_task_count < max_loop:
                    self.dispatched_task_count = max_loop
            
        best_target = self.get_best_target(df_dataset)
        num_violations = (df_dataset[best_target] == 1).sum() if (df_dataset is not None and best_target) else 0
        
        # CSV完了数ではなく、タスク生成回数を基準にして決定論的な重複を防ぐ
        current_idx = self.dispatched_task_count

        # --- 【STEP 0】フォーカスモード / 一貫性検証モード のピンポイント検証 ---
        if self.FOCUS_POINTS:
            # configになければデフォルト10回とする
            exact_repeats = getattr(self.config, 'FOCUS_EXACT_REPEATS', 10)
            total_exact_samples = len(self.FOCUS_POINTS) * exact_repeats
            
            if self.focus_exact_test_count < total_exact_samples:
                point_idx = (self.focus_exact_test_count // exact_repeats) % len(self.FOCUS_POINTS)
                repeat_idx = (self.focus_exact_test_count % exact_repeats) + 1
                
                exact_point = self.FOCUS_POINTS[point_idx]
                result = {name: exact_point.get(name, sum(self.config.PARAM_RANGES[name])/2.0) for name in self.param_names}
                
                mode_label = "CONSISTENCY" if self.run_mode == "verify_consistency" else "FOCUS"
                result["reason"] = f"[{mode_label}] Exact Point {point_idx+1}/{len(self.FOCUS_POINTS)} (Repeat {repeat_idx}/{exact_repeats})"
                
                self.focus_exact_test_count += 1
                self.dispatched_task_count += 1
                return result
            elif self.run_mode == "verify_consistency":
                # --- 全反復テスト完了時の自動分類とDKW評価 ---
                print("\n[Strategist] 📊 全反復テスト完了。TTCの安定性を評価し、分類ごとのDKW証明を行います...")
                df = self.estimator.load_dataset()
                if df is not None and not df.empty:
                    # [修正] 全データではなく、この検証モードで実行されたデータのみを分析対象とする
                    consistency_df = df[df['reason'].str.contains('\\[CONSISTENCY\\]', na=False)]
                    if consistency_df.empty:
                        print("[Strategist] ⚠️ 一貫性検証モードで実行されたデータが見つかりませんでした。")
                        return {"system_command": "stop", "reason": "No consistency data found"}

                    threshold = getattr(self.config, 'CONSISTENCY_THRESHOLD', 0.2)
                    df_consistent, df_stochastic = point_extractors.classify_consistency(consistency_df, self.param_names, threshold=threshold, min_repeats=2)
                    
                    # --- [追加] 分類されたデータを個別のCSVとして保存 ---
                    traces_dir = os.path.expanduser(f"~/simulation_traces")
                    out_path_consistent = os.path.join(traces_dir, f"{self.scenario_name}_consistent_risk.csv")
                    point_extractors.save_dataframe_to_csv(df_consistent, out_path_consistent, f"[Strategist] 💾 確実なリスク領域のデータを保存しました: {out_path_consistent}")
                    out_path_stochastic = os.path.join(traces_dir, f"{self.scenario_name}_stochastic_risk.csv")
                    point_extractors.save_dataframe_to_csv(df_stochastic, out_path_stochastic, f"[Strategist] 💾 偶然のリスク領域のデータを保存しました: {out_path_stochastic}")
                    # --------------------------------------------------
                    
                    def print_dkw_result(title, target_df):
                        print(f"\n--- {title} (データ件数: {len(target_df)}) ---")
                        summary = self.estimator.evaluate_and_summarize_dkw(
                            target_column=getattr(self.config, 'DKW_TARGET_METRIC', 'min_ttc'),
                            df=target_df,
                            q=0.05,
                            delta=getattr(self.config, 'DKW_TOTAL_DELTA', 0.05),
                            epsilon=getattr(self.config, 'DKW_TARGET_EPSILON', 0.15)
                        )
                        if summary["status"] == "error":
                            print(f"  -> {summary['message']}")
                        else:
                            print(f"  - ワースト5%の推定値: {summary['estimate']:.3f} | 信頼区間: [{summary['lower_bound']:.3f}, {summary['upper_bound']:.3f}] (幅: {summary['interval_width']:.3f})")

                    print_dkw_result("確実なリスク領域 (Consistent Risk)", df_consistent)
                    print_dkw_result("偶然のリスク領域 (Stochastic Risk)", df_stochastic)

                self._print_final_report(current_idx, "一貫性検証 (Verify Consistency)", "分類ごとのDKW証明完了")
                return {"system_command": "stop", "reason": "Consistency Verification Complete"}

        # --- [追加] 単独モード: Sequential-DKW (SMC) による指定データの統計的検証 ---
        if self.run_mode == "dkw":
            delta_i = self.dkw_total_delta / (2 ** self.dkw_stage)
            bounds_result = self.estimator.calculate_quantile_with_dkw(
                target_column=self.dkw_target_metric,
                q=0.05,
                delta=delta_i,
                bounds=self.dkw_bounds,
                region=self.dkw_region
            )
            
            if bounds_result:
                current_samples = bounds_result["sample_size"]
                target_n_i = self.dkw_base_samples * (self.dkw_stage ** 2)
                
                interval_width = bounds_result["upper_bound"] - bounds_result["lower_bound"]
                is_converged = interval_width <= self.dkw_target_epsilon
                
                # --- 具体的な数値を抽出 ---
                lower = bounds_result["lower_bound"]
                upper = bounds_result["upper_bound"]
                estimate = bounds_result["estimate"]

                # --- [追加] 計算に使用されたサンプルデータを専用ファイルとして保存 ---
                filtered_df = bounds_result.get("filtered_df")
                out_csv = os.path.expanduser(f"~/simulation_traces/{self.scenario_name}_dkw_samples.csv")
                point_extractors.save_dataframe_to_csv(filtered_df, out_csv)
                # -------------------------------------------------------------

                print(f"[Strategist] 📊 DKW評価中 (Stage {self.dkw_stage}): 有効サンプル数 {current_samples}/{target_n_i}, 信頼区間幅 {interval_width:.3f} (目標 <= {self.dkw_target_epsilon})")
                print(f"             ↳ ワースト5%の推定値: {estimate:.3f} | 信頼区間: [{lower:.3f}, {upper:.3f}]")
                if is_converged:
                    msg = (f"SMC証明完了 (Stage {self.dkw_stage}): 信頼水準 {100*(1-self.dkw_total_delta):.1f}% で精度 {self.dkw_target_epsilon} を満たしました。\n"
                           f"👉 指定領域におけるワースト5%の {self.dkw_target_metric} は [{lower:.3f}, {upper:.3f}] の間に存在します。")
                    self._print_final_report(current_idx, "SMC (DKW)", msg)
                    return {"system_command": "stop", "reason": msg}
            else:
                current_samples = 0
                target_n_i = self.dkw_base_samples * (self.dkw_stage ** 2)

            if current_samples >= target_n_i:
                self.dkw_stage += 1
                print(f"[Strategist] 📊 精度未達。Stage {self.dkw_stage} (目標サンプル: {self.dkw_base_samples * (self.dkw_stage ** 2)}) へ移行し、追加サンプリングを行います。")

            reason = f"SMC: Sequential-DKW Sampling (Stage {self.dkw_stage})"
            
            # --- [追加] 非矩形領域からの棄却サンプリング (Rejection Sampling) ---
            import re
            MACROS = {
                "emp_safe": "(c_collision == 0)",
                "jama_safe": "(theory_margin_a_human >= 0.0)",
                "intersect_safe": "((c_collision == 0) and (theory_margin_a_human >= 0.0))",
                "union_safe": "((c_collision == 0) or (theory_margin_a_human >= 0.0))"
            }
            query_str = self.dkw_region
            if query_str != "custom":
                for key, val in MACROS.items():
                    query_str = re.sub(rf'\b{key}\b', val, query_str)
                    
            calc = TheoreticalSafetyCalculator(self.config) if TheoreticalSafetyCalculator else None
            
            # [最適化] ループ内で毎回計算しないよう、過去データの最大ループ番号を事前に算出しておく
            max_loop_num = 0
            if df_dataset is not None and 'loop_num' in df_dataset.columns:
                max_loop_val = df_dataset['loop_num'].max()
                if pd.notna(max_loop_val):
                    max_loop_num = int(max_loop_val)

            while len(self.task_cache) < self.CACHE_SIZE:
                # [修正] 専用のインデックスを使って順番通りにSobol列を取得し、統計の一様性を維持
                # [修正] 過去データと重複しないように、過去データの最大ループ番号をオフセットとして加算
                pt = self.get_sobol_point(max_loop_num + self.dkw_sobol_index)
                self.dkw_sobol_index += 1
                
                # 事前条件チェック
                if query_str != "custom":
                    check_pt = pt.copy()
                    # 理論値の事前計算 (JAMA理論の領域などを判定可能にする)
                    if calc:
                        theory_res = calc.evaluate(pt.get("dx0", 0), pt.get("ego_speed", 0), pt.get("npc_speed", 0))
                        check_pt.update(theory_res)
                    # シミュレーション結果依存の変数は、結果が出る前に棄却されないよう安全なダミー値をセット
                    check_pt["c_collision"] = 0
                    check_pt["min_ttc"] = 99.9
                    try:
                        if pd.DataFrame([check_pt]).query(query_str).empty:
                            continue # 領域条件を満たさないため棄却して次の点を生成
                    except Exception:
                        pass # 評価エラーの場合は安全のためそのまま通す
                
                pt["reason"] = reason
                self.task_cache.append(pt)
            
            # [修正] ここでattemptsを足すと二重加算になるため、通常のタスク発行と同様に +1 だけ行う
            self.dispatched_task_count += 1
            return self.task_cache.pop(0)

        # --- STEP 1: 初期探索 ---
        if self.current_phase == "STEP1" and (current_idx < self.INITIAL_EXPLORATION_LIMIT or num_violations == 0):
            if self.FOCUS_POINTS:
                # フォーカスモード時は、全体探索ではなく Focus の周辺をランダム探索する
                candidates = self.generate_candidate_points()
                best_point = candidates[np.random.randint(len(candidates))]
                result = {name: best_point[i] for i, name in enumerate(self.param_names)}
                result["reason"] = f"STEP1: Focus Neighborhood Search (V:{num_violations})"
                self.dispatched_task_count += 1
                return result
            else:
                result = {**self.get_sobol_point(current_idx), "reason": f"STEP1: Global Search (V:{num_violations})"}
                self.dispatched_task_count += 1
                return result
                
        # --- マージンモードのセーフティ (危険データが1つもない場合は境界が引けないためランダム探索でごまかす) ---
        if self.current_phase == "STEP3" and num_violations == 0:
            print("[Strategist] ⚠️ STEP3(マージンモード)で起動されましたが、データセットに衝突(1)の記録がありません。境界構築のため一時的にグローバル探索を実施します。")
            result = {**self.get_sobol_point(current_idx), "reason": "STEP3 Fallback: Global Search (No Violations)"}
            self.dispatched_task_count += 1
            return result

        # AI学習・予測
        self.estimator.train(target_column=best_target)
        candidates = self.generate_candidate_points()
        mean, std = self.estimator.predict_uncertainty(candidates)
        if mean is None:
            result = {**self.get_sobol_point(current_idx), "reason": "Fallback (Error)"}
            self.dispatched_task_count += 1
            return result

        # --- フェーズ移行判定 ---
        if self.current_phase == "STEP1":
            self.current_phase = "STEP2"

        if self.current_phase == "STEP2":
            self.step2_exploration_count += 1
            is_stable, shift_rate = self._evaluate_boundary_stability()
            
            if len(self.stability_history) >= self.STABILITY_HISTORY_LENGTH:
                print(f"[Strategist] {log_prefix}STEP2 | 探索回数: {self.step2_exploration_count}/{self.STEP2_MAX_EXPLORATION} | 境界反転率: {shift_rate*100:.2f}% (安定条件: {self.stability_streak}/{self.STABILITY_REQUIRED_STREAK})")
            else:
                print(f"[Strategist] {log_prefix}STEP2 | 探索回数: {self.step2_exploration_count}/{self.STEP2_MAX_EXPLORATION} | 定点観測データ収集中 ({len(self.stability_history)}/{self.STABILITY_HISTORY_LENGTH})")
            
            if is_stable or self.step2_exploration_count >= self.STEP2_MAX_EXPLORATION:
                print(f"\n[Strategist] ✨ {log_prefix}STEP3へ移行完了。マージンの不確実性潰しを開始します。✨\n")
                self.current_phase = "STEP3"

        # --- 次のターゲットの選択 ---
        # [修正] ギリギリの境界だけでなく、「安全」と予測されている領域全体(mean <= 0.5)を対象とする
        safe_idx = np.where(mean <= 0.5)[0]

        best_indices = []

        if self.current_phase == "STEP3":
            if len(safe_idx) > 0:
                max_std = np.max(std[safe_idx])
                print(f"[Strategist] {log_prefix}STEP3 | 安全領域(mean<=0.5)候補数: {len(safe_idx)} | 最大不確実性 σ = {max_std:.4f} (目標 < {self.MARGIN_MAX_UNCERTAINTY})")
                
                if max_std < self.MARGIN_MAX_UNCERTAINTY:
                    self._print_final_report(current_idx, best_target, "安全領域の死角(不確実性)を完全に排除しました")
                    return {"system_command": "stop", "reason": "Safe Area Verification Complete"}
                
                # [修正] 最大不確実性を持つ上位のインデックスを複数取得してキャッシュ用にする
                # 分散ワーカーが同じ局所領域ばかり探索しないよう、上位プールからランダム抽出して多様性を確保
                sorted_idx = np.argsort(std[safe_idx])[::-1]
                top_candidates = safe_idx[sorted_idx[:self.CACHE_SIZE * 5]]
                best_indices = np.random.choice(top_candidates, size=min(len(top_candidates), self.CACHE_SIZE), replace=False).tolist()
                
                # [追加] もし安全領域の候補がCACHE_SIZE(6個)に満たない場合、司令塔の推論ループによる詰まりを防ぐため、
                # 足りない分を全体の不確実性が高い場所から補充して確実に6個確保する。
                if len(best_indices) < self.CACHE_SIZE:
                    all_sorted_idx = np.argsort(std)[::-1]
                    for idx in all_sorted_idx:
                        if idx not in best_indices:
                            best_indices.append(idx)
                        if len(best_indices) >= self.CACHE_SIZE:
                            break
                            
                reason = f"STEP3: Safe Area Cleanup (σ={max_std:.4f})"
            else:
                print(f"[Strategist] {log_prefix}STEP3 | 安全と予測される領域がありません。バックアップ探索を実施します。")
                sorted_idx = np.argsort(std)[::-1]
                top_candidates = sorted_idx[:self.CACHE_SIZE * 5]
                best_indices = np.random.choice(top_candidates, size=min(len(top_candidates), self.CACHE_SIZE), replace=False).tolist()
                reason = f"STEP3: Backup Search (M:{mean[best_indices[0]] if len(best_indices)>0 else 0:.2f})"
        else:
            dice = np.random.rand()
            if dice < 0.3:
                sorted_idx = np.argsort(std)[::-1]
                top_candidates = sorted_idx[:self.CACHE_SIZE * 5]
                best_indices = np.random.choice(top_candidates, size=min(len(top_candidates), self.CACHE_SIZE), replace=False).tolist()
                reason = "STEP2: Exploration (Max σ)"
            else:
                sorted_idx = np.argsort(np.abs(mean - 0.5))
                top_candidates = sorted_idx[:self.CACHE_SIZE * 5]
                best_indices = np.random.choice(top_candidates, size=min(len(top_candidates), self.CACHE_SIZE), replace=False).tolist()
                reason = "STEP2: Boundary 0.5"
        
        if current_idx >= self.MAX_SAMPLES:
            return {"system_command": "stop", "reason": "Max Samples Reached"}

        # 選ばれた上位の候補をキャッシュに保存
        for b_idx in best_indices:
            best_point = candidates[b_idx]
            result = {name: best_point[i] for i, name in enumerate(self.param_names)}
            result["reason"] = "[FOCUS] " + reason if self.FOCUS_POINTS else reason
            self.task_cache.append(result)
        
        self.dispatched_task_count += 1
        return self.task_cache.pop(0)

    def _print_final_report(self, num_samples, target, reason):
        print("\n" + "="*60 + f"\n🎉 [検証完了] {reason}\n" + "="*60)
        print(f"・総実行回数: {num_samples}回\n・主要ターゲット: {target}")
        print("="*60 + "\n")

    def generate_candidate_points(self, num=None):
        num_points = num if num is not None else self.num_candidates
        if not self.FOCUS_POINTS:
            # 従来の全体探索モード (一様分布)
            cols = [np.random.uniform(self.active_bounds[n][0], self.active_bounds[n][1], num_points) for n in self.param_names]
            return np.column_stack(cols)
        else:
            # 集中探索(Focus)モード: 指定されたポイントの周辺に正規分布で生成
            cols = []
            num_per_point = num_points // len(self.FOCUS_POINTS)
            
            for name in self.param_names:
                param_range = self.config.PARAM_RANGES[name][1] - self.config.PARAM_RANGES[name][0]
                std_dev = param_range * self.FOCUS_NOISE  # パラメータの幅に応じた標準偏差
                
                param_candidates = []
                for point in self.FOCUS_POINTS:
                    # 指定ポイントに該当のパラメータが無ければ範囲の中央を基準にする
                    center = point.get(name, sum(self.config.PARAM_RANGES[name])/2.0)
                    samples = np.random.normal(loc=center, scale=std_dev, size=num_per_point)
                    param_candidates.extend(samples)
                
                # 端数合わせ
                while len(param_candidates) < num_points:
                    param_candidates.append(np.random.uniform(self.config.PARAM_RANGES[name][0], self.config.PARAM_RANGES[name][1]))
                    
                # 定義された範囲外にはみ出た値をクリップ（制限）する
                clipped = np.clip(param_candidates[:num_points], self.config.PARAM_RANGES[name][0], self.config.PARAM_RANGES[name][1])
                cols.append(clipped)
                
            return np.column_stack(cols)
