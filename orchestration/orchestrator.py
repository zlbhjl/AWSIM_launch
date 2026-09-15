from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from contracts.execution import TestCase
from contracts.statistics import StatisticalRequest
from evaluation.binomial_ci import BinomialCIService
from orchestration.cache_policy import resolve_strategy_cache_size
from runtime.cluster.host_worker import HostWorkerConfig, HostWorkerManager
from runtime.cluster.ray_queue import TaskQueue
from runtime.cluster.task_queue_gateway import TaskQueueGateway
from runtime.repository.statistical_history import StatisticalHistoryRepository
from runtime.repository.strategy_dataset import StrategyDatasetRepository
from targets.statistical import (
    build_statistical_region_policy,
    load_statistical_target_profile,
)
from .resume import ResumeConfig, ResumeService
from .replay import ReplayCsvStrategy, ReplayCsvStrategyConfig
from .strategy import (
    ActiveLearningStrategist,
    FixedCaseStrategy,
    FixedCaseStrategyConfig,
    ParameterCaseStrategy,
    ParameterCaseStrategyConfig,
)
from .fixed_parameter_sampling import (
    FixedParameterSamplingStrategy,
    FixedParameterSamplingStrategyConfig,
)


@dataclass(frozen=True)
class OrchestratorConfig:
    output: str
    fixture: str | None = None
    params: dict[str, object] = field(default_factory=dict)
    case_id: str | None = None
    case_kind: str = "uturn"
    run_mode: str = "explore"
    target: str = "awsim"
    experiment_id: str | None = None
    worker_id: str = "worker_v2_local"
    reason: str = "manual_orchestrator_run"
    tags: list[str] = field(default_factory=list)
    config_module: str = "targets.awsim.case_kinds.uturn"
    container_profile: str | None = None
    scenario_profile: str | None = None
    focus_points: list[dict[str, object]] | None = None
    path_root: str | None = None
    dataset_csv: str | None = None
    replay_csv: str | None = None
    replay_reason_pattern: str = "BINOMIAL_CI:"
    replay_expected_count: int | None = None
    history_path: str | None = None
    dkw_bounds: dict[str, object] | None = None
    dkw_region: str = "custom"
    dkw_pure_smc: bool = False
    dkw_simultaneous: bool = False
    resume_from: str | None = None
    resume_current_only: bool = False
    max_samples: int | None = None
    scenario_type: str | None = None
    simulation_output_dir: str | None = None
    expected_trace_path: str | None = None
    local_loop_num: int | None = None
    headless: bool = False
    ext_mode: str = "cvm"
    refresh_interval: int | None = 10
    max_strategy_cases: int | None = None
    binomial_target: str = "c_collision"
    binomial_method: str = "wilson"
    binomial_confidence: float = 0.95
    binomial_target_width: float = 0.02
    binomial_min_samples: int | None = None
    queue_actor_name: str = "local_task_queue"
    queue_namespace: str | None = None
    queue_address: str | None = None
    shared_store_actor_name: str | None = None
    shared_store_namespace: str | None = None
    shared_store_address: str | None = None
    worker_count: int | None = None
    queue_high_water: int | None = None
    queue_low_water: int | None = None
    run_inline_worker: bool = True
    with_host_worker: bool = False
    headless_host: bool = False
    poll_interval_sec: float = 2.0
    cache_size: int | None = None


def build_task_payload(test_case: TestCase) -> dict[str, object]:
    payload = {
        "case_id": test_case.case_id,
        "target": test_case.target,
        "case_kind": test_case.case_kind,
        **dict(test_case.input),
        "reason": test_case.reason,
        "tags": list(test_case.tags),
    }
    config_module = test_case.meta.get("config_module")
    if isinstance(config_module, str) and config_module:
        payload["config_module"] = config_module
    scenario_profile = test_case.meta.get("scenario_profile")
    if isinstance(scenario_profile, str) and scenario_profile:
        payload["scenario_profile"] = scenario_profile
    container_profile = test_case.meta.get("container_profile")
    if isinstance(container_profile, str) and container_profile:
        payload["container_profile"] = container_profile
    for key in (
        "replay_source_loop_num",
        "replay_source_case_id",
        "replay_source_collision",
        "replay_source_reason",
        "replay_source_csv",
    ):
        if key in test_case.meta:
            payload[key] = test_case.meta[key]
    return payload


