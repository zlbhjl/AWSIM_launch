#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
from types import SimpleNamespace
import pandas as pd
import numpy as np
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C
from sklearn.preprocessing import StandardScaler
from scipy.stats import gaussian_kde, beta, norm
import point_extractors

class SafetyEstimator:
    def __init__(self, scenario_name, config, traces_dir="~/simulation_traces"):
        """
        汎用安全性推定器 (Gaussian Process Regression)
        Config-Driven アーキテクチャに基づき、あらゆるシナリオに即座に適応します。
        """
        self.traces_dir = os.path.expanduser(traces_dir)
        self.dataset_file = os.path.join(self.traces_dir, f"{scenario_name}_dataset.csv")
        self.base_dataset_file = os.path.join(self.traces_dir, f"{scenario_name}_dataset_base.csv")
        
        self.config = config
        self.scaler = StandardScaler()
        
        # モデル定義: ガウス過程回帰
        # 予測値の平均(μ)だけでなく、不確実性(σ)を算出するために最適化
        kernel = C(1.0, (1e-3, 1e3)) * RBF(1.0, (1e-2, 1e2))
        self.model = GaussianProcessRegressor(
            kernel=kernel, 
            alpha=0.01, 
            n_restarts_optimizer=5, 
            random_state=42
        )
        
        self.is_trained = False
        # [汎用化] 入力パラメータ名を Config のキーから自動取得
        param_ranges = getattr(self.config, "PARAM_RANGES", {}) if self.config is not None else {}
        self.feature_names = list(param_ranges.keys())

    def load_dataset(self):
        """
        統合されたデータセットCSVを読み込む。
        過去のベースデータ(_base.csv)が存在する場合は結合して返す。
        """
        df_list = []
        if os.path.exists(self.base_dataset_file):
            try:
                df_list.append(pd.read_csv(self.base_dataset_file, engine='python', on_bad_lines='skip'))
            except Exception as e:
                print(f"[Estimator] ❌ ベースデータセットCSVの読み込み失敗: {e}")
                
        if os.path.exists(self.dataset_file):
            try:
                df_list.append(pd.read_csv(self.dataset_file, engine='python', on_bad_lines='skip'))
            except Exception as e:
                print(f"[Estimator] ❌ データセットCSVの読み込み失敗: {e}")

        if not df_list:
            return None
            
        if len(df_list) == 1:
            return df_list[0]
            
        return pd.concat(df_list, ignore_index=True)

    def load_training_data(self, target_column):
        """
        データセットを読み込み、学習用の (X, y) を作成。
        """
        df_dataset = self.load_dataset()
        if df_dataset is None:
            return None

        # ターゲット指標の存在確認
        if target_column not in df_dataset.columns:
            print(f"[Estimator] ⚠️ 指標 '{target_column}' が見つかりません。")
            return None

        # --- 新機能: 異常データのフィルタリング ---
        # 1. 各種評価指標で解析エラー (-1) が発生したデータを除外
        for col in df_dataset.columns:
            if col.startswith("c_") or col.startswith("formula_"):
                df_dataset = df_dataset[~df_dataset[col].isin([-1, "-1", -1.0])]
                
        
        # --- [追加] 既存データに含まれる TTC の論理矛盾（すり抜け）を学習前に補正 ---
        if "c_collision" in df_dataset.columns:
            collision_mask = df_dataset["c_collision"].isin([1, "1", 1.0])
            for col in df_dataset.columns:
                if col.startswith("c_ttc_"):
                    df_dataset.loc[collision_mask, col] = 1
                    
        ttc_cols = sorted([c for c in df_dataset.columns if c.startswith('c_ttc_')], key=lambda x: float(x.split('_')[-1]))
        for i in range(len(ttc_cols) - 1):
            df_dataset.loc[df_dataset[ttc_cols[i]].isin([1, "1", 1.0]), ttc_cols[i+1]] = 1

        # --- [追加] 特徴量の列に文字列（ズレ等）が混入しているとScikit-Learnがクラッシュするため、強制的に数値に変換 ---
        for col in self.feature_names:
            df_dataset[col] = pd.to_numeric(df_dataset[col], errors='coerce')

        # 欠損値の除去と、0/1 (Boolean) データへの絞り込み
        essential_cols = ["loop_num", target_column] + self.feature_names
        df_dataset = df_dataset.dropna(subset=essential_cols)
        df_valid = df_dataset[df_dataset[target_column].isin([0, 1])]
        
        # 学習には「安全(0)」と「危険(1)」の両方のサンプルが必要
        if len(df_valid) < 2 or df_valid[target_column].nunique() < 2:
            return None

        # --- [追加] GPの計算爆発(O(N^3))を防ぐためのスマートなデータ間引き (Active Data Pruning) ---
        MAX_TRAIN_SAMPLES = 1500
        if len(df_valid) > MAX_TRAIN_SAMPLES:
            # 1. 絶対に残す「重要なエッジケース」の条件
            # 衝突した、またはニアミス(TTCが1.5s未満、または接近距離が2.0m未満)だったデータ
            is_critical = (df_valid.get('c_collision', 0) == 1) | \
                          (df_valid.get('min_ttc', 999.0) < 1.5) | \
                          (df_valid.get('min_distance', 999.0) < 2.0)
            
            # 2. 直近のデータ(最新の境界探索トレンド)も一定数残す
            recent_threshold = df_valid['loop_num'].max() - 500
            is_recent = df_valid['loop_num'] > recent_threshold
            
            must_keep_mask = is_critical | is_recent
            df_must_keep = df_valid[must_keep_mask]
            df_others = df_valid[~must_keep_mask]
            
            # 3. 安全で古いデータ(others)から、上限に収まるようにランダムサンプリング
            remain_count = MAX_TRAIN_SAMPLES - len(df_must_keep)
            if remain_count > 0 and len(df_others) > remain_count:
                df_others_sampled = df_others.sample(n=remain_count, random_state=42)
                df_valid = pd.concat([df_must_keep, df_others_sampled])
            else:
                df_valid = df_must_keep
            
        return df_valid

    def train(self, target_column):
        """
        指定されたターゲット指標の境界線を学習。
        """
        df = self.load_training_data(target_column)
        if df is None:
            return False
        
        X = df[self.feature_names].values
        y = df[target_column].values

        # 特徴量を標準化（スケーリング）して学習効率を向上
        X_scaled = self.scaler.fit_transform(X)

        try:
            self.model.fit(X_scaled, y)
            self.is_trained = True
            return True
        except Exception as e:
            print(f"[Estimator] ❌ 学習失敗: {e}")
            return False

    def predict_uncertainty(self, X_new):
        """
        未実行地点の平均予測値と、モデルの「自信のなさ（不確実性）」を算出。
        """
        if not self.is_trained:
            return None, None
            
        X_new_scaled = self.scaler.transform(X_new)
        # ガウス過程回帰の核心: return_std=True で標準偏差(σ)を取得
        mean, std = self.model.predict(X_new_scaled, return_std=True)
        return mean, std

    def calculate_dkw_bounds(self, target_column, delta=0.05, bounds=None, region="custom", df=None, use_kde_weighting=False):
        """
        【ステップ1〜4】DKW (Dvoretzky-Kiefer-Wolfowitz-Massart) 不等式による信頼帯の構築
        経験的累積分布関数 (ECDF) と、信頼水準 (1-delta) に基づく絶対的な信頼帯を計算します。
        """
        if df is None:
            df = self.load_dataset()
            
        if df is None or df.empty or target_column not in df.columns:
            print(f"[Estimator] ⚠️ データセットがない、または指標 '{target_column}' が見つかりません。")
            return None

        try:
            df = point_extractors.filter_by_region_and_bounds(df, region=region, bounds=bounds)
        except Exception as e:
            print(f"[Estimator] ⚠️ {e}")
            return None

        # --- [追加] エラーデータの厳格な除外 ---
        # c_collision等に -1 が入っている完全なエラー行を事前に弾く
        if 'c_collision' in df.columns:
            df = df[~df['c_collision'].isin([-1, "-1", -1.0])]

        data = pd.to_numeric(df[target_column], errors='coerce').dropna()
        
        # --- [修正] 指標に応じたマイナス値の扱い ---
        # min_ttc, min_distance, z_margin は物理的に0未満にならないためマイナスをエラー(ゴミ)として弾くが、
        # min_ttb や theory_margin は「マイナス(手遅れ)」が正常な物理状態であるため評価に残す
        if target_column in ['min_ttc', 'min_distance', 'z_margin']:
            data = data[data >= 0]
            
        # --- [追加] 無限大(inf)の安全な処理 ---
        # TTCなどで衝突しなかった場合の np.inf を、シミュレーションの予測上限値(5.0)にクリップし、幅がinfに発散するのを防ぐ
        data.replace([np.inf, -np.inf], [5.0, -5.0], inplace=True)

        k_actual = len(data) # 実際のサンプル数
        if k_actual == 0:
            print(f"[Estimator] ⚠️ DKWバウンドを計算するための有効なサンプルがありません。")
            return None

        y_values = data.values
        valid_indices = data.index

        # --- [追加] KDEを用いた重要度サンプリング (Importance Sampling) による重み付け ---
        if use_kde_weighting and k_actual > 1:
            try:
                # パラメータ空間(X)の取得とスケーリング (scipy.stats.gaussian_kde は次元D x サンプル数N を要求)
                X_params = df.loc[valid_indices, self.feature_names].values
                X_scaled = self.scaler.fit_transform(X_params).T 
                
                # KDEでAIのサンプリング密度(偏り)を推定
                kde = gaussian_kde(X_scaled)
                dens = kde.evaluate(X_scaled)
                dens = np.clip(dens, 1e-10, None) # ゼロ除算防止
                
                # 一様分布を仮定した場合の重み (密度の逆数) を計算
                weights = 1.0 / dens
                
                # --- [追加] Weight Clipping (重み崩壊の防止) ---
                # 極端に密度の低い未知領域のサンプルが持つ異常な重みをカットし、ESSの崩壊を防ぐ
                clip_val = np.percentile(weights, 99)
                weights = np.clip(weights, 0.0, clip_val)
                
                # 正規化(合計を1)する
                weights /= np.sum(weights)
                
                # 加重ECDF (Weighted ECDF) の構築
                sort_idx = np.argsort(y_values)
                x_sorted = y_values[sort_idx]
                weights_sorted = weights[sort_idx]
                ecdf = np.cumsum(weights_sorted)
                
                # 有効サンプル数 (Effective Sample Size: ESS) の計算
                k_eff = 1.0 / np.sum(weights**2)
                print(f"  [Estimator] 💡 KDE重点サンプリング適用: 実サンプル数 {k_actual} -> 有効サンプル数(ESS) {k_eff:.1f}")
            except Exception as e:
                print(f"  [Estimator] ⚠️ KDEの計算に失敗しました (偏り補正が不可能なため、この状態でのDKW評価を中止します): {e}")
                return None
        else:
            x_sorted = np.sort(y_values)
            ecdf = np.arange(1, k_actual + 1) / k_actual
            k_eff = k_actual

        # DKW不等式による許容誤差 Δ (Delta) の計算
        delta_margin = np.sqrt(np.log(2.0 / delta) / (2 * k_eff))

        # 上限と下限の境界を算出 (0〜1の範囲にクリップ)
        lower_bound = np.maximum(ecdf - delta_margin, 0.0)
        upper_bound = np.minimum(ecdf + delta_margin, 1.0)

        return {
            "x": x_sorted, "ecdf": ecdf,
            "lower_bound": lower_bound, "upper_bound": upper_bound,
            "delta": delta, "delta_margin": delta_margin, "sample_size": k_eff,
            "filtered_df": df
        }
        
    def calculate_quantile_with_dkw(self, target_column, q=0.05, delta=0.05, bounds=None, region="custom", df=None, use_kde_weighting=False):
        """
        【ステップ5】対象指標（分位数）の導出
        例: 95%の信頼水準 (delta=0.05) で、下位5% (q=0.05) の最小TTCがどの範囲にあるかを数学的に保証する。
        """
        dkw_bounds = self.calculate_dkw_bounds(target_column, delta, bounds=bounds, region=region, df=df, use_kde_weighting=use_kde_weighting)
        if dkw_bounds is None: return None
            
        x, ecdf = dkw_bounds["x"], dkw_bounds["ecdf"]
        
        # 分位数の点推定と、DKWバウンドに基づく信頼区間(真の分位数の下限・上限)の計算
        return {
            "q": q,
            "confidence_level": 1 - delta,
            "estimate": x[np.searchsorted(ecdf, q)] if np.searchsorted(ecdf, q) < len(x) else x[-1],
            "lower_bound": x[np.searchsorted(dkw_bounds["upper_bound"], q)] if np.searchsorted(dkw_bounds["upper_bound"], q) < len(x) else x[-1],
            "upper_bound": x[np.searchsorted(dkw_bounds["lower_bound"], q)] if np.searchsorted(dkw_bounds["lower_bound"], q) < len(x) else x[-1],
            "sample_size": dkw_bounds["sample_size"],
            "filtered_df": dkw_bounds["filtered_df"]
        }

    def evaluate_and_summarize_dkw(self, target_column, df, q=0.05, delta=0.05, epsilon=0.15):
        """
        指定されたデータフレームに対してDKW評価を行い、
        評価状態や信頼区間などの結果サマリーを辞書で返す。
        外部モジュール（CLIツールやStrategist）から評価機能として直接利用するためのメソッド。
        """
        if df is None or df.empty or len(df) < 2:
            return {"status": "error", "message": "評価対象のデータが不足しています(2件以上必要)。"}

        bounds_result = self.calculate_quantile_with_dkw(
            target_column=target_column,
            q=q,
            delta=delta,
            df=df
        )

        if not bounds_result:
            return {"status": "error", "message": "DKW評価を実行できませんでした。"}

        return {
            "status": "success",
            "q": q,
            "delta": delta,
            "epsilon": epsilon,
            "estimate": bounds_result["estimate"],
            "lower_bound": bounds_result["lower_bound"],
            "upper_bound": bounds_result["upper_bound"],
            "interval_width": bounds_result["upper_bound"] - bounds_result["lower_bound"],
            "sample_size": bounds_result["sample_size"]
        }

    def evaluate_and_summarize_dkw_multiple(self, target_columns, df, q=0.05, delta_total=0.05, epsilon=0.15, use_kde_weighting=False, region="custom", bounds=None):
        """
        複数指標の同時保証を行う。
        ボンフェローニ補正を用いて、各指標のエラー予算(delta)を分割して評価する。
        """
        if df is None or df.empty or len(df) < 2:
            return {"status": "error", "message": "評価対象のデータが不足しています(2件以上必要)。"}

        M = len(target_columns)
        if M == 0:
            return {"status": "error", "message": "評価対象の指標が指定されていません。"}
            
        delta_i = delta_total / M  # ボンフェローニ補正によるエラー予算の分割

        results = {"status": "success", "metrics": {}}
        for target in target_columns:
            bounds_result = self.calculate_quantile_with_dkw(
                target_column=target, q=q, delta=delta_i, df=df, use_kde_weighting=use_kde_weighting,
                region=region, bounds=bounds
            )
            
            if not bounds_result:
                return {"status": "error", "message": f"指標 '{target}' のDKW評価に失敗しました。有効なサンプルが不足しています。"}
                
            results["metrics"][target] = {
                "estimate": bounds_result["estimate"], "lower_bound": bounds_result["lower_bound"],
                "upper_bound": bounds_result["upper_bound"], "interval_width": bounds_result["upper_bound"] - bounds_result["lower_bound"],
                "sample_size": bounds_result["sample_size"], "filtered_df": bounds_result["filtered_df"]
            }
        return results

    def calculate_binomial_confidence_interval(
        self,
        target_column,
        confidence_level=0.95,
        method="wilson",
        bounds=None,
        region="custom",
        df=None,
        reason_pattern=None,
    ):
        if df is None:
            df = self.load_dataset()

        if df is None or df.empty or target_column not in df.columns:
            print(f"[Estimator] ⚠️ データセットがない、または指標 '{target_column}' が見つかりません。")
            return None

        working_df = df.copy()
        if reason_pattern and "reason" in working_df.columns:
            reason_series = working_df["reason"].fillna("").astype(str)
            working_df = working_df[reason_series.str.contains(reason_pattern, na=False)]

        try:
            working_df = point_extractors.filter_by_region_and_bounds(
                working_df, region=region, bounds=bounds
            )
        except Exception as e:
            print(f"[Estimator] ⚠️ {e}")
            return None

        if working_df is None or working_df.empty:
            print("[Estimator] ⚠️ 指定条件に一致するデータがありません。")
            return None

        if "c_collision" in working_df.columns:
            working_df = working_df[~working_df["c_collision"].isin([-1, "-1", -1.0])]

        target = pd.to_numeric(working_df[target_column], errors="coerce")
        valid_mask = target.isin([0, 1])
        valid_df = working_df[valid_mask].copy()
        if valid_df.empty:
            print(f"[Estimator] ⚠️ 指標 '{target_column}' の有効な 0/1 データがありません。")
            return None

        target_valid = pd.to_numeric(valid_df[target_column], errors="coerce")
        n = int(len(target_valid))
        k = int((target_valid == 1).sum())
        p_hat = k / n
        alpha = 1.0 - confidence_level

        if method == "wilson":
            z = norm.ppf(1.0 - alpha / 2.0)
            denom = 1.0 + (z ** 2) / n
            center = (p_hat + (z ** 2) / (2.0 * n)) / denom
            half_width = (
                z
                * np.sqrt((p_hat * (1.0 - p_hat) / n) + (z ** 2) / (4.0 * (n ** 2)))
                / denom
            )
            lower = max(0.0, center - half_width)
            upper = min(1.0, center + half_width)
        elif method == "clopper-pearson":
            lower = 0.0 if k == 0 else float(beta.ppf(alpha / 2.0, k, n - k + 1))
            upper = 1.0 if k == n else float(beta.ppf(1.0 - alpha / 2.0, k + 1, n - k))
        else:
            raise ValueError(f"Unsupported binomial CI method: {method}")

        return {
            "target_column": target_column,
            "method": method,
            "confidence_level": confidence_level,
            "sample_size": n,
            "success_count": k,
            "estimate": p_hat,
            "lower_bound": lower,
            "upper_bound": upper,
            "interval_width": upper - lower,
            "filtered_df": valid_df,
        }

    def evaluate_and_summarize_binomial_ci(
        self,
        target_column,
        confidence_level=0.95,
        method="wilson",
        bounds=None,
        region="custom",
        df=None,
        reason_pattern=None,
    ):
        result = self.calculate_binomial_confidence_interval(
            target_column=target_column,
            confidence_level=confidence_level,
            method=method,
            bounds=bounds,
            region=region,
            df=df,
            reason_pattern=reason_pattern,
        )
        if not result:
            return {"status": "error", "message": "二項信頼区間を計算できませんでした。"}

        return {
            "status": "success",
            "target_column": target_column,
            "method": method,
            "confidence_level": confidence_level,
            "sample_size": result["sample_size"],
            "success_count": result["success_count"],
            "estimate": result["estimate"],
            "lower_bound": result["lower_bound"],
            "upper_bound": result["upper_bound"],
            "interval_width": result["interval_width"],
            "filtered_df": result["filtered_df"],
        }

    def _ft4d_config_dir(self):
        return os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "verification_core",
            "ft4d",
            "config",
        )

    def _ft4d_tree_path(self, tree_mode):
        if tree_mode not in {"basic", "combined"}:
            raise ValueError(
                "tree_mode must be 'basic' or 'combined', "
                f"got {tree_mode!r}"
            )
        filename = "tree_basic.json" if tree_mode == "basic" else "tree_bbsl.json"
        return os.path.join(self._ft4d_config_dir(), filename)

    def _ft4d_event_mapping(self, tree_mode):
        basic_mapping = {
            "SALT_PEPPER": "salt_pepper",
            "OCCLUSION": "occlusion",
            "BLUR": "blur",
        }
        combined_mapping = {
            "SALT_PEPPER": "salt_pepper",
            "OCCLUSION": "occlusion",
            "BLUR": "blur",
            "SP_OCC": "sp_occ",
            "SP_BLUR": "sp_blur",
            "OCC_BLUR": "occ_blur",
            "ALL_THREE": "all_three",
        }
        return basic_mapping if tree_mode == "basic" else combined_mapping

    def _calculate_ft4d_tree(self, built, *, tree_mode, sigma_pf_source, and_rule):
        from verification_core.ft4d import FT4DCalculator, FT4DVisualizer, FaultTree
        from verification_core.ft4d.statistics import (
            RecognitionTestResult,
            bonferroni_child_delta,
            evaluate_recognition_test,
        )

        tree = FaultTree.from_json(self._ft4d_tree_path(tree_mode))
        calc = FT4DCalculator(
            tree,
            sigma_pf_source=sigma_pf_source,
            and_rule=and_rule,
        )
        calc.set_universal_dataset(built["universal_dataset"])

        sigma_pf_assumptions = built["sigma_pf_assumptions"]
        statistical_test_config = built.get("statistical_test_config", {})
        recognition_tests_by_tree = built.get("recognition_tests_by_tree", {})
        mapping = self._ft4d_event_mapping(tree_mode)
        root_delta = statistical_test_config.get(
            "root_delta",
            statistical_test_config.get("delta"),
        )
        epsilon = statistical_test_config.get("epsilon")
        expected_recognition_rate = statistical_test_config.get(
            "expected_recognition_rate"
        )
        computed_child_delta = None
        if root_delta is not None:
            computed_child_delta = bonferroni_child_delta(
                root_delta,
                len(mapping),
            )
        recognition_test_payloads = {}
        for event_id, condition_name in mapping.items():
            if condition_name not in built["events"]:
                raise KeyError(
                    f"Condition {condition_name!r} is required for tree_mode "
                    f"{tree_mode!r} but not present in the BBSL output."
                )

            metrics = built["events"][condition_name]
            calc.set_basic_event(
                event_id,
                sigma_pf=sigma_pf_assumptions[event_id],
                sigma_pb=metrics["sigma_pb"],
            )
            calc.set_basic_event_datasets(
                event_id,
                metrics["dataset_d"],
                metrics["dataset_e"],
            )
            recognition_test = None
            if (
                metrics["total_count"] > 0
                and epsilon is not None
                and expected_recognition_rate is not None
                and computed_child_delta is not None
            ):
                recognition_test = evaluate_recognition_test(
                    correct_count=metrics["correct_count"],
                    expected_recognition_rate=expected_recognition_rate,
                    epsilon=epsilon,
                    delta=computed_child_delta,
                    n=metrics["total_count"],
                )
            raw_recognition_test = (
                recognition_tests_by_tree.get(tree_mode, {}).get(condition_name)
                or metrics.get("recognition_test")
            )
            if recognition_test is None and raw_recognition_test is not None:
                recognition_test = RecognitionTestResult(**raw_recognition_test)
            if recognition_test is not None:
                recognition_test_payloads[condition_name] = {
                    "n": recognition_test.n,
                    "required_sample_size": recognition_test.required_sample_size,
                    "has_required_sample_size": recognition_test.has_required_sample_size,
                    "correct_count": recognition_test.correct_count,
                    "required_correct_count": recognition_test.required_correct_count,
                    "meets_correct_count": recognition_test.meets_correct_count,
                    "sample_recognition_rate": recognition_test.sample_recognition_rate,
                    "required_sample_rate": recognition_test.required_sample_rate,
                    "expected_recognition_rate": recognition_test.expected_recognition_rate,
                    "epsilon": recognition_test.epsilon,
                    "delta": recognition_test.delta,
                    "confidence": recognition_test.confidence,
                    "passed": recognition_test.passed,
                }
                calc.set_basic_event_test(
                    event_id,
                    recognition_test,
                )

        report = calc.calculate()
        visualizer = FT4DVisualizer(tree)
        return {
            "tree_mode": tree_mode,
            "event_inputs": {
                event_id: {
                    "condition_name": condition_name,
                    "dataset_d": sorted(
                        built["events"][condition_name]["dataset_d"]
                    ),
                    "dataset_e": sorted(
                        built["events"][condition_name]["dataset_e"]
                    ),
                    "total_count": built["events"][condition_name]["total_count"],
                    "correct_count": built["events"][condition_name]["correct_count"],
                    "error_count": built["events"][condition_name]["error_count"],
                    "sigma_pb": built["events"][condition_name]["sigma_pb"],
                    "recognition_test": recognition_test_payloads.get(condition_name),
                }
                for event_id, condition_name in mapping.items()
            },
            "local_ft4d_report": report,
            "rendered_tree": visualizer.render_tree(),
            "top_sigma_pe": report["tree"]["sigma_pe"],
        }

    def evaluate_ft4d_from_bbsl_output(
        self,
        output_json_path,
        *,
        tree_mode="basic",
        sigma_pf_source=None,
        and_rule=None,
    ):
        from adapters.bbsl import BBSLExperimentAdapter, BBSLEventSetBuilder

        adapter = BBSLExperimentAdapter()
        output = adapter.load_output(output_json_path)
        builder = BBSLEventSetBuilder(adapter)
        built = builder.build_event_inputs(output)

        effective_sigma_pf_source = sigma_pf_source or built["sigma_pf_source"]
        effective_and_rule = and_rule or built["and_rule"]

        result = {
            "source_output_json": output_json_path,
            "tree_mode": tree_mode,
            "active_conditions": built["active_conditions"],
            "sigma_pf_source": effective_sigma_pf_source,
            "sigma_pb_mode": built["sigma_pb_mode"],
            "and_rule": effective_and_rule,
        }

        if tree_mode == "all":
            basic = self._calculate_ft4d_tree(
                built,
                tree_mode="basic",
                sigma_pf_source=effective_sigma_pf_source,
                and_rule=effective_and_rule,
            )
            combined = self._calculate_ft4d_tree(
                built,
                tree_mode="combined",
                sigma_pf_source=effective_sigma_pf_source,
                and_rule=effective_and_rule,
            )
            result["local_runs"] = {
                "basic": basic,
                "combined": combined,
            }
            result["top_sigma_pe"] = {
                "basic": basic["top_sigma_pe"],
                "combined": combined["top_sigma_pe"],
            }
        else:
            result.update(
                self._calculate_ft4d_tree(
                    built,
                    tree_mode=tree_mode,
                    sigma_pf_source=effective_sigma_pf_source,
                    and_rule=effective_and_rule,
                )
            )

        return result

    def run_bbsl_and_evaluate_ft4d(
        self,
        *,
        target_repo,
        mini=False,
        max_images=None,
        tree="basic",
        sigma_pf_source="dataset",
        sigma_pb_mode="delta-clean",
        and_rule="min",
        detect_timeout=None,
        reuse_existing_output=False,
    ):
        from adapters.bbsl import BBSLExperimentAdapter, run_bbsl_experiment

        if reuse_existing_output:
            output_json_path = BBSLExperimentAdapter().default_output_path(
                target_repo,
                mini,
            )
        else:
            output_json_path = run_bbsl_experiment(
                target_repo,
                mini=mini,
                max_images=max_images,
                tree=tree,
                sigma_pf_source=sigma_pf_source,
                sigma_pb_mode=sigma_pb_mode,
                and_rule=and_rule,
                detect_timeout=detect_timeout,
            )

        return self.evaluate_ft4d_from_bbsl_output(
            output_json_path,
            tree_mode=tree,
            sigma_pf_source=sigma_pf_source,
            and_rule=and_rule,
        )


def create_ft4d_only_estimator():
    return SafetyEstimator(
        scenario_name="ft4d_bridge",
        config=SimpleNamespace(PARAM_RANGES={}),
    )
