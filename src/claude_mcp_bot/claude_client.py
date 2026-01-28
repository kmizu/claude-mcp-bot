"""Claude API Client - handles communication with Claude API."""

from typing import Any

from anthropic import Anthropic


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
    ) -> Any:
        """Send a chat request to Claude API.

        Args:
            messages: Conversation messages
            tools: Available tools in Claude API format

        Returns:
            Claude API response
        """
        kwargs = {
            "model": self.model,
            "max_tokens": 4096,
            "system": self.system_prompt,
            "messages": messages,
        }

        if tools:
            kwargs["tools"] = tools

        return self.client.messages.create(**kwargs)
