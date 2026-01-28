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

    def get_context_messages(self) -> list[dict[str, Any]]:
        """Get messages for Claude API context."""
        messages = []

        # Add compressed context as a system-like message if exists
        if self.compressed_context:
            messages.append({
                "role": "user",
                "content": f"[以前の会話の要約]\n{self.compressed_context}",
            })
            messages.append({
                "role": "assistant",
                "content": "うん、覚えてるで！続けよか。",
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
        to_compress = self.short_term[:split_point]
        self.short_term = self.short_term[split_point:]

        # Format messages for summarization
        conversation_text = self._format_messages(to_compress)

        # Ask Claude to summarize
        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",  # Use faster model for summarization
                max_tokens=500,
                messages=[{
                    "role": "user",
                    "content": f"""以下の会話を簡潔に要約してください。重要なポイントだけを残してください。

会話:
{conversation_text}

要約（3-5文で）:""",
                }],
            )
            new_summary = response.content[0].text

            # Merge with existing compressed context
            if self.compressed_context:
                self.compressed_context = f"{self.compressed_context}\n\n{new_summary}"
            else:
                self.compressed_context = new_summary

        except Exception as e:
            print(f"[Memory] Compression error: {e}")

    def _format_messages(self, messages: list[dict[str, Any]]) -> str:
        """Format messages for summarization."""
        lines = []
        for msg in messages:
            role = "こうちゃん" if msg["role"] == "user" else "ゆき"
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
            parts.append(f"【これまでの記憶】\n{self.global_summary}")

        # Add recent important memories
        recent_important = sorted(
            [m for m in self.long_term if m.importance >= 0.7],
            key=lambda m: m.timestamp,
            reverse=True,
        )[:5]

        if recent_important:
            memory_texts = [f"- {m.content}" for m in recent_important]
            parts.append("【最近の重要な記憶】\n" + "\n".join(memory_texts))

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
                    "content": f"""以下の会話から、覚えておくべき重要な情報を抽出してください。

会話:
{conversation_text}

以下のJSON形式で回答してください（重要な情報がなければ空配列を返してください）:
{{
  "memories": [
    {{
      "content": "覚えておくべき内容",
      "type": "episode/semantic/emotion のいずれか",
      "importance": 0.0から1.0の数値,
      "keywords": ["キーワード1", "キーワード2"]
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
                    "content": f"""以下の記憶を1-2文で要約してください。

記憶:
{chr(10).join(memory_texts)}

要約:""",
                }],
            )
            self.global_summary = response.content[0].text
            self.save()

        except Exception as e:
            print(f"[Memory] Summary error: {e}")
