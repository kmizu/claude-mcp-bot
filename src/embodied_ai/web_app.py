"""FastAPI application for mobile-friendly chat + camera + TTS."""

from __future__ import annotations

import asyncio
import base64
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

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


WEB_DIR = Path(__file__).parent / "web"


class ChatRequest(BaseModel):
    """Incoming chat payload from the PWA frontend."""

    message: str = ""
    image_base64: str | None = None
    image_media_type: str = "image/jpeg"
    speak: bool = False
    voice_id: str | None = None
    model: str | None = None


class ChatResponse(BaseModel):
    """Response payload for chat requests."""

    reply: str
    audio_base64: str | None = None
    audio_mime_type: str | None = None
    tts_error: str | None = None


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

    async def shutdown(self) -> None:
        """Persist state and close external connections."""
        if self.bot:
            self.bot.stop()
        if self.mcp_client:
            await self.mcp_client.close()
        self.bot = None
        self.mcp_client = None
        self.claude_client = None

    async def run_autonomous_tick(
        self,
        speak: bool = True,
        voice_id: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Inject system-time message and let the bot react autonomously."""
        bot = self.bot
        if not bot:
            raise RuntimeError("Bot is not ready.")

        if model and not is_claude_model(model):
            raise InvalidModelSelectionError(
                f"Only Claude models are allowed, got: {model}"
            )

        # Keep short-term context bounded before adding a new autonomous prompt.
        self.compact_conversation_context()

        now = datetime.now()
        date_text = (
            f"{now.year}年{now.month}月{now.day}日"
            f"{now.hour:02d}:{now.minute:02d}:{now.second:02d}"
        )
        system_notice = (
            f"これはシステム時間です。{date_text}になりました。"
            "今この瞬間に合う行動を自律的に選んでください。"
            "必要ならカメラ系ツールや音声系ツールを使ってください。"
        )

        async with self.lock:
            reply = await bot.process_user_content(
                f"[System Tick] {system_notice}",
                model=model,
            )

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
            "created_at": now.isoformat(),
            "system_notice": system_notice,
            "reply": reply,
            "audio_base64": audio_base64,
            "audio_mime_type": audio_mime_type,
            "tts_error": tts_error,
        }
        self.autonomous_next_id += 1
        self.autonomous_events.append(event)
        self.autonomous_events = self.autonomous_events[-100:]
        return event

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


def _encode_audio(audio_bytes: bytes) -> str:
    """Base64-encode binary audio for JSON transport."""
    return base64.b64encode(audio_bytes).decode("ascii")


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
        return {
            "status": "ok" if bot else "initializing",
            "tools": [t["name"] for t in bot.tools] if bot else [],
            "tts_enabled": runtime.tts is not None,
            "default_model": runtime.claude_client.model if runtime.claude_client else None,
            "model_family": "claude",
            "autonomous_min_interval_seconds": runtime.autonomous_min_interval_seconds,
            "autonomous_compaction_threshold": runtime.autonomous_compaction_threshold,
            "autonomous_compaction_target_messages": runtime.autonomous_compaction_target_messages,
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
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid image data: {exc}") from exc

        try:
            async with runtime.lock:
                reply = await bot.process_user_content(user_content, model=payload.model)
        except InvalidModelSelectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Chat processing failed: {exc}",
            ) from exc

        if not payload.speak:
            return ChatResponse(reply=reply)

        if not runtime.tts:
            return ChatResponse(
                reply=reply,
                tts_error="TTS is not configured. Set ELEVENLABS_API_KEY and voice_id.",
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
            )
        except Exception as exc:
            return ChatResponse(reply=reply, tts_error=str(exc))

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
            )
        except InvalidModelSelectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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