def build_worker_argv(config: OrchestratorConfig) -> list[str]:
    return [
        "--queue-actor-name",
        config.queue_actor_name,
        *(["--queue-namespace", config.queue_namespace] if config.queue_namespace else []),
        *(["--queue-address", config.queue_address] if config.queue_address else []),
        "--output",
        config.output,
        "--worker-id",
        config.worker_id,
        "--case-kind",
        config.case_kind,
        "--mode",
        config.run_mode,
        "--ext_mode",
        config.ext_mode,
        "--target",
        config.target,
        "--config-module",
        config.config_module,
        *sum([["--tag", tag] for tag in config.tags], []),
        *(["--path-root", config.path_root] if config.path_root else []),
        *(
            ["--dataset-csv", config.dataset_csv]
            if config.dataset_csv and not config.shared_store_actor_name
            else []
        ),
        *(
            ["--shared-store-actor-name", config.shared_store_actor_name]
            if config.shared_store_actor_name
            else []
        ),
        *(
            ["--shared-store-namespace", config.shared_store_namespace]
            if config.shared_store_actor_name and config.shared_store_namespace
            else []
        ),
        *(
            ["--shared-store-address", config.shared_store_address]
            if config.shared_store_actor_name and config.shared_store_address
            else []
        ),
        *(["--history-path", config.history_path] if config.history_path else []),
        *(
            ["--refresh-interval", str(config.refresh_interval)]
            if config.refresh_interval is not None
            else []
        ),
        *(["--headless"] if config.headless else []),
        *(
            ["--container-profile", config.container_profile]
            if config.container_profile
            else []
        ),
        *(
            ["--scenario-profile", config.scenario_profile]
            if config.scenario_profile
            else []
        ),
    ]


