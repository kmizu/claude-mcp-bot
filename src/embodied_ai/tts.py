"""ElevenLabs text-to-speech integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class TTSConfigurationError(RuntimeError):
    """Raised when TTS is requested without required configuration."""


@dataclass
class ElevenLabsTTS:
    """Thin async wrapper around ElevenLabs text-to-speech API."""

    api_key: str
    default_voice_id: str
    default_model_id: str = "eleven_multilingual_v2"
    default_output_format: str = "mp3_44100_128"
    timeout_seconds: float = 30.0

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        model_id: str | None = None,
        output_format: str | None = None,
    ) -> tuple[bytes, str]:
        """Generate speech audio and return `(audio_bytes, mime_type)`."""
        if not text.strip():
            raise ValueError("Text for TTS must not be empty.")

        selected_voice = voice_id or self.default_voice_id
        if not selected_voice:
            raise TTSConfigurationError("ElevenLabs voice_id is not configured.")

        payload: dict[str, Any] = {
            "text": text,
            "model_id": model_id or self.default_model_id,
        }

        selected_output = output_format or self.default_output_format
        if selected_output:
            payload["output_format"] = selected_output

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{selected_voice}"
        headers = {
            "xi-api-key": self.api_key,
            "accept": "audio/mpeg",
            "content-type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()

        return response.content, "audio/mpeg"
