import asyncio
import logging
import time

import httpx

from app.config import Settings, openai_audio_target

logger = logging.getLogger(__name__)

DEEPGRAM_LISTEN_URL = "https://api.deepgram.com/v1/listen"
STT_TIMEOUT_SECONDS = 60.0

# Twilio can answer 404 for a few seconds after the webhook, until the voice note is stored.
MEDIA_NOT_READY_DELAYS_SECONDS = (1.0, 2.0, 4.0, 8.0)

_EXTENSIONS = {
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "m4a",
    "audio/amr": "amr",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
}


async def download_media(
    http: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    auth: tuple[str, str] | None = None,
) -> bytes:
    delays = iter(MEDIA_NOT_READY_DELAYS_SECONDS)
    attempt = 0
    while True:
        response = await http.get(url, headers=headers, auth=auth, follow_redirects=True)
        delay = next(delays, None)
        if response.status_code != 404 or delay is None:
            response.raise_for_status()
            return response.content
        attempt += 1
        logger.warning("Media not ready (HTTP 404); retry %d in %.0fs", attempt, delay)
        await asyncio.sleep(delay)


async def transcribe(
    http: httpx.AsyncClient, settings: Settings, audio: bytes, mime_type: str
) -> str:
    started = time.perf_counter()
    if settings.stt_provider == "deepgram":
        text = await _transcribe_deepgram(http, settings, audio, mime_type)
    else:
        text = await _transcribe_openai(http, settings, audio, mime_type)
    logger.info(
        "Transcribed %d bytes via %s in %.0f ms",
        len(audio),
        settings.stt_provider,
        (time.perf_counter() - started) * 1000,
    )
    return text.strip()


async def _transcribe_openai(
    http: httpx.AsyncClient, settings: Settings, audio: bytes, mime_type: str
) -> str:
    url, headers, model = openai_audio_target(
        settings, "transcriptions", azure=settings.stt_provider == "azure"
    )
    base_mime = mime_type.split(";")[0].strip().lower()
    filename = f"audio.{_EXTENSIONS.get(base_mime, 'ogg')}"
    data = {"model": model}
    if settings.stt_language:
        data["language"] = settings.stt_language
    response = await http.post(
        url,
        headers=headers,
        data=data,
        files={"file": (filename, audio, base_mime)},
        timeout=STT_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return str(response.json().get("text", ""))


async def _transcribe_deepgram(
    http: httpx.AsyncClient, settings: Settings, audio: bytes, mime_type: str
) -> str:
    assert settings.deepgram_api_key is not None
    params = {"model": settings.deepgram_model, "smart_format": "true"}
    if settings.stt_language:
        params["language"] = settings.stt_language
    else:
        params["detect_language"] = "true"
    response = await http.post(
        DEEPGRAM_LISTEN_URL,
        params=params,
        headers={
            "Authorization": f"Token {settings.deepgram_api_key.get_secret_value()}",
            "Content-Type": mime_type.split(";")[0].strip(),
        },
        content=audio,
        timeout=STT_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    try:
        return str(response.json()["results"]["channels"][0]["alternatives"][0]["transcript"])
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Unexpected Deepgram response shape") from exc
