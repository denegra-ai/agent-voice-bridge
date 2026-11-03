import asyncio
import logging
import time
import uuid
from pathlib import Path

import httpx

from app.config import Settings, openai_audio_target

logger = logging.getLogger(__name__)

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
TTS_TIMEOUT_SECONDS = 60.0


async def synthesize(http: httpx.AsyncClient, settings: Settings, text: str) -> str:
    """Render `text` to an audio file in `settings.audio_dir` and return its file name.

    OpenAI and Azure OpenAI return Ogg/Opus, which WhatsApp plays as a voice note. ElevenLabs
    returns MP3, which WhatsApp shows as a regular audio attachment.
    """
    started = time.perf_counter()
    if settings.tts_provider == "elevenlabs":
        audio, extension = await _synthesize_elevenlabs(http, settings, text), "mp3"
    else:
        audio, extension = await _synthesize_openai(http, settings, text), "ogg"

    filename = f"{uuid.uuid4().hex}.{extension}"
    await asyncio.to_thread(_store, settings.audio_dir, filename, audio, settings.audio_ttl_seconds)
    logger.info(
        "Synthesized %d chars via %s in %.0f ms",
        len(text),
        settings.tts_provider,
        (time.perf_counter() - started) * 1000,
    )
    return filename


async def _synthesize_openai(http: httpx.AsyncClient, settings: Settings, text: str) -> bytes:
    url, headers, model = openai_audio_target(
        settings, "speech", azure=settings.tts_provider == "azure"
    )
    response = await http.post(
        url,
        headers=headers,
        json={
            "model": model,
            "voice": settings.openai_tts_voice,
            "input": text,
            "response_format": "opus",
        },
        timeout=TTS_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.content


async def _synthesize_elevenlabs(http: httpx.AsyncClient, settings: Settings, text: str) -> bytes:
    assert settings.elevenlabs_api_key is not None and settings.elevenlabs_voice_id is not None
    response = await http.post(
        ELEVENLABS_TTS_URL.format(voice_id=settings.elevenlabs_voice_id),
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": settings.elevenlabs_api_key.get_secret_value()},
        json={"text": text, "model_id": settings.elevenlabs_model_id},
        timeout=TTS_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.content


def _store(directory: Path, filename: str, audio: bytes, ttl_seconds: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - ttl_seconds
    for old in directory.iterdir():
        if old.is_file() and old.stat().st_mtime < cutoff:
            old.unlink(missing_ok=True)
    (directory / filename).write_bytes(audio)
