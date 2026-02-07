"""FastAPI application for mobile-friendly chat + camera + TTS."""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .bot import Bot
from .claude_client import ClaudeClient, InvalidModelSelectionError, is_claude_model
from .config_loader import load_config, resolve_bot_paths, resolve_system_prompt
from .mcp_client import MCPClient
from .tts import ElevenLabsTTS

try:
    import boto3
except Exception:  # pragma: no cover - optional in local dev
    boto3 = None


WEB_DIR = Path(__file__).parent / "web"
WEEKDAYS_JA = ("月", "火", "水", "木", "金", "土", "日")


class ChatRequest(BaseModel):
    """Incoming chat payload from the PWA frontend."""

    message: str = ""
    image_base64: str | None = None
    image_media_type: str = "image/jpeg"
    speak: bool = False
    voice_id: str | None = None
    model: str | None = None
    session_id: str | None = None
    client_datetime: str | None = None
    conversation_state: "ConversationStatePayload | None" = None


class ChatResponse(BaseModel):
    """Response payload for chat requests."""

    reply: str
    audio_base64: str | None = None
    audio_mime_type: str | None = None
    tts_error: str | None = None
    conversation_state: "ConversationStatePayload | None" = None


class SpeakRequest(BaseModel):
    """Incoming payload for direct TTS requests."""

    text: str = Field(min_length=1)
    voice_id: str | None = None


class SpeakResponse(BaseModel):
    """Audio payload returned by direct TTS endpoint."""

    audio_base64: str
    audio_mime_type: str


class AutonomousTickRequest(BaseModel):
    """Optional parameters for an autonomous tick."""

    speak: bool = True
    voice_id: str | None = None
    model: str | None = None
    timezone: str | None = None
    image_base64: str | None = None
    image_media_type: str = "image/jpeg"
    session_id: str | None = None
    conversation_state: "ConversationStatePayload | None" = None
    force: bool = False


class AutonomousTickResponse(BaseModel):
    """Response payload for autonomous tick."""

    id: int
    created_at: str
    system_notice: str
    reply: str
    audio_base64: str | None = None
    audio_mime_type: str | None = None
    tts_error: str | None = None
    timezone: str | None = None
    conversation_state: "ConversationStatePayload | None" = None


class ConversationStatePayload(BaseModel):
    """Portable conversation state passed between client and server."""

    short_term: list[dict[str, Any]] = Field(default_factory=list)
    compressed_context: str = ""


@dataclass
class ConversationState:
    """Per-session short-term conversation context."""

    short_term: list[dict[str, Any]] = field(default_factory=list)
    compressed_context: str = ""
    updated_at: float = 0.0


