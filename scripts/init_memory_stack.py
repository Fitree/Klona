#!/usr/bin/env python3
"""Interactive setup for the server-side KLONA memory stack."""

from __future__ import annotations

import json
import os
import pty
import select
import subprocess
import sys
import termios
import threading
import tty
import urllib.error
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"

ENV_ORDER = (
    "HOST_VAULT_DIR",
    "HIGH_LEVEL_MCP_HOST_PORT",
    "HIGH_LEVEL_MCP_AUTH_TOKEN",
    "HIGH_LEVEL_ALLOWED_HOSTS",
    "MEMORY_AGENT_QUEUE_DB",
    "MEMORY_AGENT_STATE_DIR",
    "MEMORY_AGENT_TIMEOUT_SECONDS",
    "MEMORY_AGENT_MAX_RETRIES",
    "OPENCODE_HOST",
    "OPENCODE_PORT",
)

DEFAULTS = {
    "HOST_VAULT_DIR": "./vault",
    "HIGH_LEVEL_MCP_HOST_PORT": "32311",
    "HIGH_LEVEL_MCP_AUTH_TOKEN": "",
    "HIGH_LEVEL_ALLOWED_HOSTS": "",
    "MEMORY_AGENT_QUEUE_DB": "/state/queue.db",
    "MEMORY_AGENT_STATE_DIR": "/state",
    "MEMORY_AGENT_TIMEOUT_SECONDS": "600",
    "MEMORY_AGENT_MAX_RETRIES": "2",
    "OPENCODE_HOST": "127.0.0.1",
    "OPENCODE_PORT": "4096",
}


BUILD_CMD = ["docker", "compose", "build"]
START_MEMORY_SERVER_CMD = ["docker", "compose", "up", "-d", "--wait", "memory-server"]
RUN_MEMORY_AGENT_CMD = ["docker", "compose", "run", "--rm", "--service-ports", "--no-deps", "memory-agent"]
STOP_MEMORY_SERVER_CMD = ["docker", "compose", "stop", "memory-server"]
DOCKER_DETACH_SEQUENCE = b"\x10\x11"
HEALTH_POLL_INTERVAL_SECONDS = 2.0
EXPECTED_HEALTH_STATUS = "ok"
EXPECTED_HEALTH_SERVER = "klona-memory-agent"
EXPECTED_HEALTH_VERSION = "0.1.0"


def _ask(prompt: str, default: str | None = None) -> str:
    label = f"{prompt} [{default}]: " if default is not None else f"{prompt}: "
    try:
        value = input(label).strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise SystemExit("setup cancelled") from exc
    if value:
        return value
    if default is not None:
        return default
    raise SystemExit(f"{prompt} cannot be empty")


def build_env(values: dict[str, str]) -> str:
    return "\n".join(f"{key}={values[key]}" for key in ENV_ORDER) + "\n"


def high_level_health_url(values: dict[str, str]) -> str:
    return f"http://localhost:{values['HIGH_LEVEL_MCP_HOST_PORT']}/health"


