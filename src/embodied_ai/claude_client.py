"""Claude API Client - handles communication with Claude API."""

from datetime import datetime
from typing import Any

from anthropic import Anthropic


def is_claude_model(model_name: str) -> bool:
    """Return True if model identifier belongs to Claude family."""
    return model_name.strip().lower().startswith("claude")


class InvalidModelSelectionError(ValueError):
    """Raised when a non-Claude model is selected."""


class ClaudeClient:
    """Client for Claude API with tool use support."""

    def __init__(self, model: str, system_prompt: str):
        self.client = Anthropic()
        self.model = model
        self.system_prompt = system_prompt

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> Any:
        """Send a chat request to Claude API.

        Args:
            messages: Conversation messages
            tools: Available tools in Claude API format
            model: Optional per-request model override (Claude family only)

        Returns:
            Claude API response
        """
        selected_model = model or self.model
        if not is_claude_model(selected_model):
            raise InvalidModelSelectionError(
                f"Only Claude models are allowed, got: {selected_model}"
            )

        kwargs = {
            "model": selected_model,
            "max_tokens": 4096,
            "system": self.system_prompt,
            "messages": messages,
        }

        if tools:
            kwargs["tools"] = tools

        return self.client.messages.create(**kwargs)

    def list_claude_models(self, limit: int = 100) -> list[dict[str, str]]:
        """Fetch available Claude model identifiers from Anthropic API."""
        page = self.client.models.list(limit=limit)
        raw_items = list(getattr(page, "data", []) or [])

        models: list[dict[str, str]] = []
        for item in raw_items:
            if not is_claude_model(item.id):
                continue
            created_at = item.created_at
            if isinstance(created_at, datetime):
                created = created_at.isoformat()
            else:
                created = str(created_at)
            models.append({
                "id": item.id,
                "display_name": item.display_name,
                "created_at": created,
            })

        models.sort(key=lambda m: m["created_at"], reverse=True)

        if is_claude_model(self.model) and all(m["id"] != self.model for m in models):
            models.insert(0, {
                "id": self.model,
                "display_name": self.model,
                "created_at": "",
            })

        return models
