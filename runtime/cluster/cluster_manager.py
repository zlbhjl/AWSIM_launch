from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from redis_cluster import cluster_config
from runtime.container.profile import resolve_container_launch_profile
from runtime.container.supervised_process import PROCESS_SUPERVISOR_SOCKET_ENV


@dataclass(frozen=True)
class ClusterLaunchConfig:
    case_kind: str
    run_mode: str
    output_path: str
    queue_actor_name: str
    target: str = "awsim"
    run_id: str | None = None
    queue_namespace: str = "awsim_cluster"
    queue_address: str | None = None
    config_module: str | None = None
    container_profile: str | None = None
    scenario_profile: str | None = None
    dataset_csv: str | None = None
    ext_mode: str = "cvm"
    headless: bool = False
    with_host_worker: bool = False
    refresh_interval: int | None = 10
    restart_on_refresh: bool = True
    shared_store_actor_name: str | None = None
    shared_store_namespace: str = "awsim_cluster"
    shared_store_address: str | None = None
    sync_awsim_launch: bool = True
    sync_awsim_script_py: bool = False
    sync_aw_runtime_monitor: bool = True
    sync_autoware180_map: bool = False
    ray_head_start_timeout_sec: int = 180
    worker_launch_stagger_sec: float = 20.0
    worker_queue_connect_retries: int = 6
    worker_queue_connect_retry_interval_sec: float = 15.0
    worker_queue_empty_wait_timeout_sec: float = 300.0
    worker_queue_empty_wait_interval_sec: float = 5.0
    worker_queue_heartbeat_interval_sec: float = 60.0
    worker_gpu_health_check: bool = False
    worker_gpu_health_timeout_sec: float = 5.0


@dataclass(frozen=True)
class SyncPathSpec:
    label: str
    source: str
    remote_path: str
    remote_dir: str
    excludes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClusterLaunchSummary:
    head_address: str
    launched_workers: tuple[str, ...]
    skipped_workers: tuple[str, ...]
    preflight_failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContainerState:
    exists: bool
    running: bool = False
    status: str = "missing"
    exit_code: int | None = None
    error: str = ""
    inspect_error: str = ""