def _is_healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            return (
                payload.get("status") == EXPECTED_HEALTH_STATUS
                and payload.get("server") == EXPECTED_HEALTH_SERVER
                and payload.get("version") == EXPECTED_HEALTH_VERSION
            )
    except (OSError, urllib.error.URLError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def _poll_health_until_detach(url: str, master_fd: int, detached: threading.Event, done: threading.Event) -> None:
    while not done.is_set() and not detached.is_set():
        if _is_healthy(url):
            try:
                os.write(master_fd, DOCKER_DETACH_SEQUENCE)
            except OSError:
                return
            detached.set()
            os.write(
                sys.stdout.fileno(),
                (
                    "\nMemory-agent is healthy; detaching and leaving services running.\n"
                    "Stop later with `docker compose down`, or inspect one-off containers with "
                    "`docker compose ps` and stop the memory-agent container explicitly.\n"
                ).encode(),
            )
            return
        done.wait(HEALTH_POLL_INTERVAL_SECONDS)


def _status_to_return(status: int | None, detached: bool) -> tuple[int, bool]:
    if status is None:
        return 1, False
    if os.WIFEXITED(status):
        return_code = os.WEXITSTATUS(status)
    elif os.WIFSIGNALED(status):
        return_code = 128 + os.WTERMSIG(status)
    else:
        return_code = 1
    return return_code, detached and return_code == 0


def run_memory_agent_until_healthy(health_url: str) -> tuple[int, bool]:
    """Run memory-agent interactively and detach from the same container after health is ready."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("Non-TTY detected; running memory-agent in blocking foreground mode without auto-detach.")
        return subprocess.call(RUN_MEMORY_AGENT_CMD, cwd=REPO_ROOT), False

    pid, master_fd = pty.fork()
    if pid == 0:
        os.chdir(REPO_ROOT)
        os.execvp(RUN_MEMORY_AGENT_CMD[0], RUN_MEMORY_AGENT_CMD)

    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    old_termios = termios.tcgetattr(stdin_fd)
    detached = threading.Event()
    done = threading.Event()
    poller = threading.Thread(
        target=_poll_health_until_detach,
        args=(health_url, master_fd, detached, done),
        daemon=True,
    )
    poller.start()

    status: int | None = None
    try:
        tty.setraw(stdin_fd)
        while True:
            waited_pid, waited_status = os.waitpid(pid, os.WNOHANG)
            if waited_pid == pid:
                status = waited_status
                break

            readable, _, _ = select.select([master_fd, stdin_fd], [], [], 0.2)
            if master_fd in readable:
                try:
                    output = os.read(master_fd, 4096)
                except OSError:
                    output = b""
                if output:
                    os.write(stdout_fd, output)
                else:
                    _, status = os.waitpid(pid, 0)
                    break
            if stdin_fd in readable:
                user_input = os.read(stdin_fd, 4096)
                if user_input:
                    os.write(master_fd, user_input)
    finally:
        done.set()
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_termios)
        os.close(master_fd)

    return _status_to_return(status, detached.is_set())


def collect_values() -> dict[str, str]:
    """Ask non-model setup questions only.

    OpenCode auth, model, and reasoning-effort selection intentionally happen later inside
    the memory-agent container so choices match the final runtime environment.
    """
    high_port = _ask("High-level user-agent MCP host port", DEFAULTS["HIGH_LEVEL_MCP_HOST_PORT"])
    return {
        "HOST_VAULT_DIR": _ask("Host markdown vault directory", DEFAULTS["HOST_VAULT_DIR"]),
        "HIGH_LEVEL_MCP_HOST_PORT": high_port,
        "HIGH_LEVEL_MCP_AUTH_TOKEN": _ask("High-level user-agent MCP bearer token (empty disables auth)", DEFAULTS["HIGH_LEVEL_MCP_AUTH_TOKEN"]),
        "HIGH_LEVEL_ALLOWED_HOSTS": _ask(
            "High-level allowed hosts (empty allows all Host headers)", DEFAULTS["HIGH_LEVEL_ALLOWED_HOSTS"]
        ),
        "MEMORY_AGENT_QUEUE_DB": _ask("Memory-agent queue DB path in container", DEFAULTS["MEMORY_AGENT_QUEUE_DB"]),
        "MEMORY_AGENT_STATE_DIR": _ask("Memory-agent state dir in container", DEFAULTS["MEMORY_AGENT_STATE_DIR"]),
        "MEMORY_AGENT_TIMEOUT_SECONDS": _ask("Recall timeout seconds", DEFAULTS["MEMORY_AGENT_TIMEOUT_SECONDS"]),
        "MEMORY_AGENT_MAX_RETRIES": _ask("Queue retry attempts", DEFAULTS["MEMORY_AGENT_MAX_RETRIES"]),
        "OPENCODE_HOST": _ask("OpenCode internal host", DEFAULTS["OPENCODE_HOST"]),
        "OPENCODE_PORT": _ask("OpenCode internal port", DEFAULTS["OPENCODE_PORT"]),
    }


def main() -> int:
    if ENV_PATH.exists():
        answer = _ask(".env already exists; overwrite? Type yes to continue", "no")
        if answer.lower() != "yes":
            raise SystemExit("leaving existing .env unchanged")
    values = collect_values()
    ENV_PATH.write_text(build_env(values), encoding="utf-8")
    print(f"Wrote {ENV_PATH}")
    print("Building Docker images...")
    build_result = subprocess.run(BUILD_CMD, cwd=REPO_ROOT, check=False)
    if build_result.returncode != 0:
        return build_result.returncode

    print("Starting low-level memory-server detached...")
    server_result = subprocess.run(START_MEMORY_SERVER_CMD, cwd=REPO_ROOT, check=False)
    if server_result.returncode != 0:
        print("Stopping partially started low-level memory-server...")
        subprocess.run(STOP_MEMORY_SERVER_CMD, cwd=REPO_ROOT, check=False)
        return server_result.returncode

    print("Starting memory-agent interactively. Answer its OpenCode auth/model prompts below.")
    detached = False
    try:
        result, detached = run_memory_agent_until_healthy(high_level_health_url(values))
        return result
    finally:
        if detached:
            print("Leaving detached memory-server and memory-agent running.")
        else:
            print("Stopping detached low-level memory-server...")
            subprocess.run(STOP_MEMORY_SERVER_CMD, cwd=REPO_ROOT, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