@dataclass
class DynamoDBConversationStore:
    """DynamoDB-backed per-session conversation storage."""

    table_name: str
    region_name: str | None = None
    ttl_days: int = 14
    max_state_bytes: int = 140000
    _table: Any = field(default=None, init=False, repr=False)

    def _get_table(self) -> Any:
        """Lazily create a DynamoDB table client."""
        if self._table is not None:
            return self._table
        if boto3 is None:
            raise RuntimeError("boto3 is not available; cannot use DynamoDB store.")
        dynamodb = boto3.resource("dynamodb", region_name=self.region_name)
        self._table = dynamodb.Table(self.table_name)
        return self._table

    def describe(self) -> dict[str, Any]:
        """Return public metadata for health/debug output."""
        return {
            "backend": "dynamodb",
            "table_name": self.table_name,
            "ttl_days": self.ttl_days,
            "max_state_bytes": self.max_state_bytes,
            "region": self.region_name or "",
        }

    def load_state(self, session_id: str) -> ConversationState | None:
        """Load one session state from DynamoDB."""
        table = self._get_table()
        response = table.get_item(Key={"session_id": session_id})
        item = response.get("Item")
        if not item:
            return None

        state_json = item.get("state_json")
        if not state_json:
            return None

        data = json.loads(state_json)
        payload = ConversationStatePayload.model_validate(data)
        return ConversationState(
            short_term=payload.short_term,
            compressed_context=payload.compressed_context,
            updated_at=time.monotonic(),
        )

    def save_state(self, session_id: str, state: ConversationState) -> None:
        """Persist one session state into DynamoDB."""
        table = self._get_table()
        payload = {
            "short_term": state.short_term,
            "compressed_context": state.compressed_context,
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        size_bytes = len(encoded.encode("utf-8"))
        if size_bytes > self.max_state_bytes:
            raise ValueError(
                "Conversation state is too large for configured max_state_bytes "
                f"({size_bytes} > {self.max_state_bytes})."
            )

        now_epoch = int(time.time())
        expires_at = now_epoch + max(self.ttl_days, 1) * 24 * 60 * 60
        table.put_item(
            Item={
                "session_id": session_id,
                "state_json": encoded,
                "updated_at": now_epoch,
                "expires_at": expires_at,
            }
        )


@dataclass
class RuntimeState:
    """Shared runtime resources for the web API."""

    config_path: str | None
    config: dict[str, Any] | None = None
    bot: Bot | None = None
    mcp_client: MCPClient | None = None
    tts: ElevenLabsTTS | None = None
    claude_client: ClaudeClient | None = None
    autonomous_events: list[dict[str, Any]] = field(default_factory=list)
    autonomous_next_id: int = 1
    autonomous_min_interval_seconds: float = 3.0
    autonomous_compaction_threshold: int = 80
    autonomous_compaction_target_messages: int = 40
    autonomous_default_timezone: str = "Asia/Tokyo"
    default_session_id: str = "default"
    max_conversation_states: int = 64
    max_conversation_state_bytes: int = 140000
    conversation_store: DynamoDBConversationStore | None = None
    conversation_states: dict[str, ConversationState] = field(default_factory=dict)
    autonomous_last_tick_monotonic: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def startup(self) -> None:
        """Initialize bot runtime and optional TTS."""
        load_dotenv()

        config, config_path = load_config(self.config_path)
        self.config = config
        system_prompt = resolve_system_prompt(config, config_path)

        mcp_client = MCPClient()
        claude_config = config.get("claude", {})
        claude_client = ClaudeClient(
            model=claude_config.get("model", "claude-sonnet-4-20250514"),
            system_prompt=system_prompt,
        )

        await mcp_client.connect(config.get("mcpServers", {}))
        memory_path, desire_path, self_path = resolve_bot_paths(config, config_path)

        bot = Bot(
            mcp_client=mcp_client,
            claude_client=claude_client,
            memory_path=memory_path,
            desire_path=desire_path,
            self_path=self_path,
            autonomous_interval=config.get("bot", {}).get("autonomous_interval", 30.0),
        )
        await bot.initialize()

        self.mcp_client = mcp_client
        self.bot = bot
        self.tts = self._build_tts(config)
        self.claude_client = claude_client
        self.autonomous_min_interval_seconds = float(
            config.get("web", {}).get("autonomous_min_interval_seconds", 3.0)
        )
        self.autonomous_compaction_threshold = int(
            config.get("web", {}).get("autonomous_compaction_threshold", 80)
        )
        self.autonomous_compaction_target_messages = int(
            config.get("web", {}).get("autonomous_compaction_target_messages", 40)
        )
        self.autonomous_default_timezone = str(
            config.get("web", {}).get(
                "autonomous_timezone",
                os.getenv("EMBODIED_AI_TIMEZONE", "Asia/Tokyo"),
            )
        )
        self.max_conversation_state_bytes = int(
            config.get("web", {}).get(
                "max_conversation_state_bytes",
                os.getenv("EMBODIED_AI_MAX_CONVERSATION_STATE_BYTES", 140000),
            )
        )
        self.max_conversation_states = int(
            config.get("web", {}).get("max_conversation_states", 64)
        )
        self.conversation_store = self._build_conversation_store(config)

    async def shutdown(self) -> None:
        """Persist state and close external connections."""
        if self.mcp_client:
            await self.mcp_client.close()
        self.bot = None
        self.mcp_client = None
        self.claude_client = None
        self.conversation_store = None

    async def run_autonomous_tick(
        self,
        speak: bool = True,
        voice_id: str | None = None,
        model: str | None = None,
        timezone_name: str | None = None,
        image_base64: str | None = None,
        image_media_type: str = "image/jpeg",
        session_id: str | None = None,
        conversation_state: ConversationStatePayload | None = None,
    ) -> dict[str, Any]:
        """Inject system-time message and let the bot react autonomously."""
        bot = self.bot
        if not bot:
            raise RuntimeError("Bot is not ready.")

        if model and not is_claude_model(model):
            raise InvalidModelSelectionError(
                f"Only Claude models are allowed, got: {model}"
            )

        tz_name = timezone_name or self.autonomous_default_timezone
        tz = self._resolve_timezone(tz_name)
        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone(tz)
        date_text = _format_japanese_datetime(now_local)
        system_notice = (
            f"これはシステム時間です。{{{date_text}}}になりました。"
            "今この瞬間に合う行動を自律的に選んでください。"
            "必要ならカメラ系ツールや音声系ツールを使ってください。"
        )
        autonomous_content = _build_autonomous_tick_content(
            system_notice=system_notice,
            image_base64=image_base64,
            image_media_type=image_media_type,
        )

        resolved_session = self._normalize_session_id(session_id)
        async with self.lock:
            await self._apply_conversation_state(
                session_id=resolved_session,
                payload_state=conversation_state,
            )
            # Keep short-term context bounded before adding a new autonomous prompt.
            self.compact_conversation_context()
            reply = await bot.process_user_content(
                autonomous_content,
                model=model,
            )
            self._snapshot_conversation_state(resolved_session)
            state_payload = self._get_session_state_payload(resolved_session)
            await self._persist_session_state(resolved_session)

        audio_base64 = None
        audio_mime_type = None
        tts_error = None
        if speak:
            if self.tts:
                try:
                    audio_bytes, mime_type = await self.tts.synthesize(
                        text=reply,
                        voice_id=voice_id,
                    )
                    audio_base64 = _encode_audio(audio_bytes)
                    audio_mime_type = mime_type
                except Exception as exc:
                    tts_error = str(exc)
            else:
                tts_error = "TTS is not configured. Set ELEVENLABS_API_KEY and voice_id."

        event = {
            "id": self.autonomous_next_id,
            "created_at": now_utc.isoformat(),
            "system_notice": system_notice,
            "reply": reply,
            "audio_base64": audio_base64,
            "audio_mime_type": audio_mime_type,
            "tts_error": tts_error,
            "timezone": str(now_local.tzinfo),
            "conversation_state": (
                state_payload.model_dump() if state_payload else None
            ),
        }
        self.autonomous_next_id += 1
        self.autonomous_events.append(event)
        self.autonomous_events = self.autonomous_events[-100:]
        return event

    def _normalize_session_id(self, session_id: str | None) -> str:
        """Normalize inbound session IDs and keep key size bounded."""
        if not session_id:
            return self.default_session_id
        normalized = session_id.strip()
        if not normalized:
            return self.default_session_id
        return normalized[:120]

    def _restore_conversation_state(self, session_id: str) -> bool:
        """Load per-session short-term context into the shared bot instance."""
        bot = self.bot
        if not bot:
            return False

        state = self.conversation_states.get(session_id)
        if not state:
            bot.memory.short_term = []
            bot.memory.compressed_context = ""
            return False

        bot.memory.short_term = copy.deepcopy(state.short_term)
        bot.memory.compressed_context = state.compressed_context
        state.updated_at = time.monotonic()
        return True

    async def _apply_conversation_state(
        self,
        session_id: str,
        payload_state: ConversationStatePayload | None,
    ) -> None:
        """Load conversation state from request payload or fallback cache."""
        if payload_state:
            bot = self.bot
            if not bot:
                return

            state = self._normalize_conversation_state(
                short_term=payload_state.short_term,
                compressed_context=payload_state.compressed_context,
            )
            bot.memory.short_term = copy.deepcopy(state.short_term)
            bot.memory.compressed_context = state.compressed_context
            self.conversation_states[session_id] = state
            self._prune_conversation_states()
            return

        if self._restore_conversation_state(session_id):
            return

        state = await self._load_session_state_from_store(session_id)
        if not state:
            return

        self.conversation_states[session_id] = state
        self._prune_conversation_states()
        self._restore_conversation_state(session_id)

    def _snapshot_conversation_state(self, session_id: str) -> None:
        """Persist current bot short-term context into the per-session map."""
        bot = self.bot
        if not bot:
            return

        memory = bot.memory
        self.conversation_states[session_id] = self._normalize_conversation_state(
            short_term=memory.short_term,
            compressed_context=memory.compressed_context,
        )
        self._prune_conversation_states()

    def _get_session_state_payload(
        self,
        session_id: str,
    ) -> ConversationStatePayload | None:
        """Return normalized per-session context for client-side persistence."""
        state = self.conversation_states.get(session_id)
        if not state:
            return None
        return ConversationStatePayload(
            short_term=copy.deepcopy(state.short_term),
            compressed_context=state.compressed_context,
        )

    async def _load_session_state_from_store(self, session_id: str) -> ConversationState | None:
        """Load one session from the configured external store."""
        store = self.conversation_store
        if not store:
            return None

        try:
            state = await asyncio.to_thread(store.load_state, session_id)
            if not state:
                return None
            return self._normalize_conversation_state(
                short_term=state.short_term,
                compressed_context=state.compressed_context,
            )
        except Exception as exc:
            print(f"[Web] Conversation store load failed for {session_id}: {exc}")
            return None

    async def _persist_session_state(self, session_id: str) -> None:
        """Persist one session into the configured external store."""
        store = self.conversation_store
        if not store:
            return

        state = self.conversation_states.get(session_id)
        if not state:
            return

        try:
            await asyncio.to_thread(store.save_state, session_id, state)
        except Exception as exc:
            print(f"[Web] Conversation store save failed for {session_id}: {exc}")

    def _normalize_conversation_state(
        self,
        short_term: list[dict[str, Any]],
        compressed_context: str,
    ) -> ConversationState:
        """Sanitize and size-bound conversation state."""
        normalized_short_term = self._sanitize_short_term_messages(short_term)
        normalized_compressed = self._truncate_text(
            compressed_context,
            max_chars=24000,
        )
        bounded_short, bounded_compressed = self._fit_state_size_limit(
            normalized_short_term,
            normalized_compressed,
        )
        return ConversationState(
            short_term=copy.deepcopy(bounded_short),
            compressed_context=bounded_compressed,
            updated_at=time.monotonic(),
        )

    def _fit_state_size_limit(
        self,
        short_term: list[dict[str, Any]],
        compressed_context: str,
    ) -> tuple[list[dict[str, Any]], str]:
        """Ensure serialized state stays under size limit."""
        max_bytes = max(self.max_conversation_state_bytes, 20000)
        working_short = copy.deepcopy(short_term)
        working_compressed = compressed_context

        while (
            self._estimate_state_size_bytes(working_short, working_compressed) > max_bytes
            and len(working_short) > 8
        ):
            # Drop oldest chunk first to preserve recent context.
            drop = max(1, len(working_short) // 8)
            working_short = working_short[drop:]

        if self._estimate_state_size_bytes(working_short, working_compressed) > max_bytes:
            working_compressed = self._truncate_text(working_compressed, max_chars=8000)

        while (
            self._estimate_state_size_bytes(working_short, working_compressed) > max_bytes
            and len(working_short) > 2
        ):
            working_short = working_short[1:]

        if self._estimate_state_size_bytes(working_short, working_compressed) > max_bytes:
            working_short = working_short[-8:]
            working_compressed = self._truncate_text(working_compressed, max_chars=2000)

        return working_short, working_compressed

    def _estimate_state_size_bytes(
        self,
        short_term: list[dict[str, Any]],
        compressed_context: str,
    ) -> int:
        """Estimate UTF-8 byte size for serialized conversation state."""
        payload = {
            "short_term": short_term,
            "compressed_context": compressed_context,
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return len(encoded.encode("utf-8"))

    def _sanitize_short_term_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Normalize short-term messages for safe persistence and transport."""
        normalized: list[dict[str, Any]] = []
        for message in messages[-120:]:
            clean = self._sanitize_message(message)
            if clean:
                normalized.append(clean)
        return normalized

    def _sanitize_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Sanitize one message while preserving Claude-compatible structure."""
        role = str(message.get("role", "")).strip()
        if role not in {"user", "assistant"}:
            return None

        content = message.get("content", "")
        if isinstance(content, str):
            return {
                "role": role,
                "content": self._truncate_text(content, max_chars=1800),
            }

        if isinstance(content, list):
            blocks: list[dict[str, Any]] = []
            for block in content[:16]:
                clean_block = self._sanitize_content_block(block)
                if clean_block:
                    blocks.append(clean_block)
            if not blocks:
                return {
                    "role": role,
                    "content": "",
                }
            return {
                "role": role,
                "content": blocks,
            }

        return {
            "role": role,
            "content": self._truncate_text(content, max_chars=1200),
        }

    def _sanitize_content_block(self, block: Any) -> dict[str, Any] | None:
        """Normalize rich content blocks and remove heavy binary payloads."""
        if not isinstance(block, dict):
            return None

        block_type = str(block.get("type", "")).strip()
        if not block_type:
            return None

        if block_type == "text":
            return {
                "type": "text",
                "text": self._truncate_text(block.get("text", ""), max_chars=1800),
            }

        if block_type == "image":
            return {
                "type": "text",
                "text": "[Image attached by user]",
            }

        if block_type == "tool_use":
            return {
                "type": "tool_use",
                "id": self._truncate_text(block.get("id", ""), max_chars=120),
                "name": self._truncate_text(block.get("name", ""), max_chars=120),
                "input": self._sanitize_json_value(block.get("input")),
            }

        if block_type == "tool_result":
            content = block.get("content", "")
            if isinstance(content, str):
                content_text = content
            else:
                content_text = json.dumps(
                    self._sanitize_json_value(content),
                    ensure_ascii=False,
                )
            return {
                "type": "tool_result",
                "tool_use_id": self._truncate_text(
                    block.get("tool_use_id", ""),
                    max_chars=120,
                ),
                "content": self._truncate_text(content_text, max_chars=1800),
            }

        return None

    def _sanitize_json_value(self, value: Any, depth: int = 0) -> Any:
        """Recursively sanitize JSON-like values for compact transport."""
        if depth > 4:
            return self._truncate_text(str(value), max_chars=300)

        if isinstance(value, (type(None), bool, int, float)):
            return value

        if isinstance(value, str):
            return self._truncate_text(value, max_chars=600)

        if isinstance(value, list):
            return [self._sanitize_json_value(v, depth + 1) for v in value[:16]]

        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in list(value.items())[:16]:
                result[self._truncate_text(str(key), max_chars=120)] = (
                    self._sanitize_json_value(item, depth + 1)
                )
            return result

        return self._truncate_text(str(value), max_chars=300)

    def _truncate_text(self, value: Any, max_chars: int) -> str:
        """Safely coerce to string and truncate."""
        text = value if isinstance(value, str) else str(value)
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars]}...[truncated]"

    def _prune_conversation_states(self) -> None:
        """Drop oldest sessions when in-memory session cache grows too large."""
        max_sessions = max(self.max_conversation_states, 1)
        if len(self.conversation_states) <= max_sessions:
            return

        oldest = sorted(
            self.conversation_states.items(),
            key=lambda item: item[1].updated_at,
        )[: len(self.conversation_states) - max_sessions]
        for session_id, _ in oldest:
            self.conversation_states.pop(session_id, None)

    def _resolve_timezone(self, timezone_name: str) -> ZoneInfo:
        """Resolve IANA timezone name with fallback to UTC."""
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            try:
                return ZoneInfo(self.autonomous_default_timezone)
            except ZoneInfoNotFoundError:
                return ZoneInfo("UTC")

    def compact_conversation_context(self) -> None:
        """Aggressively compact short-term memory during autonomous operation."""
        bot = self.bot
        if not bot:
            return

        memory = bot.memory
        threshold = max(self.autonomous_compaction_threshold, 10)
        target = max(10, min(self.autonomous_compaction_target_messages, threshold))

        if len(memory.short_term) <= threshold:
            return

        memory.compact_short_term_with_llm(
            target_messages=target,
            max_rounds=8,
        )

    def _build_conversation_store(
        self,
        config: dict[str, Any],
    ) -> DynamoDBConversationStore | None:
        """Create optional external conversation store."""
        web_config = config.get("web", {})
        store_config = web_config.get("conversation_store", {})

        backend = str(
            store_config.get(
                "backend",
                os.getenv(
                    "EMBODIED_AI_CONVERSATION_STORE",
                    "dynamodb" if os.getenv("EMBODIED_AI_SESSION_TABLE") else "memory",
                ),
            )
        ).lower()
        if backend not in {"dynamodb", "ddb"}:
            return None

        table_name = (
            store_config.get("table_name")
            or os.getenv("EMBODIED_AI_SESSION_TABLE")
            or ""
        ).strip()
        if not table_name:
            print("[Web] Conversation store backend=dynamodb but table_name is missing.")
            return None

        region = (
            store_config.get("region")
            or os.getenv("AWS_REGION")
            or os.getenv("AWS_DEFAULT_REGION")
            or None
        )
        ttl_days = int(
            store_config.get(
                "ttl_days",
                os.getenv("EMBODIED_AI_SESSION_TTL_DAYS", 14),
            )
        )

        if boto3 is None:
            print("[Web] boto3 unavailable; DynamoDB conversation store disabled.")
            return None

        store = DynamoDBConversationStore(
            table_name=table_name,
            region_name=region,
            ttl_days=ttl_days,
            max_state_bytes=self.max_conversation_state_bytes,
        )
        print(
            "[Web] Conversation store enabled: "
            f"dynamodb table={table_name} region={region or 'default'} ttl_days={ttl_days}"
        )
        return store

    def _build_tts(self, config: dict[str, Any]) -> ElevenLabsTTS | None:
        """Create ElevenLabs client when configuration is available."""
        tts_config = config.get("tts", {})
        if tts_config.get("enabled", True) is False:
            return None

        api_key = tts_config.get("api_key") or os.getenv("ELEVENLABS_API_KEY")
        if not api_key:
            return None

        voice_id = tts_config.get("voice_id") or os.getenv("ELEVENLABS_VOICE_ID", "")
        if not voice_id:
            return None

        return ElevenLabsTTS(
            api_key=api_key,
            default_voice_id=voice_id,
            default_model_id=tts_config.get("model_id", "eleven_multilingual_v2"),
            default_output_format=tts_config.get("output_format", "mp3_44100_128"),
            timeout_seconds=float(tts_config.get("timeout_seconds", 30.0)),
        )


def _normalize_image_data(image_base64: str) -> str:
    """Remove data URL prefix and validate base64 payload."""
    if "," in image_base64 and image_base64.startswith("data:"):
        image_base64 = image_base64.split(",", 1)[1]

    # Validate without keeping decoded bytes in memory.
    base64.b64decode(image_base64, validate=True)
    return image_base64


def _build_user_content(payload: ChatRequest) -> str | list[dict[str, Any]]:
    """Create Claude-compatible user content from chat payload."""
    has_text = bool(payload.message.strip())
    has_image = bool(payload.image_base64)

    if not has_image:
        return payload.message

    image_data = _normalize_image_data(payload.image_base64 or "")
    blocks: list[dict[str, Any]] = []

    if has_text:
        blocks.append({"type": "text", "text": payload.message})
    else:
        blocks.append({
            "type": "text",
            "text": "What can you see in this image?",
        })

    blocks.append({
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": payload.image_media_type,
            "data": image_data,
        },
    })
    return blocks


