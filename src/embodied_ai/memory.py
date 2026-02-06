"""Memory system for the bot - short-term and long-term memory management."""

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from anthropic import Anthropic


@dataclass
class Memory:
    """A single memory unit."""
    id: str
    type: str  # "episode", "semantic", "emotion"
    content: str
    timestamp: str
    importance: float  # 0.0 - 1.0
    keywords: list[str] = field(default_factory=list)
    related_to: list[str] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        content: str,
        memory_type: str,
        importance: float,
        keywords: list[str] | None = None,
    ) -> "Memory":
        """Create a new memory with auto-generated ID and timestamp."""
        return cls(
            id=f"mem_{uuid.uuid4().hex[:8]}",
            type=memory_type,
            content=content,
            timestamp=datetime.now().isoformat(),
            importance=importance,
            keywords=keywords or [],
            related_to=[],
        )


class MemoryManager:
    """Manages both short-term and long-term memory."""

    def __init__(
        self,
        storage_path: str,
        anthropic_client: Anthropic | None = None,
        max_short_term_messages: int = 20,
        compression_threshold: int = 15,
    ):
        self.storage_path = Path(storage_path)
        self.client = anthropic_client or Anthropic()
        self.max_messages = max_short_term_messages
        self.compression_threshold = compression_threshold

        # Short-term memory (current conversation)
        self.short_term: list[dict[str, Any]] = []
        self.compressed_context: str = ""  # Summary of compressed messages

        # Long-term memory (persistent)
        self.long_term: list[Memory] = []
        self.global_summary: str = ""

        # Load existing memories
        self.load()

    # === Short-term Memory ===

    def add_message(self, message: dict[str, Any]) -> None:
        """Add a message to short-term memory."""
        self.short_term.append(message)

        # Check if compression is needed
        if len(self.short_term) >= self.compression_threshold:
            self._compress_old_messages()

    def compact_short_term_with_llm(
        self,
        target_messages: int = 20,
        max_rounds: int = 8,
    ) -> int:
        """Compact short-term memory by repeatedly summarizing with LLM."""
        target = max(6, target_messages)
        rounds = 0

        while len(self.short_term) > target and rounds < max_rounds:
            rounds += 1
            before = len(self.short_term)

            if len(self.short_term) >= self.compression_threshold:
                split_point = len(self.short_term) // 2
            else:
                split_point = max(2, len(self.short_term) - target)

            if split_point < 2:
                break

            self._compress_prefix_messages(split_point)

            if len(self.short_term) >= before:
                break

        return len(self.short_term)

    def get_context_messages(self) -> list[dict[str, Any]]:
        """Get messages for Claude API context."""
        messages = []

        # Add compressed context as a system-like message if exists
        if self.compressed_context:
            messages.append({
                "role": "user",
                "content": f"[Previous Conversation Summary]\n{self.compressed_context}",
            })
            messages.append({
                "role": "assistant",
                "content": "Yes, I remember! Let's continue.",
            })

        # Add recent messages
        messages.extend(self.short_term)

        return messages

    def _compress_old_messages(self) -> None:
        """Compress old messages into a summary."""
        if len(self.short_term) < self.compression_threshold:
            return

        # Take older half of messages to compress
        split_point = len(self.short_term) // 2
        self._compress_prefix_messages(split_point)

    def _compress_prefix_messages(self, split_point: int) -> None:
        """Compress the oldest `split_point` messages using LLM summarization."""
        if split_point < 2 or len(self.short_term) < 2:
            return

        to_compress = self.short_term[:split_point]
        remaining = self.short_term[split_point:]

        conversation_text = self._format_messages(to_compress)
        if not conversation_text.strip():
            # If no textual content is found, still trim old messages.
            self.short_term = remaining
            return

        new_summary = self._summarize_conversation(conversation_text)
        if not new_summary:
            return

        self.short_term = remaining
        if self.compressed_context:
            self.compressed_context = f"{self.compressed_context}\n\n{new_summary}"
        else:
            self.compressed_context = new_summary

    def _summarize_conversation(self, conversation_text: str) -> str:
        """Summarize conversation text with a Claude model."""
        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=500,
                messages=[{
                    "role": "user",
                    "content": f"""Summarize the following conversation concisely. Keep only the important points.

Conversation:
{conversation_text}

Summary (3-5 sentences):""",
                }],
            )
            return response.content[0].text
        except Exception as e:
            print(f"[Memory] Compression error: {e}")
            return ""

    def _format_messages(self, messages: list[dict[str, Any]]) -> str:
        """Format messages for summarization."""
        lines = []
        for msg in messages:
            role = "User" if msg["role"] == "user" else "Bot"
            content = msg.get("content", "")
            if isinstance(content, str):
                lines.append(f"{role}: {content}")
            elif isinstance(content, list):
                # Handle structured content
                text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
                if text_parts:
                    lines.append(f"{role}: {' '.join(text_parts)}")
        return "\n".join(lines)

    # === Long-term Memory ===

    def save_memory(self, memory: Memory) -> None:
        """Save a memory to long-term storage."""
        self.long_term.append(memory)
        self.save()

    def recall(self, query: str, limit: int = 5) -> list[Memory]:
        """Recall relevant memories based on query keywords."""
        if not self.long_term:
            return []

        # Simple keyword matching
        query_words = set(query.lower().split())

        scored_memories = []
        for memory in self.long_term:
            # Calculate relevance score
            memory_words = set(memory.content.lower().split())
            keyword_words = set(kw.lower() for kw in memory.keywords)

            # Score based on word overlap and keywords
            content_overlap = len(query_words & memory_words)
            keyword_overlap = len(query_words & keyword_words) * 2  # Keywords weighted more

            score = (content_overlap + keyword_overlap) * memory.importance
            if score > 0:
                scored_memories.append((score, memory))

        # Sort by score and return top results
        scored_memories.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored_memories[:limit]]

    def get_memory_context(self) -> str:
        """Get a summary of long-term memories for context."""
        if not self.long_term and not self.global_summary:
            return ""

        parts = []

        if self.global_summary:
            parts.append(f"[Memory Summary]\n{self.global_summary}")

        # Add recent important memories
        recent_important = sorted(
            [m for m in self.long_term if m.importance >= 0.7],
            key=lambda m: m.timestamp,
            reverse=True,
        )[:5]

        if recent_important:
            memory_texts = [f"- {m.content}" for m in recent_important]
            parts.append("[Recent Important Memories]\n" + "\n".join(memory_texts))

        return "\n\n".join(parts)

    def extract_memories_from_conversation(self, messages: list[dict[str, Any]]) -> None:
        """Extract important information from conversation and save as memories."""
        if not messages:
            return

        conversation_text = self._format_messages(messages)

        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1000,
                messages=[{
                    "role": "user",
                    "content": f"""Extract important information worth remembering from the following conversation.

Conversation:
{conversation_text}

Respond in the following JSON format (return empty array if no important information):
{{
  "memories": [
    {{
      "content": "Content worth remembering",
      "type": "episode/semantic/emotion",
      "importance": 0.0 to 1.0,
      "keywords": ["keyword1", "keyword2"]
    }}
  ]
}}

JSON:""",
                }],
            )

            # Parse response
            text = response.content[0].text
            # Try to extract JSON from the response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start != -1 and end > start:
                data = json.loads(text[start:end])
                for mem_data in data.get("memories", []):
                    memory = Memory.create(
                        content=mem_data["content"],
                        memory_type=mem_data.get("type", "semantic"),
                        importance=float(mem_data.get("importance", 0.5)),
                        keywords=mem_data.get("keywords", []),
                    )
                    self.save_memory(memory)

        except Exception as e:
            print(f"[Memory] Extraction error: {e}")

    def decay_memories(self, decay_rate: float = 0.01) -> None:
        """Reduce importance of old memories (forgetting)."""
        current_time = datetime.now()
        to_remove = []

        for memory in self.long_term:
            try:
                mem_time = datetime.fromisoformat(memory.timestamp)
                days_old = (current_time - mem_time).days

                # Decay importance based on age
                memory.importance = max(0.1, memory.importance - (decay_rate * days_old))

                # Mark for removal if importance is too low
                if memory.importance < 0.15:
                    to_remove.append(memory)
            except ValueError:
                pass

        # Remove forgotten memories
        for memory in to_remove:
            self.long_term.remove(memory)

        if to_remove:
            self.save()

    # === Persistence ===

    def load(self) -> None:
        """Load memories from file."""
        if not self.storage_path.exists():
            return

        try:
            with open(self.storage_path) as f:
                data = json.load(f)

            self.global_summary = data.get("summary", "")
            self.long_term = [
                Memory(**mem_data)
                for mem_data in data.get("memories", [])
            ]

        except Exception as e:
            print(f"[Memory] Load error: {e}")

    def save(self) -> None:
        """Save memories to file."""
        data = {
            "version": "1.0",
            "last_updated": datetime.now().isoformat(),
            "summary": self.global_summary,
            "memories": [asdict(m) for m in self.long_term],
        }

        try:
            with open(self.storage_path, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Memory] Save error: {e}")

    def update_global_summary(self) -> None:
        """Update the global summary of all memories."""
        if not self.long_term:
            return

        memory_texts = [f"- {m.content}" for m in self.long_term]

        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[{
                    "role": "user",
                    "content": f"""Summarize the following memories in 1-2 sentences.

Memories:
{chr(10).join(memory_texts)}

Summary:""",
                }],
            )
            self.global_summary = response.content[0].text
            self.save()

        except Exception as e:
            print(f"[Memory] Summary error: {e}")
