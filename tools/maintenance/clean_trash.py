#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from redis_cluster import cluster_config  # noqa: E402


@dataclass(frozen=True)
class NodeTarget:
    key: str
    machine: str
    user: str
    ip: str
    enabled: bool
    password: str | None = None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clean GNOME Trash on cluster nodes without touching experiment data.",
    )
    parser.add_argument(
        "--nodes",
        default="enabled",
        help="Comma-separated node labels/machines/IPs, or 'enabled', or 'all'. Default: enabled.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete Trash contents. Without this, only prints current usage.",
    )
    parser.add_argument(
        "--connect-timeout",
        type=int,
        default=8,
        help="SSH connection timeout in seconds.",
    )
    return parser.parse_args(argv)


def _iter_nodes() -> Iterable[NodeTarget]:
    for key, info in cluster_config.CLUSTER_NODES.items():
        yield NodeTarget(
            key=key,
            machine=str(info.get("machine", key)),
            user=str(info.get("user", "")),
            ip=str(info.get("ip", "")),
            enabled=bool(info.get("enabled", False)),
            password=(
                str(info.get("container", {}).get("password"))
                if info.get("container", {}).get("password")
                else None
            ),
        )


def _select_nodes(selector: str) -> list[NodeTarget]:
    nodes = list(_iter_nodes())
    normalized = selector.strip().lower()
    if normalized == "all":
        return nodes
    if normalized == "enabled":
        return [node for node in nodes if node.enabled]

    wanted = {item.strip() for item in selector.split(",") if item.strip()}
    selected: list[NodeTarget] = []
    for node in nodes:
        aliases = {
            node.key,
            node.machine,
            node.ip,
            node.machine.replace("号機", ""),
        }
        if aliases & wanted:
            selected.append(node)
    if not selected:
        raise ValueError(f"No cluster nodes matched --nodes={selector!r}")
    return selected


def _is_local_node(node: NodeTarget) -> bool:
    return node.ip == cluster_config.MASTER_IP or node.ip in {"127.0.0.1", "localhost"}


def _remote_script(*, apply: bool) -> str:
    action = (
        "find \"$TRASH/files\" \"$TRASH/info\" -mindepth 1 -maxdepth 1 -exec rm -r -- {} + 2>/dev/null || true"
        if apply
        else "true"
    )
    return f"""
set -u
TRASH="$HOME/.local/share/Trash"
mkdir -p "$TRASH/files" "$TRASH/info"
echo "before: $(du -sh "$TRASH" 2>/dev/null || true)"
{action}
echo "after : $(du -sh "$TRASH" 2>/dev/null || true)"
df -h /
""".strip()


def _run_with_password(command: list[str], password: str | None, *, timeout: int) -> tuple[int, str, str]:
    if not password:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        return result.returncode, result.stdout, result.stderr

    try:
        import pexpect
    except ImportError:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        return result.returncode, result.stdout, result.stderr

    child = pexpect.spawn(command[0], command[1:], encoding="utf-8", timeout=timeout)
    chunks: list[str] = []
    try:
        while True:
            index = child.expect(
                ["[Pp]assword:", "continue connecting", pexpect.EOF, pexpect.TIMEOUT]
            )
            chunks.append(child.before)
            if index == 0:
                child.sendline(password)
            elif index == 1:
                child.sendline("yes")
            elif index == 2:
                break
            else:
                child.close(force=True)
                return 124, "".join(chunks), "timeout while waiting for SSH command"
    finally:
        if child.isalive():
            child.close()
    return int(child.exitstatus or 0), "".join(chunks).replace("\r", ""), ""


def _run_node(node: NodeTarget, *, apply: bool, connect_timeout: int) -> int:
    script = _remote_script(apply=apply)
    print(f"===== {node.machine} ({node.user}@{node.ip}) =====")
    if _is_local_node(node):
        command = ["bash", "-lc", script]
    else:
        command = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            f"ConnectTimeout={connect_timeout}",
            f"{node.user}@{node.ip}",
            "bash",
            "-lc",
            shlex.quote(script),
        ]

    stdout, stderr = "", ""
    if _is_local_node(node):
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        returncode, stdout, stderr = result.returncode, result.stdout, result.stderr
    else:
        returncode, stdout, stderr = _run_with_password(
            command,
            node.password,
            timeout=max(connect_timeout, 1) + 60,
        )
    if stdout:
        print(stdout.rstrip())
    if stderr:
        print(stderr.rstrip(), file=sys.stderr)
    print()
    return returncode


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        nodes = _select_nodes(args.nodes)
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    if not args.apply:
        print("[dry-run] Trash usage only. Add --apply to delete Trash contents.")

    exit_code = 0
    for node in nodes:
        exit_code = max(
            exit_code,
            _run_node(node, apply=bool(args.apply), connect_timeout=int(args.connect_timeout)),
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