def _build_autonomous_tick_content(
    system_notice: str,
    image_base64: str | None = None,
    image_media_type: str = "image/jpeg",
) -> str | list[dict[str, Any]]:
    """Build autonomous tick content; include camera frame when provided."""
    text = (
        f"[System Tick] {system_notice}"
        "\n\n必要に応じて、添付画像（ある場合）の状況も見て判断してください。"
    )
    if not image_base64:
        return text

    image_data = _normalize_image_data(image_base64)
    return [
        {"type": "text", "text": text},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image_media_type,
                "data": image_data,
            },
        },
    ]


def _encode_audio(audio_bytes: bytes) -> str:
    """Base64-encode binary audio for JSON transport."""
    return base64.b64encode(audio_bytes).decode("ascii")


def _format_japanese_datetime(dt: datetime) -> str:
    """Format datetime like 2026年2月7日（土）8時30分."""
    weekday = WEEKDAYS_JA[dt.weekday()]
    return f"{dt.year}年{dt.month}月{dt.day}日（{weekday}）{dt.hour}時{dt.minute:02d}分"


def _attach_client_datetime_note(
    content: str | list[dict[str, Any]],
    client_datetime: str | None,
) -> str | list[dict[str, Any]]:
    """Append trusted environment-time note from client to user content."""
    if not client_datetime:
        return content

    datetime_text = client_datetime.strip()
    if not datetime_text:
        return content

    note = f"{{これは環境側の時刻です。{datetime_text}}}"

    if isinstance(content, str):
        if content.strip():
            return f"{content}\n\n{note}"
        return note

    if isinstance(content, list):
        blocks = copy.deepcopy(content)
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                text = str(block.get("text", "")).strip()
                block["text"] = f"{text}\n\n{note}" if text else note
                return blocks
        blocks.insert(0, {"type": "text", "text": note})
        return blocks

    return content


