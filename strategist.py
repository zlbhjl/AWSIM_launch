#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import numpy as np
import pandas as pd
from evaluation.gp_boundary import GPBoundaryService
from evaluation.statistical_service import StatisticalEvaluationService
from redis_cluster import cluster_config
import point_extractors
from runtime.repository.strategy_dataset import StrategyDatasetRepository
from targets.bbsl.batch_loop import (
    run_bbsl_until_ft4d_confident,
)
from targets.bbsl.ft4d_bridge import (
    run_bbsl_and_evaluate_ft4d,
)
from targets.bbsl.underconfident_loop import run_bbsl_underconfident_loop

try:
    from theoretical_calculator import TheoreticalSafetyCalculator
except ImportError:
    TheoreticalSafetyCalculator = None


def _load_case_definition_from_config(config, scenario_name):
    explicit_loader = getattr(config, "get_case_definition", None)
    if callable(explicit_loader):
        loaded = dict(explicit_loader())
        return {
            "scenario_type": loaded.get("scenario_type", scenario_name),
            "repeat_count": int(loaded.get("repeat_count", 0)),
            "timeout_sec": float(loaded.get("timeout_sec", 200.0)),
            "target_npcs": list(loaded.get("target_npcs", [])),
            "param_ranges": dict(loaded.get("param_ranges", {})),
            "fixed_params": dict(loaded.get("fixed_params", {})),
        }

    return {
        "scenario_type": getattr(config, "SCENARIO_TYPE", scenario_name),
        "repeat_count": int(getattr(config, "REPEAT_COUNT", 0)),
        "timeout_sec": float(getattr(config, "TIMEOUT_SEC", 200.0)),
        "target_npcs": list(getattr(config, "TARGET_NPCS", [])),
        "param_ranges": dict(getattr(config, "PARAM_RANGES", {})),
        "fixed_params": dict(getattr(config, "FIXED_PARAMS", {})),
    }


def _load_strategy_settings_from_config(config):
    explicit_loader = getattr(config, "get_strategy_settings", None)
    if callable(explicit_loader):
        return dict(explicit_loader())

    return {
        "target_priorities": list(getattr(config, "TARGET_PRIORITIES", [])),
        "initial_exploration_limit": getattr(config, "INITIAL_EXPLORATION_LIMIT", 100),
        "min_samples": getattr(config, "MIN_SAMPLES", 500),
        "max_samples": getattr(config, "MAX_SAMPLES", 2000),
        "stability_reference_points": getattr(config, "STABILITY_REFERENCE_POINTS", 2000),
        "stability_history_length": getattr(config, "STABILITY_HISTORY_LENGTH", 50),
        "stability_hysteresis": getattr(config, "STABILITY_HYSTERESIS", (0.40, 0.60)),
        "stability_shift_threshold": getattr(config, "STABILITY_SHIFT_THRESHOLD", 0.01),
        "stability_required_streak": getattr(config, "STABILITY_REQUIRED_STREAK", 3),
        "step2_max_exploration": getattr(config, "STEP2_MAX_EXPLORATION", 500),
        "margin_range": getattr(config, "MARGIN_RANGE", (0.3, 0.48)),
        "margin_max_uncertainty": getattr(config, "MARGIN_MAX_UNCERTAINTY", 0.05),
        "focus_points": list(getattr(config, "FOCUS_POINTS", [])),
        "focus_noise": getattr(config, "FOCUS_NOISE", 0.05),
        "dkw_target_metric": getattr(config, "DKW_TARGET_METRIC", "min_ttc"),
        "dkw_target_metrics": list(getattr(config, "DKW_TARGET_METRICS", ["min_ttc", "min_distance"])),
        "binomial_ci_target": getattr(config, "BINOMIAL_CI_TARGET", "c_collision"),
        "binomial_ci_method": getattr(config, "BINOMIAL_CI_METHOD", "wilson"),
        "binomial_ci_confidence": getattr(config, "BINOMIAL_CI_CONFIDENCE", 0.95),
        "binomial_ci_target_width": getattr(config, "BINOMIAL_CI_TARGET_WIDTH", 0.02),
        "binomial_ci_min_samples": getattr(config, "BINOMIAL_CI_MIN_SAMPLES", 100),
    }