class ClusterManager:
    def __init__(
        self,
        *,
        subprocess_run: Callable[..., object] | None = None,
        subprocess_popen: Callable[..., object] | None = None,
        sleeper: Callable[[float], None] | None = None,
        nodes: Mapping[str, Mapping[str, object]] | None = None,
        master_ip: str | None = None,
        ray_port: str | None = None,
    ) -> None:
        self.subprocess_run = subprocess_run or subprocess.run
        self.subprocess_popen = subprocess_popen or subprocess.Popen
        self.sleeper = sleeper or time.sleep
        self.nodes = nodes or cluster_config.CLUSTER_NODES
        self.master_ip = master_ip or cluster_config.MASTER_IP
        self.ray_port = ray_port or cluster_config.RAY_PORT

    def start_head(self, config: ClusterLaunchConfig) -> str:
        self._start_head_node(timeout_sec=config.ray_head_start_timeout_sec)
        head_address = config.queue_address or f"{self.master_ip}:{self.ray_port}"
        return head_address

    def launch_workers(
        self,
        config: ClusterLaunchConfig,
        *,
        head_address: str,
    ) -> ClusterLaunchSummary:
        launched_workers: list[str] = []
        skipped_workers: list[str] = []
        preflight_failures: list[str] = []

        launch_nodes: list[Mapping[str, object]] = []
        for node in self.nodes.values():
            if not node.get("enabled", True):
                skipped_workers.append(str(node.get("machine", "unknown")))
                continue
            if config.with_host_worker and node.get("ip") == self.master_ip:
                skipped_workers.append(str(node.get("machine", "unknown")))
                continue
            if "container" not in node:
                skipped_workers.append(str(node.get("machine", "unknown")))
                continue
            launch_nodes.append(node)

        for index, node in enumerate(launch_nodes):
            self._sync_node_sources(node=node, config=config)
            launch_profile = resolve_container_launch_profile(config.container_profile)
            requires_gpu = launch_profile.requires_gpu if launch_profile is not None else True
            if requires_gpu:
                gpu_ready, gpu_error = self._preflight_node_gpu(node=node, config=config)
                if not gpu_ready:
                    machine = str(node.get("machine", "unknown"))
                    skipped_workers.append(machine)
                    preflight_failures.append(f"{machine}: {gpu_error}")
                    continue
            command = self._build_worker_launch_command(
                node=node,
                config=config,
                head_address=head_address,
            )
            self._launch_node_worker(node=node, command=command)
            launched_workers.append(str(node.get("machine", "unknown")))
            if index < len(launch_nodes) - 1 and config.worker_launch_stagger_sec > 0:
                self.sleeper(float(config.worker_launch_stagger_sec))

        return ClusterLaunchSummary(
            head_address=head_address,
            launched_workers=tuple(launched_workers),
            skipped_workers=tuple(skipped_workers),
            preflight_failures=tuple(preflight_failures),
        )

    def start_cluster(self, config: ClusterLaunchConfig) -> ClusterLaunchSummary:
        head_address = self.start_head(config)
        return self.launch_workers(config, head_address=head_address)

    def _start_head_node(self, *, timeout_sec: int) -> None:
        self.subprocess_run(
            ["ray", "stop", "--force"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            result = self.subprocess_run(
                [
                    "ray",
                    "start",
                    "--head",
                    f"--port={self.ray_port}",
                    f"--node-ip-address={self.master_ip}",
                    "--dashboard-host=0.0.0.0",
                    "--disable-usage-stats",
                ],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            self.subprocess_run(
                ["ray", "stop", "--force"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            stdout = str(exc.stdout or "").strip()
            stderr = str(exc.stderr or "").strip()
            raise RuntimeError(
                "Timed out while starting Ray head node"
                f" after {timeout_sec}s (stdout={stdout!r}, stderr={stderr!r})"
            ) from exc
        returncode = int(getattr(result, "returncode", 0))
        if returncode != 0:
            stdout = str(getattr(result, "stdout", "")).strip()
            stderr = str(getattr(result, "stderr", "")).strip()
            raise RuntimeError(
                "Failed to start Ray head node"
                f" (stdout={stdout!r}, stderr={stderr!r})"
            )

    def _build_worker_launch_command(
        self,
        *,
        node: Mapping[str, object],
        config: ClusterLaunchConfig,
        head_address: str,
    ) -> str:
        node = self.resolve_node_for_launch(node, config)
        container = dict(node["container"])
        node_ip_address = str(node.get("ip", self.master_ip))
        workspace = str(container.get("workspace", "/home/passd"))
        container_name = str(container.get("name", "sim_worker"))
        ros_domain_id = str(container.get("ros_domain_id", 0))
        container_user = str(container.get("user", "passd"))
        container_image = str(container.get("image", "autoware_internal:2026"))
        container_password = str(container.get("password", "passd"))
        host_home = self._resolve_node_host_home(node)
        launch_profile = resolve_container_launch_profile(config.container_profile)
        if launch_profile is not None:
            if launch_profile.target != config.target:
                raise ValueError(
                    f"Container profile {launch_profile.name!r} targets "
                    f"{launch_profile.target!r}, not {config.target!r}"
                )
            container_image = launch_profile.image
            if launch_profile.docker_user is not None:
                container_user = launch_profile.docker_user
        start_ray_worker_node = (
            launch_profile.start_ray_worker_node if launch_profile is not None else True
        )
        worker_actor_address = self._resolve_worker_actor_address(
            head_address,
            start_ray_worker_node=start_ray_worker_node,
        )

        worker_argv = [
            "python3",
            "-u",
            "run_worker_v2.py",
            "--queue-actor-name",
            config.queue_actor_name,
            "--queue-address",
            worker_actor_address,
            "--queue-namespace",
            config.queue_namespace,
            "--output",
            f"{workspace}/simulation_traces/{config.case_kind}_records.jsonl",
            "--worker-id",
            f"worker_{ros_domain_id}",
            "--case-kind",
            config.case_kind,
            "--mode",
            config.run_mode,
            "--target",
            config.target,
            "--ext_mode",
            config.ext_mode,
        ]
        if config.config_module:
            worker_argv.extend(["--config-module", config.config_module])
        if config.container_profile:
            worker_argv.extend(["--container-profile", config.container_profile])
        if config.scenario_profile:
            worker_argv.extend(["--scenario-profile", config.scenario_profile])
        if config.dataset_csv and not config.shared_store_actor_name:
            worker_argv.extend(["--dataset-csv", config.dataset_csv])
        if config.refresh_interval is not None:
            worker_argv.extend(["--refresh-interval", str(config.refresh_interval)])
        if config.worker_queue_connect_retries:
            worker_argv.extend(
                ["--queue-connect-retries", str(config.worker_queue_connect_retries)]
            )
        if config.worker_queue_connect_retry_interval_sec >= 0:
            worker_argv.extend(
                [
                    "--queue-connect-retry-interval",
                    str(config.worker_queue_connect_retry_interval_sec),
                ]
            )
        if config.worker_queue_empty_wait_timeout_sec >= 0:
            worker_argv.extend(
                [
                    "--queue-empty-wait-timeout",
                    str(config.worker_queue_empty_wait_timeout_sec),
                ]
            )
        if config.worker_queue_empty_wait_interval_sec >= 0:
            worker_argv.extend(
                [
                    "--queue-empty-wait-interval",
                    str(config.worker_queue_empty_wait_interval_sec),
                ]
            )
        if config.worker_queue_heartbeat_interval_sec >= 0:
            worker_argv.extend(
                [
                    "--queue-heartbeat-interval",
                    str(config.worker_queue_heartbeat_interval_sec),
                ]
            )
        requires_gpu = launch_profile.requires_gpu if launch_profile is not None else True
        requires_ros = launch_profile.requires_ros if launch_profile is not None else True
        requires_runtime_monitor = (
            launch_profile.requires_runtime_monitor if launch_profile is not None else True
        )
        if config.worker_gpu_health_check and requires_gpu:
            worker_argv.extend(
                [
                    "--worker-gpu-health-check",
                    "--worker-gpu-health-timeout-sec",
                    str(config.worker_gpu_health_timeout_sec),
                ]
            )
        if config.restart_on_refresh:
            worker_argv.append("--restart-on-refresh")
        if config.headless:
            worker_argv.append("--headless")
        if config.shared_store_actor_name:
            worker_argv.extend(
                [
                    "--shared-store-actor-name",
                    config.shared_store_actor_name,
                    "--shared-store-namespace",
                    config.shared_store_namespace,
                    "--shared-store-address",
                    config.shared_store_address or worker_actor_address,
                ]
            )

        worker_command = " ".join(shlex.quote(part) for part in worker_argv)
        docker_network_mode = launch_profile.network_mode if launch_profile is not None else "host"
        docker_privileged = launch_profile.privileged if launch_profile is not None else True
        docker_env = dict(launch_profile.env) if launch_profile is not None else {}
        docker_mount_templates = launch_profile.mounts if launch_profile is not None else ()
        docker_run_args = list(launch_profile.docker_run_args) if launch_profile is not None else []
        bash_args = launch_profile.bash_args if launch_profile is not None else ("-i", "-c")
        bootstrap_pip_packages = (
            launch_profile.bootstrap_pip_packages
            if launch_profile is not None
            else ("ray==2.55.0",)
        )
        bootstrap_apt_packages = (
            launch_profile.bootstrap_apt_packages
            if launch_profile is not None
            else ("xvfb",)
        )
        worker_env = dict(launch_profile.worker_env) if launch_profile is not None else {
            "DISPLAY": ":99",
            "VK_ICD_FILENAMES": "/usr/share/vulkan/icd.d/nvidia_icd.json",
        }
        docker_env["EXEC_MODE"] = "cluster"
        docker_env.setdefault("HOME", workspace)
        if requires_ros:
            docker_env["ROS_DOMAIN_ID"] = ros_domain_id
        trace_suffix = f"_{config.run_id}" if config.run_id else ""
        host_trace_dir = f"{host_home}/simulation_traces_{container_name}{trace_suffix}"
        docker_mounts = [
            f"{host_home}/AWSIM_launch:{workspace}/AWSIM_launch",
            f"{host_trace_dir}:{workspace}/simulation_traces",
        ]
        docker_mounts.extend(
            mount_template.format(
                host_home=host_home,
                workspace=workspace,
                container_name=container_name,
            )
            for mount_template in docker_mount_templates
        )
        docker_argv = [
            "docker",
            "run",
            "-d",
            "-it",
            "--name",
            container_name,
            "--user",
            container_user,
            "--network",
            docker_network_mode,
        ]
        if launch_profile is not None and launch_profile.add_host_gateway:
            docker_argv.extend(["--add-host", "host.docker.internal:host-gateway"])
        if docker_privileged:
            docker_argv.append("--privileged")
        if requires_gpu:
            docker_argv.extend(["--gpus", "all"])
        docker_argv.append("--shm-size=32gb")
        for env_name, env_value in docker_env.items():
            docker_argv.extend(["-e", f"{env_name}={env_value}"])
        for mount in docker_mounts:
            docker_argv.extend(["-v", mount])
        docker_argv.extend(docker_run_args)
        docker_argv.extend(
            [
                container_image,
                "bash",
                *bash_args,
                self._build_container_bootstrap(
                    worker_command,
                    workspace,
                    head_address,
                    node_ip_address,
                    container_password,
                    worker_env=worker_env,
                    start_ray_worker_node=start_ray_worker_node,
                    bootstrap_apt_packages=bootstrap_apt_packages,
                    bootstrap_pip_packages=bootstrap_pip_packages,
                    validate_runtime_monitor=(
                        config.sync_aw_runtime_monitor and requires_runtime_monitor
                    ),
                    start_xvfb=requires_ros,
                    home_dir=docker_env["HOME"],
                ),
            ]
        )
        docker_command = (
            f"docker rm -f {container_name} > /dev/null 2>&1 || true; "
            f"mkdir -p {shlex.quote(host_trace_dir)} && chmod 777 {shlex.quote(host_trace_dir)}; "
            + " ".join(shlex.quote(part) for part in docker_argv)
        )
        return docker_command

    @staticmethod
    def resolve_node_for_launch(
        node: Mapping[str, object],
        config: ClusterLaunchConfig,
    ) -> Mapping[str, object]:
        launch_profile = resolve_container_launch_profile(config.container_profile)
        if launch_profile is None or launch_profile.container_name_template is None:
            return node
        container = node.get("container")
        if not isinstance(container, Mapping):
            return node
        resolved_container = dict(container)
        resolved_container["name"] = launch_profile.container_name_template.format(
            target=config.target,
            profile=launch_profile.name,
            ros_domain_id=container.get("ros_domain_id", 0),
            configured_name=container.get("name", "sim_worker"),
        )
        resolved_node = dict(node)
        resolved_node["container"] = resolved_container
        return resolved_node

    @staticmethod
    def _resolve_node_host_home(node: Mapping[str, object]) -> str:
        host_home = node.get("host_home")
        if host_home:
            return str(host_home).rstrip("/")
        return f"/home/{node.get('user', 'passd')}"

    @staticmethod
    def _resolve_worker_actor_address(
        head_address: str,
        *,
        start_ray_worker_node: bool,
    ) -> str:
        if start_ray_worker_node or head_address.startswith("ray://"):
            return head_address
        if "://" in head_address:
            return head_address
        host = head_address.rsplit(":", 1)[0]
        return f"ray://{host}:10001"

    def _build_container_bootstrap(
        self,
        worker_command: str,
        workspace: str,
        head_address: str,
        node_ip_address: str,
        container_password: str,
        *,
        worker_env: Mapping[str, str] | None = None,
        start_ray_worker_node: bool = True,
        bootstrap_apt_packages: tuple[str, ...] = ("xvfb",),
        bootstrap_pip_packages: tuple[str, ...] = ("ray==2.55.0",),
        validate_runtime_monitor: bool = False,
        start_xvfb: bool = True,
        home_dir: str | None = None,
    ) -> str:
        sudo_cmd = f"echo {shlex.quote(container_password)} | sudo -S"
        worker_env_prefix = " ".join(
            f"{name}={shlex.quote(value)}"
            for name, value in (worker_env or {}).items()
        )
        worker_entrypoint = (
            f"{worker_env_prefix} {worker_command}"
            if worker_env_prefix
            else worker_command
        )
        ray_start_command = (
            f"{workspace}/.local/bin/ray start --address={shlex.quote(head_address)} "
            f"--node-ip-address={shlex.quote(node_ip_address)} && "
            if start_ray_worker_node
            else ""
        )
        apt_install_command = (
            f"{sudo_cmd} apt-get update > /dev/null 2>&1 && "
            f"{sudo_cmd} DEBIAN_FRONTEND=noninteractive apt-get install -y "
            + " ".join(shlex.quote(package) for package in bootstrap_apt_packages)
            + " > /dev/null 2>&1 && "
            if bootstrap_apt_packages
            else ""
        )
        pip_install_command = (
            "python3 -m pip install --user --no-cache-dir "
            + " ".join(shlex.quote(package) for package in bootstrap_pip_packages)
            + " && "
            if bootstrap_pip_packages
            else ""
        )
        runtime_monitor_preflight = (
            f"source {shlex.quote(workspace + '/autoware/install/setup.bash')} && "
            f"cd {shlex.quote(workspace + '/AW-Runtime-Monitor')} && "
            "python3 -m py_compile main.py recorder/Recorder.py "
            "recorder/AWSIMClientOpStateTracker.py && "
            "python3 -c 'from recorder.Recorder import "
            "AWSIMClientOpStateTrackerTopic, AWSIM_CLIENT_OP_STATE_STOPPED; "
            "assert AWSIM_CLIENT_OP_STATE_STOPPED == 1' && "
            if validate_runtime_monitor
            else ""
        )
        runtime_dir_setup_command = (
            "export XDG_RUNTIME_DIR=/tmp/runtime-passd && "
            "mkdir -p \"$XDG_RUNTIME_DIR\" && "
            "chmod 700 \"$XDG_RUNTIME_DIR\" && "
        )
        supervisor_socket = "/tmp/awsim-process-supervisor.sock"
        startup_arguments = (
            "--startup-command "
            f"{shlex.quote('Xvfb :99 -screen 0 1920x1080x24')} "
            "--startup-warmup-sec 2 "
            if start_xvfb
            else ""
        )
        supervisor_entrypoint = (
            f"export {PROCESS_SUPERVISOR_SOCKET_ENV}="
            f"{shlex.quote(supervisor_socket)} && "
            "exec python3 -u -m runtime.container.supervised_process.server "
            f"--socket {shlex.quote(supervisor_socket)} "
            f"{startup_arguments}"
            f"--worker-command {shlex.quote(worker_entrypoint)}"
        )
        return (
            f"export HOME={shlex.quote(home_dir or workspace)} && "
            f"{apt_install_command}"
            f"{runtime_dir_setup_command}"
            f"{pip_install_command}"
            f"{runtime_monitor_preflight}"
            f"{ray_start_command}"
            f"cd {shlex.quote(workspace + '/AWSIM_launch')} && "
            f"{supervisor_entrypoint}"
        )

    def _launch_node_worker(self, *, node: Mapping[str, object], command: str) -> None:
        if node.get("ip") == self.master_ip:
            result = self.subprocess_run(
                command,
                shell=True,
                executable="/bin/bash",
                capture_output=True,
                text=True,
            )
            returncode = int(getattr(result, "returncode", 0))
            if returncode != 0:
                stdout = str(getattr(result, "stdout", "")).strip()
                stderr = str(getattr(result, "stderr", "")).strip()
                raise RuntimeError(
                    f"Failed to launch local worker container"
                    f" (stdout={stdout!r}, stderr={stderr!r})"
                )
            return

        ssh_target = f"{node['user']}@{node['ip']}"
        self.subprocess_popen(
            [
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=5",
                ssh_target,
                command,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def restart_node_worker(
        self,
        *,
        node: Mapping[str, object],
        config: ClusterLaunchConfig,
        head_address: str,
        sync_sources: bool = False,
    ) -> None:
        if sync_sources:
            self._sync_node_sources(node=node, config=config)
        command = self._build_worker_launch_command(
            node=node,
            config=config,
            head_address=head_address,
        )
        self._launch_node_worker(node=node, command=command)

    def get_container_state(self, node: Mapping[str, object]) -> ContainerState:
        container = node.get("container")
        if not isinstance(container, Mapping):
            return ContainerState(exists=False)
        container_name = str(container.get("name", ""))
        if not container_name:
            return ContainerState(exists=False)
        command = (
            "docker inspect -f '{{json .State}}' "
            f"{shlex.quote(container_name)} 2>/dev/null || true"
        )
        if node.get("ip") == self.master_ip:
            result = self.subprocess_run(
                command,
                shell=True,
                executable="/bin/bash",
                capture_output=True,
                text=True,
            )
        else:
            ssh_target = f"{node['user']}@{node['ip']}"
            result = self.subprocess_run(
                [
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=5",
                    ssh_target,
                    command,
                ],
                capture_output=True,
                text=True,
            )
        if int(getattr(result, "returncode", 0)) != 0:
            stderr = str(getattr(result, "stderr", "")).strip()
            return ContainerState(
                exists=True,
                running=False,
                status="inspect_error",
                inspect_error=stderr or "docker inspect failed",
            )
        stdout = str(getattr(result, "stdout", "")).strip()
        if not stdout:
            return ContainerState(exists=False)
        try:
            payload = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError as exc:
            return ContainerState(
                exists=True,
                running=False,
                status="inspect_error",
                inspect_error=str(exc),
            )
        return ContainerState(
            exists=True,
            running=bool(payload.get("Running", False)),
            status=str(payload.get("Status", "")),
            exit_code=(
                int(payload["ExitCode"])
                if isinstance(payload.get("ExitCode"), int)
                else None
            ),
            error=str(payload.get("Error", "")),
        )

    def get_container_logs(
        self,
        node: Mapping[str, object],
        *,
        tail_lines: int = 100,
    ) -> str:
        container = node.get("container")
        if not isinstance(container, Mapping):
            return ""
        container_name = str(container.get("name", ""))
        if not container_name:
            return ""
        command = (
            f"docker logs --tail {max(int(tail_lines), 0)} "
            f"{shlex.quote(container_name)} 2>&1 || true"
        )
        if node.get("ip") == self.master_ip:
            result = self.subprocess_run(
                command,
                shell=True,
                executable="/bin/bash",
                capture_output=True,
                text=True,
            )
        else:
            ssh_target = f"{node['user']}@{node['ip']}"
            result = self.subprocess_run(
                [
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=5",
                    ssh_target,
                    command,
                ],
                capture_output=True,
                text=True,
            )
        return str(getattr(result, "stdout", "") or "").strip()

    def probe_host_gpu(
        self,
        node: Mapping[str, object],
        *,
        timeout_sec: float = 5.0,
    ) -> tuple[bool, str]:
        return self._run_gpu_probe(
            node,
            "nvidia-smi --query-gpu=name,driver_version --format=csv,noheader",
            timeout_sec=timeout_sec,
        )

    def probe_container_gpu(
        self,
        node: Mapping[str, object],
        *,
        timeout_sec: float = 5.0,
    ) -> tuple[bool, str]:
        container = node.get("container")
        if not isinstance(container, Mapping) or not container.get("name"):
            return False, "container configuration is missing"
        container_name = shlex.quote(str(container["name"]))
        return self._run_gpu_probe(
            node,
            f"docker exec {container_name} nvidia-smi "
            "--query-gpu=name,driver_version --format=csv,noheader",
            timeout_sec=timeout_sec,
        )

    def remove_node_worker(self, node: Mapping[str, object]) -> tuple[bool, str]:
        container = node.get("container")
        if not isinstance(container, Mapping) or not container.get("name"):
            return False, "container configuration is missing"
        container_name = shlex.quote(str(container["name"]))
        return self._run_node_shell_command(
            node,
            f"docker rm -f {container_name}",
            timeout_sec=30.0,
        )

    def wait_for_container_gpu(
        self,
        node: Mapping[str, object],
        *,
        timeout_sec: float = 30.0,
        poll_interval_sec: float = 2.0,
        probe_timeout_sec: float = 5.0,
    ) -> tuple[bool, str]:
        deadline = time.monotonic() + max(float(timeout_sec), 0.0)
        last_detail = "container GPU did not become ready"
        while True:
            state = self.get_container_state(node)
            if state.exists and state.running:
                healthy, detail = self.probe_container_gpu(
                    node,
                    timeout_sec=probe_timeout_sec,
                )
                if healthy:
                    return True, detail
                last_detail = detail
            elif state.inspect_error:
                last_detail = state.inspect_error
            elif state.exists:
                last_detail = f"container is {state.status} (exit_code={state.exit_code})"
            if time.monotonic() >= deadline:
                return False, last_detail
            self.sleeper(
                min(
                    max(float(poll_interval_sec), 0.1),
                    max(deadline - time.monotonic(), 0.0),
                )
            )

    def _run_gpu_probe(
        self,
        node: Mapping[str, object],
        command: str,
        *,
        timeout_sec: float,
    ) -> tuple[bool, str]:
        ok, detail = self._run_node_shell_command(
            node,
            command,
            timeout_sec=timeout_sec,
        )
        return ok, detail or ("GPU ready" if ok else "nvidia-smi failed")

    def _run_node_shell_command(
        self,
        node: Mapping[str, object],
        command: str,
        *,
        timeout_sec: float,
    ) -> tuple[bool, str]:
        try:
            if node.get("ip") == self.master_ip:
                result = self.subprocess_run(
                    command,
                    shell=True,
                    executable="/bin/bash",
                    capture_output=True,
                    text=True,
                    timeout=float(timeout_sec),
                )
            else:
                ssh_target = f"{node['user']}@{node['ip']}"
                result = self.subprocess_run(
                    [
                        "ssh",
                        "-o",
                        "StrictHostKeyChecking=no",
                        "-o",
                        "BatchMode=yes",
                        "-o",
                        "ConnectTimeout=5",
                        ssh_target,
                        command,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=float(timeout_sec) + 5.0,
                )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)
        stdout = str(getattr(result, "stdout", "") or "").strip()
        stderr = str(getattr(result, "stderr", "") or "").strip()
        return int(getattr(result, "returncode", 1)) == 0, stderr or stdout

    def _sync_node_sources(
        self,
        *,
        node: Mapping[str, object],
        config: ClusterLaunchConfig,
    ) -> None:
        if node.get("ip") == self.master_ip:
            return

        sync_specs = self._build_sync_specs(config)
        if not sync_specs:
            return

        ssh_target = f"{node['user']}@{node['ip']}"
        ssh_options = [
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
        ]
        mkdir_command = "mkdir -p " + " ".join(spec.remote_dir for spec in sync_specs)
        self.subprocess_run(
            ["ssh", *ssh_options, ssh_target, mkdir_command],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        rsync_ssh = "ssh " + " ".join(shlex.quote(part) for part in ssh_options)
        for spec in sync_specs:
            command = [
                "rsync",
                "-rtvz",
                "--no-perms",
                "--no-owner",
                "--no-group",
            ]
            for exclude in spec.excludes:
                command.extend(["--exclude", exclude])
            command.extend(
                [
                    "-e",
                    rsync_ssh,
                    spec.source,
                    f"{ssh_target}:{spec.remote_path}",
                ]
            )
            self.subprocess_run(
                command,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        launch_profile = resolve_container_launch_profile(config.container_profile)
        requires_runtime_monitor = (
            launch_profile.requires_runtime_monitor if launch_profile is not None else True
        )
        if config.sync_aw_runtime_monitor and requires_runtime_monitor:
            self._verify_remote_runtime_monitor(node=node, ssh_options=ssh_options)

    def _verify_remote_runtime_monitor(
        self,
        *,
        node: Mapping[str, object],
        ssh_options: list[str],
    ) -> None:
        relative_paths = (
            "main.py",
            "recorder/Recorder.py",
            "recorder/AWSIMClientOpStateTracker.py",
        )
        local_root = Path("/home/passd/AW-Runtime-Monitor")
        expected_hashes = {
            relative_path: hashlib.sha256((local_root / relative_path).read_bytes()).hexdigest()
            for relative_path in relative_paths
        }
        remote_root = f"{self._resolve_node_host_home(node)}/AW-Runtime-Monitor"
        remote_files = [f"{remote_root}/{relative_path}" for relative_path in relative_paths]
        verify_command = (
            "sha256sum "
            + " ".join(shlex.quote(path) for path in remote_files)
            + " && python3 -m py_compile "
            + " ".join(shlex.quote(path) for path in remote_files)
        )
        ssh_target = f"{node['user']}@{node['ip']}"
        result = self.subprocess_run(
            ["ssh", *ssh_options, ssh_target, verify_command],
            capture_output=True,
            text=True,
        )
        if int(getattr(result, "returncode", 0)) != 0:
            stderr = str(getattr(result, "stderr", "") or "").strip()
            raise RuntimeError(
                f"AW-Runtime-Monitor validation failed on {ssh_target}: {stderr}"
            )
        actual_hashes: dict[str, str] = {}
        for line in str(getattr(result, "stdout", "") or "").splitlines():
            fields = line.split(maxsplit=1)
            if len(fields) != 2:
                continue
            actual_hashes[Path(fields[1].strip()).name] = fields[0]
        for relative_path, expected_hash in expected_hashes.items():
            actual_hash = actual_hashes.get(Path(relative_path).name)
            if actual_hash != expected_hash:
                raise RuntimeError(
                    "AW-Runtime-Monitor checksum mismatch on "
                    f"{ssh_target}: {relative_path}"
                )

    def _preflight_node_gpu(
        self,
        *,
        node: Mapping[str, object],
        config: ClusterLaunchConfig,
    ) -> tuple[bool, str]:
        launch_profile = resolve_container_launch_profile(config.container_profile)
        if launch_profile is not None and not launch_profile.requires_gpu:
            return True, ""
        container = node.get("container")
        if not isinstance(container, Mapping):
            return False, "container configuration is missing"
        image = (
            launch_profile.image
            if launch_profile is not None
            else str(container.get("image", "autoware_internal:2026"))
        )
        probe = [
            "docker",
            "run",
            "--rm",
            "--gpus",
            "all",
            "--entrypoint",
            "nvidia-smi",
            image,
            "--query-gpu=name,driver_version",
            "--format=csv,noheader",
        ]
        if node.get("ip") == self.master_ip:
            result = self.subprocess_run(probe, capture_output=True, text=True)
        else:
            ssh_target = f"{node['user']}@{node['ip']}"
            result = self.subprocess_run(
                [
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=5",
                    ssh_target,
                    " ".join(shlex.quote(part) for part in probe),
                ],
                capture_output=True,
                text=True,
            )
        if int(getattr(result, "returncode", 0)) == 0:
            return True, ""
        stderr = str(getattr(result, "stderr", "") or "").strip()
        stdout = str(getattr(result, "stdout", "") or "").strip()
        return False, stderr or stdout or "container GPU probe failed"

    @staticmethod
    def _build_sync_specs(config: ClusterLaunchConfig) -> tuple[SyncPathSpec, ...]:
        launch_profile = resolve_container_launch_profile(config.container_profile)
        requires_ros = launch_profile.requires_ros if launch_profile is not None else True
        requires_runtime_monitor = (
            launch_profile.requires_runtime_monitor if launch_profile is not None else True
        )
        specs: list[SyncPathSpec] = []
        if config.sync_awsim_launch:
            specs.append(
                SyncPathSpec(
                    label="AWSIM_launch",
                    source="/home/passd/AWSIM_launch/",
                    remote_path="~/AWSIM_launch/",
                    remote_dir="$HOME/AWSIM_launch",
                    excludes=("simulation_traces", "__pycache__", ".git", ".pytest_cache", "tmp"),
                )
            )
        if config.sync_awsim_script_py and requires_ros:
            specs.append(
                SyncPathSpec(
                    label="AWSIMScriptPy",
                    source="/home/passd/AWSIMScriptPy/",
                    remote_path="~/AWSIMScriptPy/",
                    remote_dir="$HOME/AWSIMScriptPy",
                    excludes=("__pycache__",),
                )
            )
        if config.sync_aw_runtime_monitor and requires_runtime_monitor:
            specs.append(
                SyncPathSpec(
                    label="AW-Runtime-Monitor",
                    source="/home/passd/AW-Runtime-Monitor/",
                    remote_path="~/AW-Runtime-Monitor/",
                    remote_dir="$HOME/AW-Runtime-Monitor",
                    excludes=("__pycache__",),
                )
            )
        if config.sync_autoware180_map and requires_ros:
            specs.append(
                SyncPathSpec(
                    label="autoware180 map",
                    source="/home/passd/autoware180_runtime/maps/",
                    remote_path="~/autoware180_runtime/maps/",
                    remote_dir="$HOME/autoware180_runtime/maps",
                )
            )
        return tuple(specs)


__all__ = [
    "ClusterLaunchConfig",
    "ClusterLaunchSummary",
    "ClusterManager",
    "ContainerState",
    "SyncPathSpec",
]