def create_app() -> FastAPI:
    """Application factory used by uvicorn."""
    config_path = os.getenv("EMBODIED_AI_CONFIG")
    runtime = RuntimeState(config_path=config_path)

    app = FastAPI(title="Embodied AI Web API", version="0.2.0")
    app.state.runtime = runtime

    @app.on_event("startup")
    async def _startup() -> None:
        await runtime.startup()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await runtime.shutdown()

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        bot = runtime.bot
        store = runtime.conversation_store
        return {
            "status": "ok" if bot else "initializing",
            "tools": [t["name"] for t in bot.tools] if bot else [],
            "tts_enabled": runtime.tts is not None,
            "default_model": runtime.claude_client.model if runtime.claude_client else None,
            "model_family": "claude",
            "autonomous_min_interval_seconds": runtime.autonomous_min_interval_seconds,
            "autonomous_compaction_threshold": runtime.autonomous_compaction_threshold,
            "autonomous_compaction_target_messages": runtime.autonomous_compaction_target_messages,
            "autonomous_default_timezone": runtime.autonomous_default_timezone,
            "active_sessions": len(runtime.conversation_states),
            "conversation_store": store.describe() if store else {"backend": "memory"},
        }

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        claude_client = runtime.claude_client
        if not claude_client:
            raise HTTPException(status_code=503, detail="Bot is not ready yet.")

        try:
            return {
                "models": claude_client.list_claude_models(),
                "default_model": claude_client.model,
            }
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to fetch model list from Anthropic: {exc}",
            ) from exc

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(payload: ChatRequest) -> ChatResponse:
        bot = runtime.bot
        if not bot:
            raise HTTPException(status_code=503, detail="Bot is not ready yet.")

        if not payload.message.strip() and not payload.image_base64:
            raise HTTPException(status_code=400, detail="Message or image is required.")

        if payload.model and not is_claude_model(payload.model):
            raise HTTPException(
                status_code=400,
                detail="Only Claude models are allowed.",
            )

        try:
            user_content = _build_user_content(payload)
            user_content = _attach_client_datetime_note(
                content=user_content,
                client_datetime=payload.client_datetime,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid image data: {exc}") from exc

        try:
            session_id = runtime._normalize_session_id(payload.session_id)
            async with runtime.lock:
                await runtime._apply_conversation_state(
                    session_id=session_id,
                    payload_state=payload.conversation_state,
                )
                reply = await bot.process_user_content(user_content, model=payload.model)
                runtime._snapshot_conversation_state(session_id)
                state_payload = runtime._get_session_state_payload(session_id)
                await runtime._persist_session_state(session_id)
        except InvalidModelSelectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Chat processing failed: {exc}",
            ) from exc

        if not payload.speak:
            return ChatResponse(
                reply=reply,
                conversation_state=state_payload,
            )

        if not runtime.tts:
            return ChatResponse(
                reply=reply,
                tts_error="TTS is not configured. Set ELEVENLABS_API_KEY and voice_id.",
                conversation_state=state_payload,
            )

        try:
            audio_bytes, mime_type = await runtime.tts.synthesize(
                text=reply,
                voice_id=payload.voice_id,
            )
            return ChatResponse(
                reply=reply,
                audio_base64=_encode_audio(audio_bytes),
                audio_mime_type=mime_type,
                conversation_state=state_payload,
            )
        except Exception as exc:
            return ChatResponse(
                reply=reply,
                tts_error=str(exc),
                conversation_state=state_payload,
            )

    @app.post("/api/autonomous/tick", response_model=AutonomousTickResponse)
    async def autonomous_tick(payload: AutonomousTickRequest) -> AutonomousTickResponse:
        if not runtime.bot:
            raise HTTPException(status_code=503, detail="Bot is not ready yet.")

        now_monotonic = time.monotonic()
        elapsed = now_monotonic - runtime.autonomous_last_tick_monotonic
        if (
            not payload.force
            and runtime.autonomous_last_tick_monotonic > 0
            and elapsed < runtime.autonomous_min_interval_seconds
        ):
            raise HTTPException(
                status_code=429,
                detail=(
                    "Autonomous tick is too frequent. "
                    f"Wait at least {runtime.autonomous_min_interval_seconds:.1f}s."
                ),
            )

        try:
            event = await runtime.run_autonomous_tick(
                speak=payload.speak,
                voice_id=payload.voice_id,
                model=payload.model,
                timezone_name=payload.timezone,
                image_base64=payload.image_base64,
                image_media_type=payload.image_media_type,
                session_id=payload.session_id,
                conversation_state=payload.conversation_state,
            )
        except InvalidModelSelectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid image data: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Autonomous tick failed: {exc}") from exc

        runtime.autonomous_last_tick_monotonic = time.monotonic()
        return AutonomousTickResponse(**event)

    @app.get("/api/autonomous/events")
    async def autonomous_events(after_id: int = 0) -> dict[str, Any]:
        events = [e for e in runtime.autonomous_events if e["id"] > after_id]
        latest_id = runtime.autonomous_events[-1]["id"] if runtime.autonomous_events else after_id
        return {
            "events": events,
            "latest_id": latest_id,
        }

    @app.post("/api/speak", response_model=SpeakResponse)
    async def speak(payload: SpeakRequest) -> SpeakResponse:
        if not runtime.tts:
            raise HTTPException(
                status_code=503,
                detail="TTS is not configured. Set ELEVENLABS_API_KEY and voice_id.",
            )

        audio_bytes, mime_type = await runtime.tts.synthesize(
            text=payload.text,
            voice_id=payload.voice_id,
        )
        return SpeakResponse(
            audio_base64=_encode_audio(audio_bytes),
            audio_mime_type=mime_type,
        )

    if (WEB_DIR / "assets").exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/manifest.webmanifest")
    async def manifest() -> FileResponse:
        return FileResponse(WEB_DIR / "manifest.webmanifest")

    @app.get("/sw.js")
    async def service_worker() -> FileResponse:
        return FileResponse(WEB_DIR / "sw.js")

    return app
