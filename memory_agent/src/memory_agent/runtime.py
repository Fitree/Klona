"""Runtime startup helpers for the memory-agent container."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Sequence

from .config import load_settings, Settings
from .constants import (
    DEFAULT_OPENCODE_HOST,
    DEFAULT_OPENCODE_PORT,
    LOW_LEVEL_MCP_ALLOWED_TOOL_PATTERN,
    LOW_LEVEL_MCP_NAME,
    MEMORY_AGENT_NAME,
)
from .system_prompt import MEMORY_AGENT_SYSTEM_PROMPT


ALLOWED_TOOL_PATTERN = LOW_LEVEL_MCP_ALLOWED_TOOL_PATTERN


@dataclass(frozen=True)
class ModelChoice:
    model: str
    raw: str
    variants: tuple[str, ...]


def run_auth_prompt_loop(opencode_env: dict[str, str]) -> None:
    """Optionally run interactive `opencode auth login` with retry/proceed/terminate choices."""
    while True:
        choice = input("Run OpenCode auth login now? [y/N] ").strip().lower()
        if choice in {"", "n", "no"}:
            return
        if choice in {"y", "yes"}:
            try:
                result = subprocess.run(["opencode", "auth", "login"], check=False, env=opencode_env)
                return_code = result.returncode
            except KeyboardInterrupt:
                return_code = 130
            if return_code == 0:
                return
            next_choice = input("OpenCode auth failed. Retry auth, proceed without auth, or terminate? [r/p/t] ").strip().lower()
            if next_choice in {"r", "retry"}:
                continue
            if next_choice in {"p", "proceed", ""}:
                return
            raise SystemExit(return_code or 1)


def discover_models(opencode_env: dict[str, str] | None = None) -> list[ModelChoice]:
    """Return available OpenCode models using the documented shell command."""
    result = subprocess.run(["opencode", "models", "--verbose"], check=False, text=True, capture_output=True, env=opencode_env)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "opencode models --verbose failed")
    models: list[ModelChoice] = []
    lines = result.stdout.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped and not stripped.lower().startswith(("model", "provider", "-")):
            model = _extract_model_name(stripped)
            if model:
                json_text, next_index = _collect_following_json(lines, index + 1)
                variants = _extract_variants_from_json(json_text) if json_text else _extract_variants(stripped)
                models.append(ModelChoice(model=model, raw=(stripped + ("\n" + json_text if json_text else "")), variants=variants))
                index = next_index
                continue
        index += 1
    return models


def _collect_following_json(lines: list[str], start_index: int) -> tuple[str, int]:
    while start_index < len(lines) and not lines[start_index].strip():
        start_index += 1
    if start_index >= len(lines) or not lines[start_index].lstrip().startswith("{"):
        return "", start_index
    collected: list[str] = []
    depth = 0
    index = start_index
    while index < len(lines):
        line = lines[index]
        collected.append(line)
        depth += line.count("{") - line.count("}")
        index += 1
        if depth <= 0:
            break
    return "\n".join(collected), index


def _extract_variants_from_json(json_text: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        return _extract_variants(json_text)
    variants = parsed.get("variants") if isinstance(parsed, dict) else None
    if isinstance(variants, dict):
        return tuple(str(key) for key in variants.keys())
    if isinstance(variants, list):
        return tuple(str(value) for value in variants)
    return _extract_variants(json_text)


def _extract_model_name(line: str) -> str:
    for token in re.split(r"\s+", line):
        cleaned = token.strip(" ,|[]()")
        if "/" in cleaned and not cleaned.startswith(("http://", "https://")):
            return cleaned
    return ""


def _extract_variants(line: str) -> tuple[str, ...]:
    lowered = line.lower()
    if "variant" not in lowered and "reasoning" not in lowered:
        return ()
    known_order = ("minimal", "low", "medium", "high", "xhigh")
    found = [variant for variant in known_order if re.search(rf"\b{re.escape(variant)}\b", lowered)]
    return tuple(found)


def choose_model_and_reasoning(default_model: str = "", default_reasoning: str = "", opencode_env: dict[str, str] | None = None) -> tuple[str, str]:
    models = discover_models(opencode_env)
    if not models:
        if default_model:
            return default_model, default_reasoning
        raise RuntimeError("OpenCode did not report any available models")
    for line in format_model_options(models):
        print(line)
    while True:
        raw = input("Choose OpenCode model number: ").strip()
        try:
            selected_index = int(raw)
            if selected_index < 1 or selected_index > len(models):
                raise ValueError
            choice = models[selected_index - 1]
        except ValueError:
            print("Choose a valid model number.")
            continue
        break
    if choice.variants:
        print(f"Reasoning effort/variant options for {choice.model}:")
        for index, variant in enumerate(choice.variants, 1):
            print(f"{index}. {variant}")
        while True:
            raw_variant = input("Choose reasoning effort/variant number (blank for default): ").strip()
            if not raw_variant:
                reasoning = default_reasoning if default_reasoning in choice.variants else ""
                break
            try:
                selected_variant_index = int(raw_variant)
                if selected_variant_index < 1 or selected_variant_index > len(choice.variants):
                    raise ValueError
                reasoning = choice.variants[selected_variant_index - 1]
            except ValueError:
                print("Choose a valid reasoning effort/variant number.")
                continue
            break
    else:
        reasoning = ""
    return choice.model, reasoning


def format_model_options(models: Sequence[ModelChoice]) -> tuple[str, ...]:
    """Return concise user-facing model menu lines without verbose discovery details."""
    return tuple(f"{index}. {choice.model}" for index, choice in enumerate(models, 1))


def generate_opencode_config(settings: Settings, model: str = "", reasoning_effort: str | None = None) -> Path:
    """Write an OpenCode config that only grants the low-level Klona memory MCP tools."""
    selected_model = model or settings.opencode_model
    selected_reasoning = settings.opencode_reasoning_effort if reasoning_effort is None else reasoning_effort
    agent_model_config = {"model": selected_model}
    if selected_reasoning:
        agent_model_config["variant"] = selected_reasoning
        # Compatibility for older OpenCode builds that still accepted reasoningEffort.
        agent_model_config["reasoningEffort"] = selected_reasoning
    config = {
        "model": selected_model,
        "agent": {
            MEMORY_AGENT_NAME: {
                **agent_model_config,
                "mode": "primary",
                "prompt": MEMORY_AGENT_SYSTEM_PROMPT,
                "permission": {
                    "*": "deny",
                    ALLOWED_TOOL_PATTERN: "allow",
                },
            }
        },
        "mcp": {
            LOW_LEVEL_MCP_NAME: {
                "type": "remote",
                "url": settings.low_level_mcp_url,
                "enabled": True,
                "oauth": False,
                "headers": ({"Authorization": f"Bearer {settings.low_level_mcp_auth_token}"} if settings.low_level_mcp_auth_token else {}),
            }
        },
        "permission": {
            "*": "deny",
            ALLOWED_TOOL_PATTERN: "allow",
        },
    }
    settings.opencode_config_path.parent.mkdir(parents=True, exist_ok=True)
    settings.opencode_config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return settings.opencode_config_path


def opencode_config_environment(config_path: Path) -> dict[str, str]:
    """Return environment variables that make OpenCode load the generated config.

    Current OpenCode reliably honors XDG_CONFIG_HOME. The generated config path is
    therefore shaped as `${XDG_CONFIG_HOME}/opencode/opencode.json` by default.
    OPENCODE_CONFIG is also set for compatibility with documented builds that
    honor it, but XDG_CONFIG_HOME is the primary loading mechanism here.
    """
    env = os.environ.copy()
    env["OPENCODE_CONFIG"] = str(config_path)
    if config_path.name == "opencode.json" and config_path.parent.name == "opencode":
        config_path.parent.mkdir(parents=True, exist_ok=True)
        env["XDG_CONFIG_HOME"] = str(config_path.parent.parent)
    else:
        raise ValueError(
            "OPENCODE_CONFIG_PATH must end with opencode/opencode.json so OpenCode can load it via XDG_CONFIG_HOME"
        )
    return env


def start_opencode_serve(config_path: Path, port: int = DEFAULT_OPENCODE_PORT) -> subprocess.Popen:
    env = opencode_config_environment(config_path)
    return subprocess.Popen(["opencode", "serve", "--hostname", os.environ.get("OPENCODE_HOST", DEFAULT_OPENCODE_HOST), "--port", str(port)], env=env)


def start_process(args: Sequence[str]) -> subprocess.Popen:
    return subprocess.Popen(list(args))


def supervise(processes: Sequence[subprocess.Popen]) -> int:
    try:
        while True:
            for proc in processes:
                code = proc.poll()
                if code is not None:
                    return code
            time.sleep(1)
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)


def main() -> None:
    settings = load_settings()
    opencode_env = opencode_config_environment(settings.opencode_config_path)
    run_auth_prompt_loop(opencode_env)
    model, reasoning = choose_model_and_reasoning(settings.opencode_model, settings.opencode_reasoning_effort, opencode_env)
    os.environ["MEMORY_AGENT_MODEL"] = model
    os.environ["MEMORY_AGENT_REASONING_EFFORT"] = reasoning
    config_path = generate_opencode_config(settings, model, reasoning)
    opencode_proc = start_opencode_serve(config_path, int(os.environ.get("OPENCODE_PORT", str(DEFAULT_OPENCODE_PORT))))
    server_proc = start_process([sys.executable, "-m", "uvicorn", "memory_agent.server:app", "--host", "0.0.0.0", "--port", "8080"])
    worker_proc = start_process([sys.executable, "-m", "memory_agent.worker"])
    raise SystemExit(supervise([opencode_proc, server_proc, worker_proc]))


if __name__ == "__main__":
    main()