class Orchestrator:
    def __init__(
        self,
        *,
        queue: Any | None = None,
        queue_gateway: TaskQueueGateway | None = None,
        worker_runner: Callable[..., object] | None = None,
        resume_service: ResumeService | None = None,
        host_worker_manager: HostWorkerManager | None = None,
        sleeper: Callable[[float], None] | None = None,
        progress_callback: Callable[[dict[str, object], OrchestratorConfig], None] | None = None,
        maintenance_callback: Callable[
            [dict[str, object], OrchestratorConfig],
            list[dict[str, object]],
        ]
        | None = None,
    ):
        queue_actor = queue or TaskQueue()
        self.queue_gateway = queue_gateway or TaskQueueGateway(actor=queue_actor)
        self.resume_service = resume_service or ResumeService()
        self.host_worker_manager = host_worker_manager or HostWorkerManager()
        self.sleeper = sleeper or time.sleep
        self.progress_callback = progress_callback
        self.maintenance_callback = maintenance_callback
        if worker_runner is None:
            from apps.cli.worker_main import run_worker_with_summary

            self.worker_runner = run_worker_with_summary
        else:
            self.worker_runner = worker_runner

    def run(self, config: OrchestratorConfig) -> dict[str, object]:
        resume_state = self.resume_service.prepare(self._build_resume_config(config))
        self.queue_gateway.set_start_counts(resume_state.last_loop_num)
        strategy = self._build_strategy(config)
        worker_summaries: list[dict[str, object]] = []
        worker_exit_code = 0
        enqueued = 0
        stop_reason = ""
        interrupted = False
        host_worker_started = False
        maintenance_events: list[dict[str, object]] = []
        target_total = self._resolve_target_total(config, resume_state.last_loop_num)

        try:
            if config.with_host_worker:
                self.host_worker_manager.start(self._build_host_worker_config(config))
                host_worker_started = True

            initial_snapshot = self.queue_gateway.get_snapshot()
            enqueued, stop_reason = self._refill_queue(
                strategy=strategy,
                config=config,
                snapshot=initial_snapshot,
                enqueued=enqueued,
            )

            while True:
                snapshot = self.queue_gateway.get_snapshot()
                self._emit_progress(snapshot, config)
                maintenance_events.extend(self._run_maintenance(snapshot, config))
                completed_count = int(snapshot["completed_count"])
                queue_size = int(snapshot["queue_size"])

                if target_total is not None and completed_count >= target_total and not snapshot["stop_signal"]:
                    stop_reason = stop_reason or "Target Reached"
                    self.queue_gateway.set_stop_signal(stop_reason)
                    snapshot = self.queue_gateway.get_snapshot()
                    queue_size = int(snapshot["queue_size"])

                # When the strategist stops before enqueuing any task, avoid calling it
                # again just to confirm the queue is still empty.
                if (
                    config.run_inline_worker
                    and queue_size <= 0
                    and (bool(snapshot["stop_signal"]) or bool(stop_reason))
                ):
                    if stop_reason and not snapshot["stop_signal"]:
                        self.queue_gateway.set_stop_signal(stop_reason)
                    break

                if not config.run_inline_worker:
                    if queue_size <= self._resolve_queue_low_water(config):
                        enqueued, refill_reason = self._refill_queue(
                            strategy=strategy,
                            config=config,
                            snapshot=snapshot,
                            enqueued=enqueued,
                        )
                        if refill_reason:
                            stop_reason = refill_reason
                    snapshot = self.queue_gateway.get_snapshot()
                    if int(snapshot["queue_size"]) <= 0 and stop_reason and not snapshot["stop_signal"]:
                        self.queue_gateway.set_stop_signal(stop_reason)
                        snapshot = self.queue_gateway.get_snapshot()
                    if self._should_finish_external_loop(snapshot, stop_reason):
                        break
                    self.sleeper(max(float(config.poll_interval_sec), 0.0))
                    continue

                if queue_size <= 0:
                    enqueued, refill_reason = self._refill_queue(
                        strategy=strategy,
                        config=config,
                        snapshot=snapshot,
                        enqueued=enqueued,
                    )
                    if refill_reason:
                        stop_reason = refill_reason
                    snapshot = self.queue_gateway.get_snapshot()
                    queue_size = int(snapshot["queue_size"])

                if queue_size <= 0:
                    if snapshot["stop_signal"] or stop_reason:
                        break
                    if not self._can_request_more_cases(config, enqueued):
                        break
                    continue

                while True:
                    worker_result = self.worker_runner(
                        build_worker_argv(config),
                        task_source=self.queue_gateway,
                    )
                    normalized_summary = self._normalize_worker_result(worker_result)
                    worker_summaries.append(normalized_summary)
                    worker_exit_code = int(normalized_summary["exit_code"])
                    snapshot = self.queue_gateway.get_snapshot()
                    if worker_exit_code != 0:
                        break
                    if normalized_summary.get("terminal_status") != "refresh_requested":
                        break
                    if int(snapshot["queue_size"]) <= 0:
                        break

                if worker_exit_code != 0:
                    break
                if int(snapshot["queue_size"]) <= 0 and (snapshot["stop_signal"] or stop_reason):
                    if stop_reason and not snapshot["stop_signal"]:
                        self.queue_gateway.set_stop_signal(stop_reason)
                    break
                if int(snapshot["queue_size"]) > 0:
                    continue
                if not self._can_request_more_cases(config, enqueued):
                    break
        except KeyboardInterrupt:
            interrupted = True
            stop_reason = "KeyboardInterrupt"
            self.queue_gateway.set_stop_signal(stop_reason)
        finally:
            if host_worker_started:
                self.host_worker_manager.stop(timeout=5.0)

        snapshot = self.queue_gateway.get_snapshot()
        mode = "local_queue" if config.run_inline_worker else "cluster_queue"
        summary = {
            "mode": mode,
            "enqueued": enqueued,
            "worker_exit_code": worker_exit_code,
            "queue_size": int(snapshot["queue_size"]),
            "completed_count": int(snapshot["completed_count"]),
            "worker_statuses": dict(snapshot["worker_statuses"]),
            "worker_summaries": worker_summaries,
            "output_path": str(Path(config.output).expanduser().resolve()),
            "resume_from": config.resume_from,
            "restored_base_csv": str(resume_state.restored_base_csv) if resume_state.restored_base_csv else None,
            "last_loop_num": resume_state.last_loop_num,
            "next_loop_num": resume_state.next_loop_num,
            "stop_reason": stop_reason or str(snapshot.get("stop_reason", "")),
            "interrupted": interrupted,
            "maintenance_events": maintenance_events,
            "final_report": self._extract_final_report_payload(strategy),
            "statistical_report": self._extract_statistical_report_payload(strategy),
        }
        if isinstance(strategy, ReplayCsvStrategy):
            summary["replay"] = {
                "source_csv": str(strategy.source_csv),
                "eligible_count": strategy.eligible_count,
                "skipped_completed_count": strategy.skipped_completed_count,
                "scheduled_count": enqueued,
            }
        if enqueued == 0:
            summary["worker_exit_code"] = 0
        return summary

    def _build_strategy(
        self,
        config: OrchestratorConfig,
    ) -> (
        FixedCaseStrategy
        | ParameterCaseStrategy
        | ReplayCsvStrategy
        | ActiveLearningStrategist
        | FixedParameterSamplingStrategy
    ):
        if config.fixture is not None:
            return FixedCaseStrategy(
                FixedCaseStrategyConfig(
                    fixture=config.fixture,
                    case_id=config.case_id,
                    case_kind=config.case_kind,
                    target=config.target,
                    reason=config.reason,
                    tags=list(config.tags),
                )
            )

        if (
            config.target == "prism"
            and config.params
            and config.run_mode == "binomial_ci"
        ):
            if config.max_samples is None:
                raise ValueError("PRISM fixed sampling requires max_samples")
            dataset_repository = self._build_dataset_repository(config)
            min_samples = (
                int(config.binomial_min_samples)
                if config.binomial_min_samples is not None
                else min(10, int(config.max_samples))
            )
            return FixedParameterSamplingStrategy(
                FixedParameterSamplingStrategyConfig(
                    target=config.target,
                    case_kind=config.case_kind,
                    params=dict(config.params),
                    max_samples=int(config.max_samples),
                    experiment_id=config.experiment_id,
                    case_id_prefix=config.case_id,
                    reason_prefix="PRISM_FIXED_SAMPLING",
                    tags=tuple(config.tags),
                    run_model_check_once=True,
                ),
                dataset_repository=dataset_repository,
                statistical_service=BinomialCIService(),
                statistical_request=StatisticalRequest(
                    method="binomial_ci",
                    metric=config.binomial_target,
                    confidence=config.binomial_confidence,
                    target_width=config.binomial_target_width,
                    options={
                        "method": config.binomial_method,
                        "region": "custom",
                        "min_samples": min_samples,
                        "max_samples": int(config.max_samples),
                    },
                ),
            )

        if config.params:
            return ParameterCaseStrategy(
                ParameterCaseStrategyConfig(
                    params=dict(config.params),
                    case_id=config.case_id,
                    case_kind=config.case_kind,
                    target=config.target,
                    reason=config.reason,
                    tags=list(config.tags),
                    scenario_type=config.scenario_type,
                    simulation_output_dir=config.simulation_output_dir,
                    expected_trace_path=config.expected_trace_path,
                    local_loop_num=config.local_loop_num,
                )
            )

        if config.replay_csv is not None:
            strategy_config = importlib.import_module(config.config_module)
            case_definition = self._load_replay_case_definition(
                strategy_config,
                config.case_kind,
            )
            input_types = dict(case_definition.get("fixed_params", {}))
            for name, bounds in dict(case_definition.get("param_ranges", {})).items():
                if name in input_types:
                    continue
                try:
                    input_types[name] = float(bounds[0])
                except (KeyError, TypeError, IndexError, ValueError):
                    input_types[name] = ""
            return ReplayCsvStrategy(
                ReplayCsvStrategyConfig(
                    source_csv=config.replay_csv,
                    input_types=input_types,
                    case_kind=config.case_kind,
                    scenario_type=str(
                        case_definition.get("scenario_type", config.case_kind)
                    ),
                    target=config.target,
                    reason_pattern=config.replay_reason_pattern,
                    expected_count=config.replay_expected_count,
                    completed_source_loop_nums=frozenset(
                        self._load_completed_replay_source_loops(config)
                    ),
                )
            )

        strategy_config = importlib.import_module(config.config_module)
        dataset_repository = self._build_dataset_repository(config)
        statistical_profile = load_statistical_target_profile(config.target)
        return ActiveLearningStrategist(
            scenario_name=config.case_kind,
            config=strategy_config,
            focus_points=(
                [dict(point) for point in config.focus_points]
                if config.focus_points is not None
                else None
            ),
            run_mode=config.run_mode,
            dataset_repository=dataset_repository,
            statistical_history_repository=StatisticalHistoryRepository(
                scenario_name=config.case_kind,
                traces_dir=dataset_repository.paths.traces_dir,
            ),
            dkw_bounds=config.dkw_bounds,
            dkw_region=config.dkw_region,
            dkw_pure_smc=config.dkw_pure_smc,
            dkw_simultaneous=config.dkw_simultaneous,
            max_samples=config.max_samples,
            binomial_target=config.binomial_target,
            binomial_method=config.binomial_method,
            binomial_confidence=config.binomial_confidence,
            binomial_target_width=config.binomial_target_width,
            binomial_min_samples=config.binomial_min_samples,
            cache_size=resolve_strategy_cache_size(
                worker_count=self._resolve_worker_count(config),
                run_mode=config.run_mode,
                resume_from=config.resume_from,
                max_samples=config.max_samples,
                explicit_cache_size=config.cache_size,
            ),
            statistical_region_policy=build_statistical_region_policy(
                config.target,
                case_kind=config.case_kind,
                config_module_name=config.config_module,
            ),
            dkw_minimum_value=statistical_profile.minimum_dkw_value,
        )

    def validate_replay_source(self, config: OrchestratorConfig) -> dict[str, object] | None:
        if config.replay_csv is None:
            return None
        strategy = self._build_strategy(config)
        if not isinstance(strategy, ReplayCsvStrategy):
            raise TypeError("replay configuration did not create ReplayCsvStrategy")
        return {
            "source_csv": str(strategy.source_csv),
            "eligible_count": strategy.eligible_count,
            "skipped_completed_count": strategy.skipped_completed_count,
        }

    def _build_resume_config(self, config: OrchestratorConfig) -> ResumeConfig:
        traces_dir = (
            str(Path(config.dataset_csv).expanduser().parent)
            if config.dataset_csv
            else str(Path(config.output).expanduser().resolve().parent)
        )
        base_dataset_csv = None
        if config.dataset_csv:
            dataset_path = Path(config.dataset_csv).expanduser()
            base_dataset_csv = str(dataset_path.with_name(f"{dataset_path.stem}_base{dataset_path.suffix}"))
        return ResumeConfig(
            scenario_name=config.case_kind,
            traces_dir=traces_dir,
            resume_from=config.resume_from,
            count_current_only=config.resume_current_only,
            current_dataset_csv=config.dataset_csv,
            base_dataset_csv=base_dataset_csv,
            run_mode=config.run_mode,
        )

    def _build_host_worker_config(self, config: OrchestratorConfig) -> HostWorkerConfig:
        return HostWorkerConfig(
            case_kind=config.case_kind,
            run_mode=config.run_mode,
            output_path=config.output,
            queue_actor_name=config.queue_actor_name,
            queue_namespace=config.queue_namespace or "awsim_cluster",
            queue_address=config.queue_address,
            config_module=config.config_module,
            container_profile=config.container_profile,
            scenario_profile=config.scenario_profile,
            dataset_csv=config.dataset_csv,
            path_root=config.path_root,
            history_path=config.history_path,
            refresh_interval=config.refresh_interval,
            focus_points=config.focus_points,
            dkw_bounds=config.dkw_bounds,
            dkw_region=config.dkw_region,
            dkw_pure_smc=config.dkw_pure_smc,
            dkw_simultaneous=config.dkw_simultaneous,
            ext_mode=config.ext_mode,
            headless=config.headless_host,
            shared_store_actor_name=config.shared_store_actor_name,
            shared_store_namespace=config.shared_store_namespace or "awsim_cluster",
            shared_store_address=config.shared_store_address,
            worker_id=f"{config.worker_id}_host",
        )

    def _resolve_worker_count(self, config: OrchestratorConfig) -> int:
        if config.worker_count is not None:
            return max(config.worker_count, 1)
        host_offset = 1 if config.with_host_worker else 0
        inline_offset = 1 if config.run_inline_worker else 0
        return max(host_offset + inline_offset, 1)

    def _resolve_queue_high_water(self, config: OrchestratorConfig) -> int:
        if config.queue_high_water is not None:
            return max(config.queue_high_water, 1)
        return self._resolve_worker_count(config) * 4

    def _resolve_queue_low_water(self, config: OrchestratorConfig) -> int:
        if config.queue_low_water is not None:
            return max(config.queue_low_water, 0)
        return self._resolve_worker_count(config) * 2

    def _can_request_more_cases(self, config: OrchestratorConfig, enqueued: int) -> bool:
        return config.max_strategy_cases is None or enqueued < config.max_strategy_cases

    @staticmethod
    def _extract_final_report_payload(strategy: object) -> dict[str, object] | None:
        report = getattr(strategy, "latest_final_report", None)
        if report is None or not hasattr(report, "to_payload"):
            return None
        return report.to_payload()

    @staticmethod
    def _extract_statistical_report_payload(
        strategy: object,
    ) -> dict[str, object] | None:
        payload_builder = getattr(strategy, "statistical_report_payload", None)
        if not callable(payload_builder):
            return None
        payload = payload_builder()
        return dict(payload) if payload is not None else None

    def _refill_queue(
        self,
        *,
        strategy: (
            FixedCaseStrategy
            | ParameterCaseStrategy
            | ReplayCsvStrategy
            | ActiveLearningStrategist
            | FixedParameterSamplingStrategy
        ),
        config: OrchestratorConfig,
        snapshot: dict[str, object],
        enqueued: int,
    ) -> tuple[int, str]:
        if not self._can_request_more_cases(config, enqueued):
            return enqueued, ""

        queue_size = int(snapshot["queue_size"])
        high_water = self._resolve_queue_high_water(config)
        stop_reason = ""
        while queue_size < high_water and self._can_request_more_cases(config, enqueued):
            test_case = strategy.next_test_case()
            if test_case is None:
                if bool(getattr(strategy, "waiting_for_result", False)):
                    break
                stop_reason = str(
                    getattr(strategy, "stop_reason", "") or "Strategist Stop"
                )
                break
            payload = build_task_payload(test_case)
            payload.setdefault("config_module", config.config_module)
            if config.scenario_profile:
                payload.setdefault("scenario_profile", config.scenario_profile)
            if config.container_profile:
                payload.setdefault("container_profile", config.container_profile)
            self.queue_gateway.add_task(payload)
            enqueued += 1
            queue_size += 1

            if (
                config.fixture is not None
                or (
                    config.params
                    and not isinstance(strategy, FixedParameterSamplingStrategy)
                )
            ):
                break
        return enqueued, stop_reason

    @staticmethod
    def _load_replay_case_definition(
        config_module: object,
        case_kind: str,
    ) -> dict[str, object]:
        loader = getattr(config_module, "get_case_definition", None)
        if callable(loader):
            return dict(loader())
        return {
            "scenario_type": getattr(config_module, "SCENARIO_TYPE", case_kind),
            "fixed_params": dict(getattr(config_module, "FIXED_PARAMS", {})),
            "param_ranges": dict(getattr(config_module, "PARAM_RANGES", {})),
        }

    def _load_completed_replay_source_loops(
        self,
        config: OrchestratorConfig,
    ) -> set[int]:
        dataset = self._build_dataset_repository(config).load_dataset()
        if dataset is None or "meta_replay_source_loop_num" not in dataset.columns:
            return set()
        completed: set[int] = set()
        for value in dataset["meta_replay_source_loop_num"].dropna().tolist():
            try:
                completed.add(int(float(value)))
            except (TypeError, ValueError):
                continue
        return completed

    def _resolve_target_total(self, config: OrchestratorConfig, last_loop_num: int) -> int | None:
        if (
            config.target == "prism"
            and config.params
            and config.run_mode == "binomial_ci"
        ):
            return None
        if config.fixture is not None or config.params:
            return last_loop_num + 1
        if config.run_mode in {"boundary_gap", "dkw", "verify_consistency", "replay"}:
            return None
        if config.run_mode == "binomial_ci":
            return (
                last_loop_num + int(config.max_samples)
                if config.max_samples is not None
                else None
            )
        if config.max_samples is not None:
            return last_loop_num + int(config.max_samples)
        try:
            strategy_config = importlib.import_module(config.config_module)
        except ImportError:
            return None
        repeat_count = getattr(strategy_config, "REPEAT_COUNT", None)
        if repeat_count is None:
            return None
        return last_loop_num + int(repeat_count)

    def _should_finish_external_loop(
        self,
        snapshot: dict[str, object],
        stop_reason: str,
    ) -> bool:
        queue_size = int(snapshot["queue_size"])
        completed_count = int(snapshot["completed_count"])
        dispatched_count = int(snapshot["dispatched_count"])
        stop_signal = bool(snapshot["stop_signal"]) or bool(stop_reason)
        return stop_signal and queue_size == 0 and completed_count >= dispatched_count

    def _emit_progress(self, snapshot: dict[str, object], config: OrchestratorConfig) -> None:
        if self.progress_callback is None:
            return
        self.progress_callback(dict(snapshot), config)

    def _run_maintenance(
        self,
        snapshot: dict[str, object],
        config: OrchestratorConfig,
    ) -> list[dict[str, object]]:
        if self.maintenance_callback is None:
            return []
        return list(self.maintenance_callback(dict(snapshot), config) or [])

    @staticmethod
    def _normalize_worker_result(result: object) -> dict[str, object]:
        if isinstance(result, dict):
            normalized = dict(result)
            normalized.setdefault("exit_code", 0)
            normalized.setdefault("terminal_status", normalized.get("status"))
            return normalized
        if isinstance(result, int):
            return {
                "exit_code": result,
                "terminal_status": None,
            }
        raise TypeError("worker_runner must return int or summary dict")

    @staticmethod
    def _build_dataset_repository(config: OrchestratorConfig) -> StrategyDatasetRepository:
        traces_dir = (
            Path(config.dataset_csv).expanduser().resolve().parent
            if config.dataset_csv
            else Path(config.output).expanduser().resolve().parent
        )
        dataset_csv = None
        base_csv = None
        if config.dataset_csv:
            dataset_path = Path(config.dataset_csv).expanduser().resolve()
            dataset_csv = dataset_path
            base_csv = dataset_path.with_name(f"{dataset_path.stem}_base{dataset_path.suffix}")
        return StrategyDatasetRepository(
            config.case_kind,
            traces_dir=traces_dir,
            dataset_csv=dataset_csv,
            base_csv=base_csv,
        )