class ActiveLearningStrategist:
    def __init__(
        self,
        scenario_name,
        config,
        num_candidates=10000,
        focus_points=None,
        run_mode="explore",
        dkw_bounds=None,
        dkw_region="custom",
        dkw_pure_smc=False,
        dkw_simultaneous=False,
        max_samples=None,
        binomial_target=None,
        binomial_method=None,
        binomial_confidence=None,
        binomial_target_width=None,
        binomial_min_samples=None,
    ):
        self.scenario_name = scenario_name
        self.config = config
        self.num_candidates = num_candidates
        self.run_mode = run_mode
        self.dkw_bounds = dkw_bounds
        self.dkw_region = dkw_region
        self.dkw_pure_smc = dkw_pure_smc
        self.dkw_simultaneous = dkw_simultaneous
        self.case_definition = _load_case_definition_from_config(config, scenario_name)
        self.strategy_settings = _load_strategy_settings_from_config(config)
        self.binomial_target = (
            binomial_target
            or self.strategy_settings.get("binomial_ci_target", "c_collision")
        )
        self.binomial_method = (
            binomial_method
            or self.strategy_settings.get("binomial_ci_method", "wilson")
        )
        self.binomial_confidence = (
            binomial_confidence if binomial_confidence is not None
            else self.strategy_settings.get("binomial_ci_confidence", 0.95)
        )
        self.binomial_target_width = (
            binomial_target_width if binomial_target_width is not None
            else self.strategy_settings.get("binomial_ci_target_width", 0.02)
        )
        self.binomial_min_samples = (
            binomial_min_samples if binomial_min_samples is not None
            else self.strategy_settings.get("binomial_ci_min_samples", 100)
        )
        
        self.param_ranges = dict(self.case_definition["param_ranges"])
        self.param_names = list(self.param_ranges.keys())
        self.dataset_repository = StrategyDatasetRepository(scenario_name)
        self.boundary_model_service = GPBoundaryService(feature_names=self.param_names)
        self.statistics_service = StatisticalEvaluationService(feature_names=self.param_names)
        self.dim = len(self.param_names)

        # --- 設定ファイル(config)から戦略パラメータを動的に取得 ---
        self.target_priorities = list(self.strategy_settings.get("target_priorities", []))
        self.INITIAL_EXPLORATION_LIMIT = self.strategy_settings.get("initial_exploration_limit", 100)
        self.MIN_SAMPLES = self.strategy_settings.get("min_samples", 500)
        self.MAX_SAMPLES = self.strategy_settings.get("max_samples", 2000)
        self.max_samples = max_samples or self.MAX_SAMPLES

        # [変更] フェーズ移行・終了条件の新しいパラメータ
        self.STABILITY_REFERENCE_POINTS = self.strategy_settings.get("stability_reference_points", 2000)
        self.STABILITY_HISTORY_LENGTH = self.strategy_settings.get("stability_history_length", 50)
        self.STABILITY_HYSTERESIS = tuple(self.strategy_settings.get("stability_hysteresis", (0.40, 0.60)))
        self.STABILITY_SHIFT_THRESHOLD = self.strategy_settings.get("stability_shift_threshold", 0.01)
        self.STABILITY_REQUIRED_STREAK = self.strategy_settings.get("stability_required_streak", 3)
        self.STEP2_MAX_EXPLORATION = self.strategy_settings.get("step2_max_exploration", 500)
        self.MARGIN_RANGE = tuple(self.strategy_settings.get("margin_range", (0.3, 0.48)))
        self.MARGIN_MAX_UNCERTAINTY = self.strategy_settings.get("margin_max_uncertainty", 0.05)
        
        # コマンドライン引数で渡された focus_points を使用 (Configに依存しない)
        self.FOCUS_POINTS = focus_points
        self.FOCUS_NOISE = self.strategy_settings.get("focus_noise", 0.05)

        # --- [変更] 抽出ロジックを外部モジュールに委譲 ---
        if self.run_mode in point_extractors.EXTRACTORS:
            extractor_func = point_extractors.EXTRACTORS[self.run_mode]
            df = self.dataset_repository.load_dataset()
            extracted_points = extractor_func(df, self.param_names, self.config)
            if extracted_points:
                self.FOCUS_POINTS = extracted_points
                print(f"[Strategist] 🎯 {len(self.FOCUS_POINTS)} 件のターゲットポイントを自動設定しました。")
            else:
                print(f"[Strategist] ⚠️ 条件に合致するポイントはありませんでした。")

        # --- [追加] Sequential-DKW (SMC) 単独証明モード用のパラメータ ---
        if self.run_mode == "dkw":
            self.dkw_stage = 1
            self.dkw_random_index = 0  # [修正] DKW専用の乱数インデックスを新設し、独立性を保証する
            self.dkw_base_samples = getattr(self.config, 'DKW_BASE_SAMPLES', 50)     # 基本サンプル数 n
            self.dkw_total_delta = getattr(self.config, 'DKW_TOTAL_DELTA', 0.05)       # 最終的な信頼水準 (例: 95%)
            self.dkw_target_epsilon = getattr(self.config, 'DKW_TARGET_EPSILON', 0.15) # 求める精度 (信頼区間の幅)
            self.dkw_target_metric = self.strategy_settings.get("dkw_target_metric", "min_ttc")
            
            # --- [追加] 過去データから安全領域(Bounds)を自動計算 ---
            if self.dkw_region != "custom":
                df = self.dataset_repository.load_dataset()
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
                
            if self.dkw_pure_smc:
                print(f"[Strategist] ⚠️ 純粋SMCモード有効: 過去のAI探索データは排除し、SMCサンプリングのみで評価します。")
                
            if self.dkw_simultaneous:
                target_metrics = self.strategy_settings.get("dkw_target_metrics", ['min_ttc', 'min_distance'])
                print(f"[Strategist] 🛡️ 多重指標同時保証モード有効: {target_metrics} を同時評価し、ボンフェローニ補正を適用します。")

            # --- [追加] 現在の有効サンプル数と目標のプレビューを表示 ---
            df_dataset = self.dataset_repository.load_dataset()
            if self.dkw_pure_smc:
                if df_dataset is not None and not df_dataset.empty and 'reason' in df_dataset.columns:
                    df_dkw = df_dataset[df_dataset['reason'].str.contains('SMC', na=False)]
                else:
                    df_dkw = pd.DataFrame() # [修正] SMCデータがない場合に None を渡すと全データが再ロードされてしまうバグを防止
            else:
                df_dkw = df_dataset
                
            try:
                f_df = point_extractors.filter_by_region_and_bounds(df_dkw, region=self.dkw_region, bounds=self.dkw_bounds)
                curr_s = len(f_df) if f_df is not None else 0
                tgt_s = self.dkw_base_samples * (self.dkw_stage ** 2)
                print(f"[Strategist] 📈 現在のSMC有効サンプル数: {curr_s} 件 (Stage {self.dkw_stage} 目標: {tgt_s} 件)")
            except Exception:
                pass

        # --- [追加] dkw_fixed: 指定回数一様サンプリング → 事後一括DKW評価 ---
        if self.run_mode == "dkw_fixed":
            self.dkw_random_index = 0
            self.dkw_total_delta = getattr(self.config, 'DKW_TOTAL_DELTA', 0.05)
            self.dkw_target_epsilon = getattr(self.config, 'DKW_TARGET_EPSILON', 0.15)
            self.dkw_target_metric = self.strategy_settings.get("dkw_target_metric", "min_ttc")

            if self.dkw_region != "custom":
                df = self.dataset_repository.load_dataset()
                if df is not None and not df.empty:
                    try:
                        f_df = point_extractors.filter_by_region_and_bounds(df, region=self.dkw_region)
                        if not f_df.empty:
                            self.dkw_bounds = {col: [float(f_df[col].min()), float(f_df[col].max())] for col in self.param_names if col in f_df.columns}
                            print(f"[Strategist] 📊 DKW固定サンプリング: 抽出条件 '{self.dkw_region}' に基づきサンプリング領域を自動算出しました -> {self.dkw_bounds}")
                        else:
                            print(f"[Strategist] ⚠️ 指定された条件('{self.dkw_region}')に該当するデータがありません。")
                    except Exception as e:
                        print(f"[Strategist] ⚠️ 領域計算に失敗しました: {e}")
                else:
                    print(f"[Strategist] ⚠️ データセットが存在しないため、領域の自動算出ができません。")

            print(f"[Strategist] 📊 DKW固定サンプリングモード: 指定回数 {self.max_samples} 回をフルで回し、最後に一括DKW評価します。")
            if self.dkw_bounds:
                print(f"[Strategist] 📍 サンプリング領域: {self.dkw_bounds}")
            if self.dkw_simultaneous:
                target_metrics = self.strategy_settings.get("dkw_target_metrics", ['min_ttc', 'min_distance'])
                print(f"[Strategist] 🛡️ 多重指標同時保証モード: {target_metrics}")

        if self.run_mode == "binomial_ci":
            self.binomial_random_index = 0
            print(
                f"[Strategist] 📊 Binomial CI モード: target={self.binomial_target} "
                f"| method={self.binomial_method} | confidence={self.binomial_confidence:.2%} "
                f"| target_width<={self.binomial_target_width:.4f}"
            )
            if self.dkw_region == "custom" and self.dkw_bounds:
                print(f"[Strategist] 📍 評価領域(手動指定): {self.dkw_bounds}")
            elif not self.dkw_bounds and self.dkw_region == "custom":
                print("[Strategist] 📍 評価領域: パラメータ空間全体の一様ランダム")
            else:
                print(f"[Strategist] 📍 評価領域フィルタ: {self.dkw_region}")
            print(
                f"[Strategist] 🎯 停止条件: サンプル数 >= {self.binomial_min_samples} かつ "
                f"95%信頼区間幅 <= {self.binomial_target_width:.4f}"
            )

        # アクティブなサンプリング範囲を決定 (DKWモードでの範囲指定があれば上書き)
        self.active_bounds = {}
        for name in self.param_names:
            if self.run_mode in ["dkw", "dkw_fixed", "binomial_ci"] and self.dkw_bounds and name in self.dkw_bounds:
                self.active_bounds[name] = self.dkw_bounds[name]
            else:
                self.active_bounds[name] = self.param_ranges[name]

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
        
        # [修正] 過去のデータを読み込んだ際、過去のエラーをすべてリカバリーしようとしてDKW証明が止まるのを防ぐため、
        # 起動時点の最大ループ番号を初期値として設定する
        self.last_recovered_loop = 0
        df_init = self.dataset_repository.load_dataset()
        if df_init is not None and 'loop_num' in df_init.columns:
            max_loop = df_init['loop_num'].max()
            if pd.notna(max_loop):
                self.last_recovered_loop = int(max_loop)
        self.boundary_gap_initial_summary = None
        self.boundary_gap_cycle = 1
        if self.run_mode == "boundary_gap":
            self.boundary_gap_initial_summary = point_extractors.summarize_boundary_gap_progress(
                df_init,
                self.param_names,
                self.config,
                target_points=self.FOCUS_POINTS,
            )

    def _print_boundary_gap_progress(self):
        df = self.dataset_repository.load_dataset()
        summary = point_extractors.summarize_boundary_gap_progress(
            df,
            self.param_names,
            self.config,
            target_points=self.FOCUS_POINTS,
        )
        initial = self.boundary_gap_initial_summary or {}

        print("\n[Strategist] 📐 boundary_gap 検証後の境界セル集計")
        print(
            f"  - 要追加セル: {initial.get('candidate_cells', 0)} -> {summary['candidate_cells']} "
            f"(差分 {summary['candidate_cells'] - initial.get('candidate_cells', 0):+d})"
        )
        print(
            f"  - 追加観測で十分に埋まった危険セル: {summary['densified_cells']} "
            f"| 明確化セル: {summary['clarified_cells']}"
        )

        target_rows = summary.get("target_cells", [])
        if target_rows:
            traces_dir = os.path.expanduser("~/simulation_traces")
            out_csv = os.path.join(traces_dir, f"{self.scenario_name}_boundary_gap_progress.csv")
            pd.DataFrame(target_rows).to_csv(out_csv, index=False)
            remaining = sum(1 for row in target_rows if row.get("status") == "needs_more_data")
            print(
                f"  - 今回のターゲット {len(target_rows)} セル中、まだ薄いセル: {remaining}"
            )
            print(f"[Strategist] 💾 ターゲットごとの集計を保存しました: {out_csv}")
        return summary

    def _refresh_boundary_gap_targets(self):
        df = self.dataset_repository.load_dataset()
        summary = self._print_boundary_gap_progress()
        extractor_func = point_extractors.EXTRACTORS.get("boundary_gap")
        next_points = extractor_func(df, self.param_names, self.config) if extractor_func else []

        if next_points:
            self.FOCUS_POINTS = next_points
            self.focus_exact_test_count = 0
            self.boundary_gap_cycle += 1
            print(
                f"[Strategist] 🔁 boundary_gap 継続: Cycle {self.boundary_gap_cycle} として "
                f"{len(self.FOCUS_POINTS)} 件のターゲットを再設定しました。"
            )
            return {"continue": True, "summary": summary}

        self.FOCUS_POINTS = []
        return {"continue": False, "summary": summary}

    def _flatten_ft4d_tree_nodes(self, node, labels, tree_mode, path=None):
        if path is None:
            path = []

        label = labels.get(node["id"], node["id"])
        current_path = path + [label]
        entry = {
            "tree_mode": tree_mode,
            "node_id": node["id"],
            "label": label,
            "path": " > ".join(current_path),
            "type": node.get("type", "gate"),
            "gate": node.get("gate"),
            "sigma_pf": node.get("sigma_pf", 0.0),
            "sigma_pe": node.get("sigma_pe", 0.0),
            "confidence": node.get("confidence", 1.0),
            "confidence_delta": node.get("confidence_delta", 0.0),
            "has_recognition_test": "recognition_test" in node,
            "recognition_test": node.get("recognition_test"),
        }

        flattened = [entry]
        for child in node.get("children", []):
            flattened.extend(
                self._flatten_ft4d_tree_nodes(
                    child,
                    labels,
                    tree_mode,
                    path=current_path,
                )
            )
        return flattened

    def _classify_ft4d_gap_reason(self, event, threshold):
        if event["type"] == "basic":
            if not event["has_any_recognition_test"]:
                return "missing-recognition-test"
            if event["insufficient_sample_occurrences"] > 0:
                return "insufficient-samples"
            if event["failed_test_occurrences"] > 0:
                return "failed-recognition-test"
            if event["min_confidence"] < threshold:
                return "low-confidence"
            return "ok"

        if event["min_confidence"] < threshold:
            return "child-confidence-propagation"
        return "ok"

    def _recommend_ft4d_action(self, gap_reason):
        actions = {
            "missing-recognition-test": "add_recognition_test",
            "insufficient-samples": "collect_more_samples",
            "failed-recognition-test": "inspect_basic_event",
            "child-confidence-propagation": "inspect_child_events",
            "low-confidence": "review_confidence_target",
            "ok": "no_action",
        }
        return actions.get(gap_reason, "review_event")

    def _aggregate_ft4d_nodes(self, all_nodes, threshold):
        grouped = {}
        for node in all_nodes:
            event = grouped.setdefault(
                node["node_id"],
                {
                    "node_id": node["node_id"],
                    "label": node["label"],
                    "type": node["type"],
                    "gate": node["gate"],
                    "tree_modes": set(),
                    "occurrence_count": 0,
                    "paths": [],
                    "min_confidence": 1.0,
                    "max_confidence_delta": 0.0,
                    "max_sigma_pe": 0.0,
                    "max_sigma_pf": 0.0,
                    "has_any_recognition_test": False,
                    "failed_test_occurrences": 0,
                    "insufficient_sample_occurrences": 0,
                    "recognition_test_samples": [],
                },
            )

            event["tree_modes"].add(node["tree_mode"])
            event["occurrence_count"] += 1
            event["paths"].append(node["path"])
            event["min_confidence"] = min(event["min_confidence"], node["confidence"])
            event["max_confidence_delta"] = max(
                event["max_confidence_delta"],
                node["confidence_delta"],
            )
            event["max_sigma_pe"] = max(event["max_sigma_pe"], node["sigma_pe"])
            event["max_sigma_pf"] = max(event["max_sigma_pf"], node["sigma_pf"])
            event["has_any_recognition_test"] = (
                event["has_any_recognition_test"] or node["has_recognition_test"]
            )

            recognition_test = node.get("recognition_test")
            if recognition_test is not None:
                event["recognition_test_samples"].append(recognition_test)
                if not recognition_test.get("has_required_sample_size", True):
                    event["insufficient_sample_occurrences"] += 1
                if not recognition_test.get("passed", True):
                    event["failed_test_occurrences"] += 1

        aggregated_events = []
        reason_priority = {
            "insufficient-samples": 0,
            "failed-recognition-test": 1,
            "child-confidence-propagation": 2,
            "missing-recognition-test": 3,
            "low-confidence": 4,
            "ok": 5,
        }
        for event in grouped.values():
            event["tree_modes"] = sorted(event["tree_modes"])
            event["paths"] = sorted(set(event["paths"]))
            event["gap_reason"] = self._classify_ft4d_gap_reason(event, threshold)
            event["recommended_action"] = self._recommend_ft4d_action(
                event["gap_reason"]
            )
            event["is_underconfident"] = event["min_confidence"] < threshold
            event["priority_score"] = (
                0 if event["type"] == "basic" else 1,
                reason_priority.get(event["gap_reason"], 99),
                event["min_confidence"],
                -event["max_sigma_pe"],
                event["node_id"],
            )
            aggregated_events.append(event)

        aggregated_events.sort(key=lambda event: event["priority_score"])
        underconfident_events = [
            event for event in aggregated_events
            if event["is_underconfident"]
        ]
        return aggregated_events, underconfident_events

    def summarize_ft4d_confidence_gaps(self, ft4d_result, min_confidence=None):
        threshold = (
            min_confidence
            if min_confidence is not None
            else getattr(self.config, "FT4D_MIN_CONFIDENCE", 0.95)
        )

        local_runs = {}
        if ft4d_result.get("tree_mode") == "all":
            local_runs = ft4d_result.get("local_runs", {})
        else:
            local_runs = {
                ft4d_result["tree_mode"]: {
                    "local_ft4d_report": ft4d_result["local_ft4d_report"],
                    "top_sigma_pe": ft4d_result["top_sigma_pe"],
                }
            }

        all_nodes = []
        for tree_mode, run in local_runs.items():
            report = run["local_ft4d_report"]
            labels = report.get("labels", {})
            all_nodes.extend(
                self._flatten_ft4d_tree_nodes(
                    report["tree"],
                    labels,
                    tree_mode,
                )
            )

        underconfident_nodes = [
            node for node in all_nodes
            if node["confidence"] < threshold
        ]
        underconfident_nodes.sort(
            key=lambda node: (node["confidence"], -node["sigma_pe"], node["path"])
        )

        nodes_with_tests = [
            node for node in all_nodes
            if node["has_recognition_test"]
        ]
        aggregated_events, underconfident_events = self._aggregate_ft4d_nodes(
            all_nodes,
            threshold,
        )

        return {
            "threshold": threshold,
            "total_nodes": len(all_nodes),
            "total_unique_events": len(aggregated_events),
            "nodes_with_recognition_test": len(nodes_with_tests),
            "has_any_recognition_test": bool(nodes_with_tests),
            "underconfident_count": len(underconfident_nodes),
            "underconfident_nodes": underconfident_nodes,
            "underconfident_unique_count": len(underconfident_events),
            "aggregated_events": aggregated_events,
            "underconfident_events": underconfident_events,
            "all_nodes": all_nodes,
        }

    def inspect_bbsl_ft4d_confidence_gaps(
        self,
        *,
        target_repo,
        execution_mode="legacy",
        condition_policy="all",
        mini=False,
        max_images=None,
        tree="basic",
        sigma_pf_source="dataset",
        sigma_pb_mode="delta-clean",
        and_rule="min",
        detect_timeout=None,
        reuse_existing_output=False,
        master_seed=1000,
        conditions=None,
        salt_pepper_density_range=(0.01, 0.08),
        occlusion_severity_range=(0.2, 0.5),
        blur_kernel_range=(5, 11),
        refresh_clean_baseline=False,
        resume_batches=True,
        max_batches=None,
        max_total_trials=500000,
        no_progress_patience=3,
        min_confidence=None,
        sigma_pf_assumption_overrides=None,
    ):
        threshold = (
            min_confidence
            if min_confidence is not None
            else getattr(self.config, "FT4D_MIN_CONFIDENCE", 0.95)
        )
        if execution_mode == "batch-loop" and condition_policy == "underconfident":
            underconfident_result = run_bbsl_underconfident_loop(
                target_repo=target_repo,
                mini=mini,
                tree=tree,
                sigma_pf_source=sigma_pf_source,
                sigma_pb_mode=sigma_pb_mode,
                and_rule=and_rule,
                detect_timeout=detect_timeout,
                max_images=max_images,
                master_seed=master_seed,
                conditions=conditions,
                salt_pepper_density_range=salt_pepper_density_range,
                occlusion_severity_range=occlusion_severity_range,
                blur_kernel_range=blur_kernel_range,
                refresh_clean_baseline=refresh_clean_baseline,
                resume_batches=resume_batches,
                max_batches=max_batches,
                max_total_trials=max_total_trials,
                no_progress_patience=no_progress_patience,
                min_confidence=threshold,
                sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
                summarize_confidence_gaps_fn=self.summarize_ft4d_confidence_gaps,
            )
            ft4d_result = underconfident_result["ft4d_result"]
            confidence_summary = underconfident_result["confidence_summary"]
        elif execution_mode == "batch-loop":
            ft4d_result = run_bbsl_until_ft4d_confident(
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
                reuse_clean_baseline=not refresh_clean_baseline,
                max_batches=max_batches,
                max_total_trials=max_total_trials,
                no_progress_patience=no_progress_patience,
                min_confidence=threshold,
                sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
            )
            confidence_summary = self.summarize_ft4d_confidence_gaps(
                ft4d_result,
                min_confidence=min_confidence,
            )
        else:
            ft4d_result = run_bbsl_and_evaluate_ft4d(
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
            confidence_summary = self.summarize_ft4d_confidence_gaps(
                ft4d_result,
                min_confidence=min_confidence,
            )
        return {
            "execution_mode": execution_mode,
            "condition_policy": condition_policy,
            "ft4d_result": ft4d_result,
            "confidence_summary": confidence_summary,
        }

    def _log_dkw_history(self, records):
        """DKW評価の推移(収束過程)をCSVに追記記録する"""
        if not records: return
        df_hist = pd.DataFrame(records)
        out_csv = os.path.expanduser(f"~/simulation_traces/{self.scenario_name}_dkw_history.csv")
        file_exists = os.path.exists(out_csv)
        df_hist.to_csv(out_csv, mode='a', header=not file_exists, index=False)

    def _log_binomial_history(self, record):
        if not record:
            return
        df_hist = pd.DataFrame([record])
        out_csv = os.path.expanduser(f"~/simulation_traces/{self.scenario_name}_binomial_ci_history.csv")
        file_exists = os.path.exists(out_csv)
        df_hist.to_csv(out_csv, mode='a', header=not file_exists, index=False)

    def get_random_point(self, index):
        # [修正] DKW不等式の前提(i.i.d: 独立同分布)を厳密に満たすため、
        # 準乱数(Sobol列)ではなく純粋な一様乱数(Pseudo-Random)を使用する。
        # (中断からの再開時に重複・欠落を防ぐため、indexをシードに含めて一意の乱数を生成する)
        rng = np.random.default_rng(seed=42 + int(index))
        point_dict = {name: rng.uniform(self.active_bounds[name][0], self.active_bounds[name][1]) 
                      for name in self.param_names}
        return point_dict

    def _build_binomial_random_task(self, df_dataset):
        reason = (
            f"BINOMIAL_CI: target={self.binomial_target} "
            f"method={self.binomial_method} conf={self.binomial_confidence:.2f}"
        )
        calc = TheoreticalSafetyCalculator(self.config) if TheoreticalSafetyCalculator else None

        max_loop_num = 0
        if df_dataset is not None and 'loop_num' in df_dataset.columns:
            max_loop_val = df_dataset['loop_num'].max()
            if pd.notna(max_loop_val):
                max_loop_num = int(max_loop_val)

        random_offset = max_loop_num
        while len(self.task_cache) < self.CACHE_SIZE:
            pt = self.get_random_point(random_offset + self.binomial_random_index)
            self.binomial_random_index += 1

            if self.dkw_region != "custom":
                check_pt = pt.copy()
                if calc:
                    theory_res = calc.evaluate(
                        pt.get("dx0", 0),
                        pt.get("ego_speed", 0),
                        pt.get("npc_speed", 0),
                    )
                    check_pt.update(theory_res)
                check_pt["c_collision"] = 0
                check_pt["min_ttc"] = 99.9
                check_pt["min_distance"] = 99.9
                check_pt["min_ttb"] = 99.9
                try:
                    temp_df = pd.DataFrame([check_pt])
                    if point_extractors.filter_by_region_and_bounds(
                        temp_df, region=self.dkw_region
                    ).empty:
                        continue
                except Exception as e:
                    print(f"[Strategist] ⚠️ 二項ランダムサンプリング中にエラー発生 (破棄します): {e}")
                    continue

            pt["reason"] = reason
            self.task_cache.append(pt)

    def get_best_target(self, df):
        if df is None or len(df) == 0: return None
        for target in self.target_priorities:
            if target in df.columns:
                v = df[df[target].isin([0, 1])]
                if 1 in v[target].values and 0 in v[target].values: return target
        return None

    def _evaluate_boundary_stability(self):
        mean, _ = self.boundary_model_service.predict_uncertainty(self.reference_points)
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
            
        df_dataset = self.dataset_repository.load_dataset()
        
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
                        rng = self.param_ranges[name][1] - self.param_ranges[name][0]
                        # 3%の微小ノイズを加えて少しずらす (シミュレータのクラッシュ回避)
                        noise = np.random.normal(0, rng * 0.03) 
                        try:
                            val = float(err_row[name]) + noise
                        except (ValueError, TypeError, KeyError):
                            # CSVのズレ等で文字列が入っている場合は安全な中央値を使用する
                            val = (self.param_ranges[name][0] + self.param_ranges[name][1]) / 2.0 + noise
                        val = np.clip(val, self.param_ranges[name][0], self.param_ranges[name][1])
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
            # dkw_fixed + pure_smc の場合は SMCタグの新規データのみカウント
            if self.run_mode == "dkw_fixed" and self.dkw_pure_smc:
                sync_df = df_dataset[df_dataset.get('reason', '').str.contains('SMC', na=False)]
            else:
                sync_df = df_dataset
            if not sync_df.empty:
                max_loop_val = pd.to_numeric(sync_df['loop_num'], errors='coerce').max()
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
                result = {name: exact_point.get(name, sum(self.param_ranges[name])/2.0) for name in self.param_names}
                
                if self.run_mode == "verify_consistency":
                    mode_label = "CONSISTENCY"
                elif self.run_mode == "boundary_gap":
                    mode_label = "BOUNDARY_GAP"
                else:
                    mode_label = "FOCUS"
                if self.run_mode == "boundary_gap":
                    result["reason"] = (
                        f"[{mode_label}] Cycle {self.boundary_gap_cycle} "
                        f"Point {point_idx+1}/{len(self.FOCUS_POINTS)} "
                        f"(Repeat {repeat_idx}/{exact_repeats})"
                    )
                else:
                    result["reason"] = f"[{mode_label}] Exact Point {point_idx+1}/{len(self.FOCUS_POINTS)} (Repeat {repeat_idx}/{exact_repeats})"
                
                self.focus_exact_test_count += 1
                self.dispatched_task_count += 1
                return result
            elif self.run_mode == "boundary_gap":
                refresh = self._refresh_boundary_gap_targets()
                summary = refresh["summary"]
                if refresh["continue"]:
                    return self.decide_next_target()

                remaining = sum(
                    1 for row in summary.get("target_cells", [])
                    if row.get("status") == "needs_more_data"
                )
                msg = (
                    f"boundary_gap 検証完了: 残り要追加セル {summary['candidate_cells']} "
                    f"(最後のターゲットで未解消 {remaining})"
                )
                self._print_final_report(current_idx, "Boundary Gap", msg)
                return {"system_command": "stop", "reason": msg}
            elif self.run_mode == "verify_consistency":
                # --- 全反復テスト完了時の自動分類とDKW評価 ---
                print("\n[Strategist] 📊 全反復テスト完了。TTCの安定性を評価し、分類ごとのDKW証明を行います...")
                df = self.dataset_repository.load_dataset()
                if df is not None and not df.empty:
                    # [修正] 全データではなく、この検証モードで実行されたデータのみを分析対象とする
                    consistency_df = df[df['reason'].str.contains('\\[CONSISTENCY\\]', na=False)]
                    if consistency_df.empty:
                        print("[Strategist] ⚠️ 一貫性検証モードで実行されたデータが見つかりませんでした。")
                        return {"system_command": "stop", "reason": "No consistency data found"}

                    threshold = getattr(self.config, 'CONSISTENCY_THRESHOLD', 0.2)
                    target_metric = self.strategy_settings.get("dkw_target_metric", "min_ttc")
                    df_consistent, df_stochastic = point_extractors.classify_consistency(consistency_df, self.param_names, target_metric=target_metric, threshold=threshold, min_repeats=2)
                    
                    # --- [追加] 分類されたデータを個別のCSVとして保存 ---
                    traces_dir = os.path.expanduser(f"~/simulation_traces")
                    out_path_consistent = os.path.join(traces_dir, f"{self.scenario_name}_consistent_risk.csv")
                    point_extractors.save_dataframe_to_csv(df_consistent, out_path_consistent, f"[Strategist] 💾 確実なリスク領域のデータを保存しました: {out_path_consistent}")
                    out_path_stochastic = os.path.join(traces_dir, f"{self.scenario_name}_stochastic_risk.csv")
                    point_extractors.save_dataframe_to_csv(df_stochastic, out_path_stochastic, f"[Strategist] 💾 偶然のリスク領域のデータを保存しました: {out_path_stochastic}")
                    # --------------------------------------------------
                    
                    def print_dkw_result(title, target_df):
                        print(f"\n--- {title} (データ件数: {len(target_df)}) ---")
                        summary = self.statistics_service.summarize_dkw(
                            target_df,
                            target_column=self.strategy_settings.get("dkw_target_metric", "min_ttc"),
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

        # ============================================================
        # dkw_fixed: 指定回数一様サンプリング → 全データ揃ったら事後評価
        # ============================================================
        if self.run_mode == "dkw_fixed":
            if self.dispatched_task_count < self.max_samples:
                pt = self.get_random_point(self.dkw_random_index)
                self.dkw_random_index += 1
                pt["reason"] = f"SMC: Fixed Sampling ({self.dispatched_task_count+1}/{self.max_samples})"
                self.dispatched_task_count += 1
                return pt

            print(f"\n[Strategist] 🎯 指定回数 {self.max_samples} 回のサンプリング完了。最終DKW評価を実行します...")
            df_dataset = self.dataset_repository.load_dataset()
            if df_dataset is None or df_dataset.empty:
                return {"system_command": "stop", "reason": "Empty dataset"}

            if self.dkw_pure_smc:
                if 'reason' in df_dataset.columns:
                    df_dkw = df_dataset[df_dataset['reason'].str.contains('SMC', na=False)]
                else:
                    df_dkw = pd.DataFrame()
            else:
                df_dkw = df_dataset

            try:
                df_filtered = point_extractors.filter_by_region_and_bounds(df_dkw, region=self.dkw_region, bounds=self.dkw_bounds)
            except Exception as e:
                print(f"[Strategist] ⚠️ 領域フィルタリング失敗: {e}")
                df_filtered = df_dkw

            if df_filtered is None or df_filtered.empty:
                return {"system_command": "stop", "reason": "No data after region filtering"}

            print(f"[Strategist] 📊 最終評価: {len(df_filtered)} サンプルでDKW評価を実行します。")

            if self.dkw_simultaneous:
                target_metrics = self.strategy_settings.get("dkw_target_metrics", ['min_ttc', 'min_distance'])
                summary = self.statistics_service.summarize_dkw_multiple(
                    df_filtered,
                    target_columns=target_metrics,
                    q=0.05, delta_total=self.dkw_total_delta,
                    epsilon=self.dkw_target_epsilon, use_kde_weighting=False,
                    region="custom", bounds=None
                )
                if summary["status"] == "success":
                    for metric, res in summary["metrics"].items():
                        print(f"  [{metric}] ワースト5%推定値: {res['estimate']:.3f} | 信頼区間: [{res['lower_bound']:.3f}, {res['upper_bound']:.3f}] (幅: {res['interval_width']:.3f})")
                    msg = f"固定サンプリング({self.max_samples}回) + 最終DKW同時証明完了"
                else:
                    msg = f"DKW評価失敗: {summary.get('message', '不明')}"
            else:
                summary = self.statistics_service.summarize_dkw(
                    df_filtered,
                    target_column=self.dkw_target_metric,
                    q=0.05, delta=self.dkw_total_delta,
                    epsilon=self.dkw_target_epsilon
                )
                if summary["status"] == "success":
                    print(f"  [{self.dkw_target_metric}] ワースト5%推定値: {summary['estimate']:.3f} | 信頼区間: [{summary['lower_bound']:.3f}, {summary['upper_bound']:.3f}] (幅: {summary['interval_width']:.3f})")
                    msg = f"固定サンプリング({self.max_samples}回) + 最終DKW証明完了"
                else:
                    msg = f"DKW評価失敗: {summary.get('message', '不明')}"

            out_csv = os.path.expanduser(f"~/simulation_traces/{self.scenario_name}_dkw_samples.csv")
            point_extractors.save_dataframe_to_csv(df_filtered, out_csv, f"[Strategist] 💾 DKWサンプルを保存: {out_csv}")
            self._print_final_report(self.dispatched_task_count, "DKW Fixed", msg)
            return {"system_command": "stop", "reason": msg}

        if self.run_mode == "binomial_ci":
            reason_pattern = r"BINOMIAL_CI:"
            summary = self.statistics_service.summarize_binomial_ci(
                df_dataset,
                target_column=self.binomial_target,
                confidence_level=self.binomial_confidence,
                method=self.binomial_method,
                bounds=self.dkw_bounds,
                region=self.dkw_region,
                reason_pattern=reason_pattern,
            )

            if summary["status"] == "success":
                n = summary["sample_size"]
                k = summary["success_count"]
                width = summary["interval_width"]
                self._log_binomial_history({
                    "task_count": current_idx,
                    "metric": self.binomial_target,
                    "method": self.binomial_method,
                    "confidence_level": self.binomial_confidence,
                    "sample_size": n,
                    "success_count": k,
                    "estimate": summary["estimate"],
                    "lower_bound": summary["lower_bound"],
                    "upper_bound": summary["upper_bound"],
                    "interval_width": width,
                    "target_width": self.binomial_target_width,
                })
                print(
                    f"[Strategist] 📊 Binomial CI 評価中: n={n}, k={k}, "
                    f"p̂={summary['estimate']:.5f} | "
                    f"{self.binomial_confidence:.1%} CI=[{summary['lower_bound']:.5f}, {summary['upper_bound']:.5f}] "
                    f"(幅 {width:.5f}, 目標 <= {self.binomial_target_width:.5f})"
                )

                if n >= self.binomial_min_samples and width <= self.binomial_target_width:
                    out_csv = os.path.expanduser(f"~/simulation_traces/{self.scenario_name}_binomial_ci_samples.csv")
                    point_extractors.save_dataframe_to_csv(
                        summary["filtered_df"],
                        out_csv,
                        f"[Strategist] 💾 Binomial CI サンプルを保存: {out_csv}"
                    )
                    msg = (
                        f"Binomial CI 完了: {self.binomial_target} の推定値 {summary['estimate']:.5f}, "
                        f"{self.binomial_confidence:.1%} CI=[{summary['lower_bound']:.5f}, {summary['upper_bound']:.5f}]"
                    )
                    self._print_final_report(self.dispatched_task_count, "Binomial CI", msg)
                    return {"system_command": "stop", "reason": msg}

                if self.max_samples is not None and n >= self.max_samples:
                    msg = (
                        f"Binomial CI は max_samples={self.max_samples} に到達。"
                        f" 現在の {self.binomial_confidence:.1%} CI=[{summary['lower_bound']:.5f}, {summary['upper_bound']:.5f}]"
                    )
                    self._print_final_report(self.dispatched_task_count, "Binomial CI", msg)
                    return {"system_command": "stop", "reason": msg}

            self._build_binomial_random_task(df_dataset)
            self.dispatched_task_count += 1
            return self.task_cache.pop(0)

        # --- [追加] 単独モード: Sequential-DKW (SMC) による指定データの統計的検証 ---
        if self.run_mode == "dkw":
            delta_i = self.dkw_total_delta / (2 ** self.dkw_stage)
            
            if self.dkw_pure_smc:
                if df_dataset is not None and not df_dataset.empty and 'reason' in df_dataset.columns:
                    df_dkw = df_dataset[df_dataset['reason'].str.contains('SMC', na=False)]
                else:
                    df_dkw = pd.DataFrame()
                use_kde = False
            else:
                df_dkw = df_dataset
                use_kde = True
                
            if self.dkw_simultaneous:
                target_metrics = self.strategy_settings.get("dkw_target_metrics", ['min_ttc', 'min_distance'])
                summary = self.statistics_service.summarize_dkw_multiple(
                    df_dkw,
                    target_columns=target_metrics, q=0.05, delta_total=delta_i,
                    epsilon=self.dkw_target_epsilon, use_kde_weighting=use_kde,
                    region=self.dkw_region, bounds=self.dkw_bounds
                )
                
                if summary["status"] == "success":
                    metrics_res = summary["metrics"]
                    current_samples = list(metrics_res.values())[0]["sample_size"]
                    
                    history_records = []
                    for metric, res in metrics_res.items():
                        history_records.append({
                            "stage": self.dkw_stage,
                            "task_count": current_idx,
                            "metric": metric,
                            "ess": res["sample_size"],
                            "estimate": res["estimate"],
                            "lower_bound": res["lower_bound"],
                            "upper_bound": res["upper_bound"],
                            "interval_width": res["interval_width"],
                            "target_epsilon": self.dkw_target_epsilon
                        })
                    self._log_dkw_history(history_records)
                    
                    target_n_i = self.dkw_base_samples * (self.dkw_stage ** 2)
                    
                    is_converged = True
                    for metric, res in metrics_res.items():
                        if res["interval_width"] > self.dkw_target_epsilon:
                            is_converged = False
                            
                    print(f"[Strategist] 📊 DKW同時評価中 (Stage {self.dkw_stage}): 有効サンプル数 {current_samples:.1f}/{target_n_i} (目標区間幅 <= {self.dkw_target_epsilon})")
                    for metric, res in metrics_res.items():
                        print(f"             ↳ [{metric}] ワースト5%推定値: {res['estimate']:.3f} | 信頼区間: [{res['lower_bound']:.3f}, {res['upper_bound']:.3f}] (幅: {res['interval_width']:.3f})")
                        
                    if is_converged:
                        metrics_count = len(target_metrics)
                        individual_conf = (1.0 - (self.dkw_total_delta / metrics_count)) * 100.0
                        total_conf = (1.0 - self.dkw_total_delta) * 100.0
                        msg = (f"SMC同時証明完了 (Stage {self.dkw_stage}): \n"
                               f"システム全体のエラー予算({self.dkw_total_delta*100:.1f}%)を {metrics_count} つの指標に分割し、それぞれ {individual_conf:.2f}% の厳格な信頼水準で評価しました。\n"
                               f"👉 指定領域において、【 {total_conf:.1f}% の確率(同時信頼水準)で、最悪のデータ(ワースト5%)が以下の範囲に収束する 】ことが証明されました。\n")
                        for metric, res in metrics_res.items():
                            msg += f"   - {metric} : [{res['lower_bound']:.3f}, {res['upper_bound']:.3f}] (推定誤差幅: {res['interval_width']:.3f})\n"
                            
                        filtered_df = list(metrics_res.values())[0].get("filtered_df")
                        if filtered_df is not None:
                            out_csv = os.path.expanduser(f"~/simulation_traces/{self.scenario_name}_dkw_samples.csv")
                            point_extractors.save_dataframe_to_csv(filtered_df, out_csv)
                            
                        self._print_final_report(current_idx, "SMC (DKW Simultaneous)", msg)
                        return {"system_command": "stop", "reason": "SMC Simultaneous Verification Complete"}
                else:
                    current_samples = 0
                    target_n_i = self.dkw_base_samples * (self.dkw_stage ** 2)
            else:
                bounds_result = self.statistics_service.calculate_dkw_quantile(
                    df_dkw,
                    target_column=self.dkw_target_metric,
                    q=0.05,
                    delta=delta_i,
                    bounds=self.dkw_bounds,
                    region=self.dkw_region,
                    use_kde_weighting=use_kde
                )
                
                if bounds_result:
                    current_samples = bounds_result["sample_size"]
                    target_n_i = self.dkw_base_samples * (self.dkw_stage ** 2)
                    
                    interval_width = bounds_result["upper_bound"] - bounds_result["lower_bound"]
                    is_converged = interval_width <= self.dkw_target_epsilon
                    
                    lower, upper, estimate = bounds_result["lower_bound"], bounds_result["upper_bound"], bounds_result["estimate"]

                    history_records = [{
                        "stage": self.dkw_stage,
                        "task_count": current_idx,
                        "metric": self.dkw_target_metric,
                        "ess": current_samples,
                        "estimate": estimate,
                        "lower_bound": lower,
                        "upper_bound": upper,
                        "interval_width": interval_width,
                        "target_epsilon": self.dkw_target_epsilon
                    }]
                    self._log_dkw_history(history_records)

                print(f"[Strategist] 📊 DKW評価中 (Stage {self.dkw_stage}): 有効サンプル数 {current_samples}/{target_n_i}, 信頼区間幅 {interval_width:.3f} (目標 <= {self.dkw_target_epsilon})")
                print(f"             ↳ ワースト5%の推定値: {estimate:.3f} | 信頼区間: [{lower:.3f}, {upper:.3f}]")
                if is_converged:
                    msg = (f"SMC証明完了 (Stage {self.dkw_stage}): 信頼水準 {100*(1-self.dkw_total_delta):.1f}% で目標精度(誤差幅 <= {self.dkw_target_epsilon})に到達しました。\n"
                           f"👉 指定領域において、【 {100*(1-self.dkw_total_delta):.1f}% の確率(信頼水準)で、最悪のデータ(ワースト5%)が以下の範囲に収束する 】ことが証明されました。\n"
                           f"   - {self.dkw_target_metric} : [{lower:.3f}, {upper:.3f}] (推定誤差幅: {interval_width:.3f})")
                    self._print_final_report(current_idx, "SMC (DKW)", msg)
                    return {"system_command": "stop", "reason": msg}
                    filtered_df = bounds_result.get("filtered_df")
                    out_csv = os.path.expanduser(f"~/simulation_traces/{self.scenario_name}_dkw_samples.csv")
                    point_extractors.save_dataframe_to_csv(filtered_df, out_csv)

                    print(f"[Strategist] 📊 DKW評価中 (Stage {self.dkw_stage}): 有効サンプル数 {current_samples:.1f}/{target_n_i}, 信頼区間幅 {interval_width:.3f} (目標 <= {self.dkw_target_epsilon})")
                    print(f"             ↳ ワースト5%の推定値: {estimate:.3f} | 信頼区間: [{lower:.3f}, {upper:.3f}]")
                    if is_converged:
                        msg = (f"SMC証明完了 (Stage {self.dkw_stage}): 信頼水準 {100*(1-self.dkw_total_delta):.1f}% で目標精度(誤差幅 <= {self.dkw_target_epsilon})に到達しました。\n"
                               f"👉 指定領域において、【 {100*(1-self.dkw_total_delta):.1f}% の確率(信頼水準)で、最悪のデータ(ワースト5%)が以下の範囲に収束する 】ことが証明されました。\n"
                               f"   - {self.dkw_target_metric} : [{lower:.3f}, {upper:.3f}] (推定誤差幅: {interval_width:.3f})")
                        self._print_final_report(current_idx, "SMC (DKW)", msg)
                        return {"system_command": "stop", "reason": msg}
                else:
                    current_samples = 0
                    target_n_i = self.dkw_base_samples * (self.dkw_stage ** 2)

            if current_samples >= target_n_i:
                self.dkw_stage += 1
                print(f"[Strategist] 📊 精度未達。Stage {self.dkw_stage} (目標サンプル: {self.dkw_base_samples * (self.dkw_stage ** 2)}) へ移行し、追加サンプリングを行います。")

            reason = f"SMC: Sequential-DKW Sampling (Stage {self.dkw_stage})"
            
            calc = TheoreticalSafetyCalculator(self.config) if TheoreticalSafetyCalculator else None
            
            # [最適化] ループ内で毎回計算しないよう、過去データの最大ループ番号を事前に算出しておく
            max_loop_num = 0
            if df_dataset is not None and 'loop_num' in df_dataset.columns:
                max_loop_val = df_dataset['loop_num'].max()
                if pd.notna(max_loop_val):
                    max_loop_num = int(max_loop_val)

            random_offset = len(df_dkw) if (self.dkw_pure_smc and df_dkw is not None) else max_loop_num

            while len(self.task_cache) < self.CACHE_SIZE:
                # [修正] 純粋な一様乱数を取得し、サンプルの独立性(i.i.d.)を維持
                pt = self.get_random_point(random_offset + self.dkw_random_index)
                self.dkw_random_index += 1
                
                # 事前条件チェック
                if self.dkw_region != "custom":
                    check_pt = pt.copy()
                    # 理論値の事前計算 (JAMA理論の領域などを判定可能にする)
                    if calc:
                        theory_res = calc.evaluate(pt.get("dx0", 0), pt.get("ego_speed", 0), pt.get("npc_speed", 0))
                        check_pt.update(theory_res)
                    # シミュレーション結果依存の変数は、結果が出る前に棄却されないよう安全なダミー値をセット
                    check_pt["c_collision"] = 0
                    check_pt["min_ttc"] = 99.9
                    check_pt["min_distance"] = 99.9
                    check_pt["min_ttb"] = 99.9 # [追加] TTB指定時のエラー回避
                    try:
                        # 共通の抽出関数を使って確実にフィルタリングする
                        temp_df = pd.DataFrame([check_pt])
                        if point_extractors.filter_by_region_and_bounds(temp_df, region=self.dkw_region).empty:
                            continue # 領域条件を満たさない(JAMA領域外など)ため棄却して次の乱数を引く
                    except Exception as e:
                        print(f"[Strategist] ⚠️ 棄却サンプリング中にエラー発生 (破棄します): {e}")
                        continue
                
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
                result = {**self.get_random_point(current_idx), "reason": f"STEP1: Global Search (V:{num_violations})"}
                self.dispatched_task_count += 1
                return result
                
        # --- マージンモードのセーフティ (危険データが1つもない場合は境界が引けないためランダム探索でごまかす) ---
        if self.current_phase == "STEP3" and num_violations == 0:
            print("[Strategist] ⚠️ STEP3(マージンモード)で起動されましたが、データセットに衝突(1)の記録がありません。境界構築のため一時的にグローバル探索を実施します。")
            result = {**self.get_random_point(current_idx), "reason": "STEP3 Fallback: Global Search (No Violations)"}
            self.dispatched_task_count += 1
            return result

        # AI学習・予測
        self.boundary_model_service.train(
            df_dataset,
            target_column=best_target,
        )
        candidates = self.generate_candidate_points()
        mean, std = self.boundary_model_service.predict_uncertainty(candidates)
        if mean is None:
            result = {**self.get_random_point(current_idx), "reason": "Fallback (Error)"}
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
        
        if self.run_mode not in ["boundary_gap", "binomial_ci"] and current_idx >= self.MAX_SAMPLES:
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
                param_range = self.param_ranges[name][1] - self.param_ranges[name][0]
                std_dev = param_range * self.FOCUS_NOISE  # パラメータの幅に応じた標準偏差
                
                param_candidates = []
                for point in self.FOCUS_POINTS:
                    # 指定ポイントに該当のパラメータが無ければ範囲の中央を基準にする
                    center = point.get(name, sum(self.param_ranges[name])/2.0)
                    samples = np.random.normal(loc=center, scale=std_dev, size=num_per_point)
                    param_candidates.extend(samples)
                
                # 端数合わせ
                while len(param_candidates) < num_points:
                    param_candidates.append(np.random.uniform(self.param_ranges[name][0], self.param_ranges[name][1]))
                    
                # 定義された範囲外にはみ出た値をクリップ（制限）する
                clipped = np.clip(param_candidates[:num_points], self.param_ranges[name][0], self.param_ranges[name][1])
                cols.append(clipped)
                
            return np.column_stack(cols)
