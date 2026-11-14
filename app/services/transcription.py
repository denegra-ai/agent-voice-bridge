import asyncio
import logging
import time

import httpx

from app.config import Settings, SttProvider, openai_audio_target

logger = logging.getLogger(__name__)

DEEPGRAM_LISTEN_URL = "https://api.deepgram.com/v1/listen"
ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
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
    """Transcribe with STT_PROVIDER; on an error or an empty transcript, try STT_FALLBACK_PROVIDER.

    An empty transcript is treated like a failure because a wrong language setting can silently
    return nothing (bench/stt: the bridge's old Deepgram setting returned "" for most Arabic clips).
    """
    fallback = settings.stt_fallback_provider
    try:
        text = await _transcribe_once(http, settings, settings.stt_provider, audio, mime_type)
    except (httpx.HTTPError, ValueError) as exc:
        if fallback is None:
            raise
        # A 4xx other than 408/429 means the primary is misconfigured (key, model, deployment);
        # the fallback keeps users answered, so make sure the broken primary still gets noticed.
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        level = (
            logging.ERROR
            if status and 400 <= status < 500 and status not in (408, 429)
            else logging.WARNING
        )
        logger.log(
            level,
            "Transcription via %s failed; trying %s",
            settings.stt_provider,
            fallback,
            exc_info=True,
        )
        return await _transcribe_once(http, settings, fallback, audio, mime_type)
    if text or fallback is None:
        return text
    logger.warning("Empty transcript from %s; trying %s", settings.stt_provider, fallback)
    return await _transcribe_once(http, settings, fallback, audio, mime_type)


async def _transcribe_once(
    http: httpx.AsyncClient,
    settings: Settings,
    provider: SttProvider,
    audio: bytes,
    mime_type: str,
) -> str:
    started = time.perf_counter()
    if provider == "deepgram":
        text = await _transcribe_deepgram(http, settings, audio, mime_type)
    elif provider == "elevenlabs":
        text = await _transcribe_elevenlabs(http, settings, audio, mime_type)
    else:
        text = await _transcribe_openai(http, settings, audio, mime_type, azure=provider == "azure")
    logger.info(
        "Transcribed %d bytes via %s in %.0f ms",
        len(audio),
        provider,
        (time.perf_counter() - started) * 1000,
    )
    return text.strip()


async def _transcribe_openai(
    http: httpx.AsyncClient, settings: Settings, audio: bytes, mime_type: str, *, azure: bool
) -> str:
    url, headers, model = openai_audio_target(settings, "transcriptions", azure=azure)
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
    # "multi" is Deepgram's language-agnostic mode; detect_language=true returned empty
    # transcripts for most Arabic clips in bench/stt.
    params["language"] = settings.stt_language or "multi"
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


async def _transcribe_elevenlabs(
    http: httpx.AsyncClient, settings: Settings, audio: bytes, mime_type: str
) -> str:
    assert settings.elevenlabs_api_key is not None
    base_mime = mime_type.split(";")[0].strip().lower()
    # Without this Scribe adds markers like "(laughter)" that the agent would read as speech.
    data = {"model_id": settings.elevenlabs_stt_model, "tag_audio_events": "false"}
    if settings.stt_language:
        data["language_code"] = settings.stt_language
    response = await http.post(
        ELEVENLABS_STT_URL,
        headers={"xi-api-key": settings.elevenlabs_api_key.get_secret_value()},
        data=data,
        files={"file": (f"audio.{_EXTENSIONS.get(base_mime, 'ogg')}", audio, base_mime)},
        timeout=STT_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    try:
        return str(response.json()["text"])
    except (KeyError, TypeError) as exc:
        raise ValueError("Unexpected ElevenLabs response shape") from exc
