from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Mapping

from redis_cluster import cluster_config


@dataclass(frozen=True)
class ClusterLaunchConfig:
    case_kind: str
    run_mode: str
    output_path: str
    queue_actor_name: str
    queue_namespace: str = "awsim_cluster"
    queue_address: str | None = None
    config_module: str | None = None
    dataset_csv: str | None = None
    ext_mode: str = "cvm"
    headless: bool = False
    with_host_worker: bool = False
    refresh_interval: int | None = 10
    restart_on_refresh: bool = True
    shared_store_actor_name: str | None = None
    shared_store_namespace: str = "awsim_cluster"
    shared_store_address: str | None = None


@dataclass(frozen=True)
class ClusterLaunchSummary:
    head_address: str
    launched_workers: tuple[str, ...]
    skipped_workers: tuple[str, ...]


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

    def start_cluster(self, config: ClusterLaunchConfig) -> ClusterLaunchSummary:
        self._start_head_node()
        head_address = config.queue_address or f"{self.master_ip}:{self.ray_port}"
        launched_workers: list[str] = []
        skipped_workers: list[str] = []

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

            command = self._build_worker_launch_command(
                node=node,
                config=config,
                head_address=head_address,
            )
            self._launch_node_worker(node=node, command=command)
            launched_workers.append(str(node.get("machine", "unknown")))

        return ClusterLaunchSummary(
            head_address=head_address,
            launched_workers=tuple(launched_workers),
            skipped_workers=tuple(skipped_workers),
        )

    def _start_head_node(self) -> None:
        self.subprocess_run(
            ["ray", "stop", "--force"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        result = self.subprocess_run(
            [
                "ray",
                "start",
                "--head",
                f"--port={self.ray_port}",
                f"--node-ip-address={self.master_ip}",
                "--dashboard-host=0.0.0.0",
                "--num-cpus=0",
                "--disable-usage-stats",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
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
        container = dict(node["container"])
        workspace = str(container.get("workspace", "/home/passd"))
        container_name = str(container.get("name", "sim_worker"))
        ros_domain_id = str(container.get("ros_domain_id", 0))
        container_user = str(container.get("user", "passd"))
        container_image = str(container.get("image", "autoware_internal:2026"))
        container_password = str(container.get("password", "passd"))

        worker_argv = [
            "python3",
            "-u",
            "run_worker_v2.py",
            "--queue-actor-name",
            config.queue_actor_name,
            "--queue-address",
            head_address,
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
            "--ext_mode",
            config.ext_mode,
        ]
        if config.config_module:
            worker_argv.extend(["--config-module", config.config_module])
        if config.dataset_csv and not config.shared_store_actor_name:
            worker_argv.extend(["--dataset-csv", config.dataset_csv])
        if config.refresh_interval is not None:
            worker_argv.extend(["--refresh-interval", str(config.refresh_interval)])
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
                    config.shared_store_address or head_address,
                ]
            )

        worker_command = " ".join(shlex.quote(part) for part in worker_argv)
        docker_command = (
            f"docker rm -f {container_name} > /dev/null 2>&1 || true; "
            f"mkdir -p ~/simulation_traces_{container_name} && chmod 777 ~/simulation_traces_{container_name}; "
            f"docker run -d -it --name {container_name} --user {container_user} "
            "--net=host --privileged --gpus all --shm-size=32gb "
            f"-e ROS_DOMAIN_ID={ros_domain_id} -e EXEC_MODE=cluster -e HOME={workspace} "
            f"-v ~/AWSIM_launch:{workspace}/AWSIM_launch "
            f"-v ~/simulation_traces_{container_name}:{workspace}/simulation_traces "
            f"{container_image} "
            f"bash -i -c {shlex.quote(self._build_container_bootstrap(worker_command, workspace, head_address, container_password))}"
        )
        return docker_command

    def _build_container_bootstrap(
        self,
        worker_command: str,
        workspace: str,
        head_address: str,
        container_password: str,
    ) -> str:
        sudo_cmd = f"echo {shlex.quote(container_password)} | sudo -S"
        return (
            f"export HOME={shlex.quote(workspace)} && "
            f"{sudo_cmd} apt-get update > /dev/null 2>&1 && "
            f"{sudo_cmd} DEBIAN_FRONTEND=noninteractive apt-get install -y xvfb > /dev/null 2>&1 && "
            "Xvfb :99 -screen 0 1920x1080x24 > /dev/null 2>&1 & "
            f"python3 -m pip install --user --no-cache-dir ray==2.55.0 && "
            f"{workspace}/.local/bin/ray start --address={shlex.quote(head_address)} "
            f"--node-ip-address=$(hostname -I | awk '{{print $1}}') && "
            f"cd {workspace}/AWSIM_launch && "
            f"DISPLAY=:99 VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.json {worker_command}"
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


__all__ = [
    "ClusterLaunchConfig",
    "ClusterLaunchSummary",
    "ClusterManager",
]
