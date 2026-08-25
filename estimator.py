#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
from types import SimpleNamespace
import pandas as pd
import numpy as np
import point_extractors
from evaluation.binomial_ci import (
    calculate_binomial_confidence_interval as calculate_binomial_confidence_interval_from_df,
    evaluate_and_summarize_binomial_ci as evaluate_and_summarize_binomial_ci_from_df,
)
from evaluation.dkw import (
    calculate_dkw_bounds as calculate_dkw_bounds_from_df,
    calculate_quantile_with_dkw as calculate_quantile_with_dkw_from_df,
    evaluate_and_summarize_dkw as evaluate_and_summarize_dkw_from_df,
    evaluate_and_summarize_dkw_multiple as evaluate_and_summarize_dkw_multiple_from_df,
)
from evaluation.gp_boundary import (
    GPBoundaryModel,
    fit_gp_boundary_model,
    predict_uncertainty as predict_gp_boundary_uncertainty,
    prepare_training_data_frame,
)
from targets.bbsl.ft4d_bridge import (
    evaluate_ft4d_from_bbsl_batch_outputs as evaluate_ft4d_from_bbsl_batch_outputs_bridge,
    evaluate_ft4d_from_bbsl_output as evaluate_ft4d_from_bbsl_output_bridge,
    run_bbsl_and_evaluate_ft4d as run_bbsl_and_evaluate_ft4d_bridge,
)
from targets.bbsl.batch_loop import (
    bbsl_batch_matches_config as bbsl_batch_matches_config_bridge,
    ensure_bbsl_clean_baseline as ensure_bbsl_clean_baseline_bridge,
    expected_bbsl_batch_tree_mode as expected_bbsl_batch_tree_mode_bridge,
    infer_bbsl_batch_tree_mode as infer_bbsl_batch_tree_mode_bridge,
    load_bbsl_resume_state as load_bbsl_resume_state_bridge,
    run_bbsl_noisy_batch_once as run_bbsl_noisy_batch_once_bridge,
    run_bbsl_until_ft4d_confident as run_bbsl_until_ft4d_confident_bridge,
    summarize_ft4d_completion as summarize_ft4d_completion_bridge,
)

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
        self.boundary_model: GPBoundaryModel | None = None
        self.scaler = None
        
        # モデル定義: ガウス過程回帰
        # 予測値の平均(μ)だけでなく、不確実性(σ)を算出するために最適化
        self.model = None
        
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
        `evaluation/gp_boundary.py` の正式 API へ委譲する。
        """
        df_dataset = self.load_dataset()
        return prepare_training_data_frame(
            df_dataset,
            target_column=target_column,
            feature_names=self.feature_names,
        )

    def train(self, target_column):
        """
        指定されたターゲット指標の境界線を学習。
        GP 学習本体は `evaluation/gp_boundary.py` を正本とする。
        """
        df = self.load_training_data(target_column)
        if df is None:
            self.boundary_model = None
            self.model = None
            self.scaler = None
            self.is_trained = False
            return False
        try:
            boundary_model = fit_gp_boundary_model(
                df,
                target_column=target_column,
                feature_names=self.feature_names,
            )
            if boundary_model is None:
                self.boundary_model = None
                self.model = None
                self.scaler = None
                self.is_trained = False
                return False
            self.boundary_model = boundary_model
            self.model = boundary_model.model
            self.scaler = boundary_model.scaler
            self.is_trained = True
            return True
        except Exception as e:
            self.boundary_model = None
            self.model = None
            self.scaler = None
            self.is_trained = False
            print(f"[Estimator] ❌ 学習失敗: {e}")
            return False

    def predict_uncertainty(self, X_new):
        """
        未実行地点の平均予測値と、モデルの「自信のなさ（不確実性）」を算出。
        予測本体は `evaluation/gp_boundary.py` の正式 API に委譲する。
        """
        if not self.is_trained or self.boundary_model is None:
            return None, None
        return predict_gp_boundary_uncertainty(self.boundary_model, X_new)

    def calculate_dkw_bounds(self, target_column, delta=0.05, bounds=None, region="custom", df=None, use_kde_weighting=False):
        """
        【ステップ1〜4】DKW (Dvoretzky-Kiefer-Wolfowitz-Massart) 不等式による信頼帯の構築
        経験的累積分布関数 (ECDF) と、信頼水準 (1-delta) に基づく絶対的な信頼帯を計算します。
        """
        if df is None:
            df = self.load_dataset()
        return calculate_dkw_bounds_from_df(
            df,
            target_column=target_column,
            delta=delta,
            bounds=bounds,
            region=region,
            use_kde_weighting=use_kde_weighting,
            feature_names=self.feature_names,
        )
        
    def calculate_quantile_with_dkw(self, target_column, q=0.05, delta=0.05, bounds=None, region="custom", df=None, use_kde_weighting=False):
        """
        【ステップ5】対象指標（分位数）の導出
        例: 95%の信頼水準 (delta=0.05) で、下位5% (q=0.05) の最小TTCがどの範囲にあるかを数学的に保証する。
        """
        if df is None:
            df = self.load_dataset()
        return calculate_quantile_with_dkw_from_df(
            df,
            target_column=target_column,
            q=q,
            delta=delta,
            bounds=bounds,
            region=region,
            use_kde_weighting=use_kde_weighting,
            feature_names=self.feature_names,
        )

    def evaluate_and_summarize_dkw(self, target_column, df, q=0.05, delta=0.05, epsilon=0.15):
        """
        指定されたデータフレームに対してDKW評価を行い、
        評価状態や信頼区間などの結果サマリーを辞書で返す。
        外部モジュール（CLIツールやStrategist）から評価機能として直接利用するためのメソッド。
        """
        return evaluate_and_summarize_dkw_from_df(
            df,
            target_column=target_column,
            q=q,
            delta=delta,
            epsilon=epsilon,
        )

    def evaluate_and_summarize_dkw_multiple(self, target_columns, df, q=0.05, delta_total=0.05, epsilon=0.15, use_kde_weighting=False, region="custom", bounds=None):
        """
        複数指標の同時保証を行う。
        ボンフェローニ補正を用いて、各指標のエラー予算(delta)を分割して評価する。
        """
        return evaluate_and_summarize_dkw_multiple_from_df(
            df,
            target_columns=target_columns,
            q=q,
            delta_total=delta_total,
            epsilon=epsilon,
            use_kde_weighting=use_kde_weighting,
            region=region,
            bounds=bounds,
            feature_names=self.feature_names,
        )

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
        return calculate_binomial_confidence_interval_from_df(
            df,
            target_column=target_column,
            confidence_level=confidence_level,
            method=method,
            bounds=bounds,
            region=region,
            reason_pattern=reason_pattern,
        )

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
        if df is None:
            df = self.load_dataset()
        return evaluate_and_summarize_binomial_ci_from_df(
            df,
            target_column=target_column,
            confidence_level=confidence_level,
            method=method,
            bounds=bounds,
            region=region,
            reason_pattern=reason_pattern,
        )

    # Legacy reference block:
    # The methods below are kept for regression comparison and behavior
    # reference only. New BBSL/FT4D development must go to
    # targets/bbsl/profile.py, targets/bbsl/ft4d_bridge.py, and
    # targets/bbsl/batch_loop.py instead of extending this section.
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

    def _merge_sigma_pf_assumptions(self, base, overrides):
        merged = dict(base or {})
        if overrides:
            merged.update(overrides)
        return merged

    def _with_sigma_pf_assumption_overrides(self, built, overrides):
        if not overrides:
            return built
        updated = dict(built)
        updated["sigma_pf_assumptions"] = self._merge_sigma_pf_assumptions(
            built.get("sigma_pf_assumptions", {}),
            overrides,
        )
        return updated

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
        slim_report = self._strip_ft4d_dataset_payloads(report)
        return {
            "tree_mode": tree_mode,
            "event_inputs": {
                event_id: {
                    "condition_name": condition_name,
                    "total_count": built["events"][condition_name]["total_count"],
                    "correct_count": built["events"][condition_name]["correct_count"],
                    "error_count": built["events"][condition_name]["error_count"],
                    "sigma_pb": built["events"][condition_name]["sigma_pb"],
                    "recognition_test": recognition_test_payloads.get(condition_name),
                }
                for event_id, condition_name in mapping.items()
            },
            "local_ft4d_report": slim_report,
            "rendered_tree": visualizer.render_tree(),
            "top_sigma_pe": slim_report["tree"]["sigma_pe"],
        }

    def evaluate_ft4d_from_bbsl_output(
        self,
        output_json_path,
        *,
        tree_mode="basic",
        sigma_pf_source=None,
        and_rule=None,
        sigma_pf_assumption_overrides=None,
    ):
        return evaluate_ft4d_from_bbsl_output_bridge(
            output_json_path,
            tree_mode=tree_mode,
            sigma_pf_source=sigma_pf_source,
            and_rule=and_rule,
            sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
        )

    def evaluate_ft4d_from_bbsl_batch_outputs(
        self,
        clean_output_json_path,
        batch_output_paths,
        *,
        tree_mode="basic",
        sigma_pf_source="dataset",
        sigma_pb_mode="delta-clean",
        and_rule="min",
        sigma_pf_assumption_overrides=None,
    ):
        return evaluate_ft4d_from_bbsl_batch_outputs_bridge(
            clean_output_json_path,
            batch_output_paths,
            tree_mode=tree_mode,
            sigma_pf_source=sigma_pf_source,
            sigma_pb_mode=sigma_pb_mode,
            and_rule=and_rule,
            sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
        )

    def _iter_bbsl_batch_outputs(self, adapter, batch_output_paths):
        for path in batch_output_paths:
            payload = adapter.load_output(path)
            payload["source_output_json"] = path
            yield payload

    def _strip_ft4d_dataset_payloads(self, value):
        if isinstance(value, dict):
            return {
                key: self._strip_ft4d_dataset_payloads(item)
                for key, item in value.items()
                if key not in {"dataset_d", "dataset_e", "universal_dataset"}
            }
        if isinstance(value, list):
            return [self._strip_ft4d_dataset_payloads(item) for item in value]
        return value

    def _flatten_ft4d_tree_nodes(self, node):
        nodes = [node]
        for child in node.get("children", []):
            nodes.extend(self._flatten_ft4d_tree_nodes(child))
        return nodes

    def _summarize_ft4d_completion(self, ft4d_result, min_confidence):
        return summarize_ft4d_completion_bridge(
            ft4d_result,
            min_confidence=min_confidence,
        )

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
        sigma_pf_assumption_overrides=None,
    ):
        return run_bbsl_and_evaluate_ft4d_bridge(
            target_repo=target_repo,
            mini=mini,
            max_images=max_images,
            tree=tree,
            sigma_pf_source=sigma_pf_source,
            sigma_pb_mode=sigma_pb_mode,
            and_rule=and_rule,
            detect_timeout=detect_timeout,
            reuse_existing_output=reuse_existing_output,
            sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
        )

    def ensure_bbsl_clean_baseline(
        self,
        *,
        target_repo,
        mini=False,
        max_images=None,
        detect_timeout=None,
        reuse_clean_baseline=True,
    ):
        return ensure_bbsl_clean_baseline_bridge(
            target_repo=target_repo,
            mini=mini,
            max_images=max_images,
            detect_timeout=detect_timeout,
            reuse_clean_baseline=reuse_clean_baseline,
        )

    def run_bbsl_noisy_batch_once(
        self,
        *,
        target_repo,
        mini=False,
        max_images=None,
        batch_id,
        master_seed,
        conditions,
        clean_success_path,
        salt_pepper_density_range=(0.01, 0.08),
        occlusion_severity_range=(0.2, 0.5),
        blur_kernel_range=(5, 11),
        detect_timeout=None,
    ):
        return run_bbsl_noisy_batch_once_bridge(
            target_repo=target_repo,
            mini=mini,
            max_images=max_images,
            batch_id=batch_id,
            master_seed=master_seed,
            conditions=conditions,
            clean_success_path=clean_success_path,
            salt_pepper_density_range=salt_pepper_density_range,
            occlusion_severity_range=occlusion_severity_range,
            blur_kernel_range=blur_kernel_range,
            detect_timeout=detect_timeout,
        )

    def _expected_bbsl_batch_tree_mode(self, tree):
        return expected_bbsl_batch_tree_mode_bridge(tree)

    def _infer_bbsl_batch_tree_mode(self, payload):
        return infer_bbsl_batch_tree_mode_bridge(payload)

    def _bbsl_batch_matches_config(
        self,
        payload,
        *,
        tree,
        master_seed,
        max_images,
        salt_pepper_density_range,
        occlusion_severity_range,
        blur_kernel_range,
        allowed_conditions,
    ):
        return bbsl_batch_matches_config_bridge(
            payload,
            tree=tree,
            master_seed=master_seed,
            max_images=max_images,
            salt_pepper_density_range=salt_pepper_density_range,
            occlusion_severity_range=occlusion_severity_range,
            blur_kernel_range=blur_kernel_range,
            allowed_conditions=allowed_conditions,
        )

    def load_bbsl_resume_state(
        self,
        *,
        target_repo,
        mini,
        tree,
        sigma_pf_source,
        sigma_pb_mode,
        and_rule,
        master_seed,
        conditions,
        salt_pepper_density_range,
        occlusion_severity_range,
        blur_kernel_range,
        min_confidence,
        resume_batches,
        ensure_clean_kwargs,
        sigma_pf_assumption_overrides=None,
    ):
        return load_bbsl_resume_state_bridge(
            target_repo=target_repo,
            mini=mini,
            tree=tree,
            sigma_pf_source=sigma_pf_source,
            sigma_pb_mode=sigma_pb_mode,
            and_rule=and_rule,
            master_seed=master_seed,
            conditions=conditions,
            salt_pepper_density_range=salt_pepper_density_range,
            occlusion_severity_range=occlusion_severity_range,
            blur_kernel_range=blur_kernel_range,
            min_confidence=min_confidence,
            resume_batches=resume_batches,
            ensure_clean_kwargs=ensure_clean_kwargs,
            sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
        )

    def run_bbsl_until_ft4d_confident(
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
        master_seed=1000,
        conditions=None,
        salt_pepper_density_range=(0.01, 0.08),
        occlusion_severity_range=(0.2, 0.5),
        blur_kernel_range=(5, 11),
        reuse_clean_baseline=True,
        resume_batches=True,
        max_batches=None,
        max_total_trials=500000,
        no_progress_patience=3,
        min_confidence=0.95,
        sigma_pf_assumption_overrides=None,
    ):
        return run_bbsl_until_ft4d_confident_bridge(
            target_repo=target_repo,
            mini=mini,
            max_images=max_images,
            tree=tree,
            sigma_pf_source=sigma_pf_source,
            sigma_pb_mode=sigma_pb_mode,
            and_rule=and_rule,
            detect_timeout=detect_timeout,
            master_seed=master_seed,
            conditions=conditions,
            salt_pepper_density_range=salt_pepper_density_range,
            occlusion_severity_range=occlusion_severity_range,
            blur_kernel_range=blur_kernel_range,
            reuse_clean_baseline=reuse_clean_baseline,
            resume_batches=resume_batches,
            max_batches=max_batches,
            max_total_trials=max_total_trials,
            no_progress_patience=no_progress_patience,
            min_confidence=min_confidence,
            sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
        )


def create_ft4d_only_estimator():
    return SafetyEstimator(
        scenario_name="ft4d_bridge",
        config=SimpleNamespace(PARAM_RANGES={}),
    )
