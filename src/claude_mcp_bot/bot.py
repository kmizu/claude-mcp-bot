"""Bot - integrates MCP client and Claude API for autonomous interaction."""

import asyncio
from typing import Any

from .claude_client import ClaudeClient
from .desire import DesireManager
from .mcp_client import MCPClient
from .memory import MemoryManager
from .self import SelfManager


class Bot:
    """Autonomous bot with MCP tool access and Claude intelligence."""

    def __init__(
        self,
        mcp_client: MCPClient,
        claude_client: ClaudeClient,
        memory_path: str = "memories.json",
        desire_path: str = "desires.json",
        self_path: str = "self.json",
        autonomous_interval: float = 30.0,
    ):
        self.mcp = mcp_client
        self.claude = claude_client
        self.tools: list[dict[str, Any]] = []
        self.autonomous_interval = autonomous_interval
        self.running = False

        # Initialize memory system
        self.memory = MemoryManager(
            storage_path=memory_path,
            anthropic_client=claude_client.client,
        )

        # Initialize desire system
        self.desire = DesireManager(storage_path=desire_path)

        # Initialize self system
        self.self_manager = SelfManager(
            storage_path=self_path,
            anthropic_client=claude_client.client,
        )

    async def initialize(self) -> None:
        """Initialize the bot by loading tools."""
        self.tools = await self.mcp.list_all_tools()
        print(f"Loaded {len(self.tools)} tools: {[t['name'] for t in self.tools]}")

    async def process_message(self, user_input: str) -> str:
        """Process a user message and return the response."""
        # Add user message to memory
        self.memory.add_message({
            "role": "user",
            "content": user_input,
        })

        return await self._get_response()

    def _clean_tool_messages(self, messages: list[dict]) -> list[dict]:
        """Remove orphaned tool_result messages that don't have matching tool_use."""
        if not messages:
            return messages

        # Collect all tool_use IDs from assistant messages
        tool_use_ids = set()
        for msg in messages:
            if msg.get("role") == "assistant":
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tool_use_ids.add(block.get("id"))

        # Filter out tool_result messages with orphaned IDs
        cleaned = []
        for msg in messages:
            content = msg.get("content", [])
            if isinstance(content, list):
                # Check if this is a tool_result message
                has_orphan = False
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        if block.get("tool_use_id") not in tool_use_ids:
                            has_orphan = True
                            break
                if has_orphan:
                    continue  # Skip this message
            cleaned.append(msg)

        return cleaned

    async def autonomous_action(self) -> str | None:
        """Perform an autonomous action based on desires."""
        if not self.tools:
            return None

        # Get the highest priority desire
        desire = self.desire.get_highest_priority_desire()
        if not desire:
            return None

        # Get a prompt for this desire
        prompt = self.desire.get_desire_prompt(desire.id)
        if not prompt:
            return None

        # Add as internal thought with desire info
        self.memory.add_message({
            "role": "user",
            "content": f"[内なる声・{desire.name}] {prompt}",
        })

        response = await self._get_response()

        # Mark desire as satisfied
        self.desire.satisfy_desire(desire.id)
        self.desire.save()

        # Self-reflection after action
        try:
            reflection = await self.self_manager.reflect_on_action(
                action=desire.name,
                outcome=response[:200] if response else "",
            )
            if reflection:
                print(f"  [ゆきの思い] {reflection}")
                # Record as successful action
                self.self_manager.record_action_evaluation(desire.name, success=True)
        except Exception:
            pass  # Don't fail the action if reflection fails

        return response

    async def _get_response(self) -> str:
        """Get response from Claude, handling tool calls."""
        # Get context with memory (includes compressed history + recent messages)
        context_messages = self.memory.get_context_messages()

        # Clean up orphaned tool_result messages
        context_messages = self._clean_tool_messages(context_messages)

        # Check if there are any tool_result in messages (can't prepend context then)
        has_tool_result = any(
            isinstance(msg.get("content"), list) and
            any(c.get("type") == "tool_result" for c in msg.get("content", []))
            for msg in context_messages
        )

        # Only add context prefix if no tool interactions are in progress
        if not has_tool_result:
            context_prefix = []

            # Add self/identity context
            self_context = self.self_manager.get_identity_context()
            if self_context:
                context_prefix.extend([
                    {"role": "user", "content": f"[ゆきの自我]\n{self_context}"},
                    {"role": "assistant", "content": "うん、ウチはウチやで！"},
                ])

            # Add long-term memory context if available
            memory_context = self.memory.get_memory_context()
            if memory_context:
                context_prefix.extend([
                    {"role": "user", "content": f"[長期記憶]\n{memory_context}"},
                    {"role": "assistant", "content": "覚えてるで！"},
                ])

            # Prepend context to messages
            if context_prefix:
                context_messages = context_prefix + context_messages

        response = self.claude.chat(
            messages=context_messages,
            tools=self.tools if self.tools else None,
        )

        # Process response
        final_texts = []
        assistant_content = []

        for content in response.content:
            if content.type == "text":
                final_texts.append(content.text)
                assistant_content.append({
                    "type": "text",
                    "text": content.text,
                })

            elif content.type == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": content.id,
                    "name": content.name,
                    "input": content.input,
                })

                # Execute tool
                print(f"  [Tool] {content.name}({content.input})")
                tool_result = await self.mcp.call_tool(content.name, content.input)

                # Add assistant message with tool use to memory
                self.memory.add_message({
                    "role": "assistant",
                    "content": assistant_content,
                })
                assistant_content = []

                # Add tool result to memory
                self.memory.add_message({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": content.id,
                        "content": tool_result,
                    }],
                })

                # Continue conversation to get final response
                follow_up = self.claude.chat(
                    messages=self.memory.get_context_messages(),
                    tools=self.tools if self.tools else None,
                )

                for fc in follow_up.content:
                    if fc.type == "text":
                        final_texts.append(fc.text)
                        assistant_content.append({
                            "type": "text",
                            "text": fc.text,
                        })

        # Add final assistant message to memory if not empty
        if assistant_content:
            self.memory.add_message({
                "role": "assistant",
                "content": assistant_content,
            })

        return "\n".join(final_texts)

    async def run_autonomous_loop(self) -> None:
        """Run autonomous behavior loop in background."""
        self.running = True
        while self.running:
            await asyncio.sleep(self.autonomous_interval)
            if self.running:
                try:
                    response = await self.autonomous_action()
                    if response:
                        print(f"\n[ゆき]\n{response}\n> ", end="", flush=True)
                except Exception as e:
                    print(f"\n[ゆき] エラー: {e}\n> ", end="", flush=True)

    def stop(self) -> None:
        """Stop the autonomous loop and save memories/desires."""
        self.running = False
        self._save_session_state()

    def _save_session_state(self) -> None:
        """Extract and save important memories and desire state from the session."""
        # Extract memories from recent conversation
        recent_messages = self.memory.short_term[-10:]  # Last 10 messages
        if recent_messages:
            self.memory.extract_memories_from_conversation(recent_messages)

        # Update global summary if we have enough memories
        if len(self.memory.long_term) >= 5:
            self.memory.update_global_summary()

        # Apply memory decay
        self.memory.decay_memories()

        # Save memories to file
        self.memory.save()

        # Save desire state to file
        self.desire.save()

        # Save self state to file
        self.self_manager.save()

        print("[System] セッションの記憶・欲求・自我状態を保存したで！")
