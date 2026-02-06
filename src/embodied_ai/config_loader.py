"""Configuration loading utilities shared by CLI and web runtimes."""

import json
from pathlib import Path
from typing import Any


def load_config(config_path: str | None = None) -> tuple[dict[str, Any], str]:
    """Load configuration JSON and return `(config, resolved_path)`."""
    if config_path is None:
        candidates = [
            Path.cwd() / "config.json",
            Path(__file__).parent.parent.parent.parent / "config.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                config_path = str(candidate)
                break
        else:
            raise FileNotFoundError("config.json not found")

    with open(config_path, encoding="utf-8") as f:
        return json.load(f), config_path


def resolve_bot_paths(
    config: dict[str, Any], config_path: str
) -> tuple[str, str, str]:
    """Resolve memory/desire/self file paths relative to the config location."""
    bot_config = config.get("bot", {})
    config_dir = Path(config_path).parent if config_path else Path.cwd()

    memory_path = bot_config.get("memory_path", "memories.json")
    if not Path(memory_path).is_absolute():
        memory_path = str(config_dir / memory_path)

    desire_path = bot_config.get("desire_path", "desires.json")
    if not Path(desire_path).is_absolute():
        desire_path = str(config_dir / desire_path)

    self_path = bot_config.get("self_path", "self.json")
    if not Path(self_path).is_absolute():
        self_path = str(config_dir / self_path)

    return memory_path, desire_path, self_path


def resolve_system_prompt(config: dict[str, Any], config_path: str) -> str:
    """Resolve and load Claude system prompt from `claude.system_prompt_file`."""
    claude_config = config.get("claude", {})
    prompt_file = claude_config.get("system_prompt_file")
    if not prompt_file:
        raise ValueError("Missing required config: claude.system_prompt_file")

    config_dir = Path(config_path).parent if config_path else Path.cwd()
    prompt_path = Path(prompt_file)
    if not prompt_path.is_absolute():
        prompt_path = config_dir / prompt_path

    if not prompt_path.exists():
        raise FileNotFoundError(
            f"System prompt file not found: {prompt_path}"
        )

    prompt = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"System prompt file is empty: {prompt_path}")
    return prompt
