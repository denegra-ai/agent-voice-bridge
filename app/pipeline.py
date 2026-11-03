"""Channel-independent flow: inbound message -> (STT) -> agent -> (TTS) -> reply."""

import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.services.agent_client import AgentRequest, ask_agent
from app.services.transcription import transcribe
from app.services.tts import synthesize

logger = logging.getLogger(__name__)

SEEN_IDS_CAPACITY = 5000

Channel = Literal["meta", "twilio"]
AudioFetcher = Callable[[], Awaitable[tuple[bytes, str]]]


@dataclass(frozen=True)
class Reply:
    text: str | None = None
    audio_url: str | None = None


def is_duplicate(seen: "OrderedDict[str, None]", message_id: str) -> bool:
    """Both providers redeliver webhooks; remember recent ids so each message is answered once."""
    if message_id in seen:
        return True
    seen[message_id] = None
    if len(seen) > SEEN_IDS_CAPACITY:
        seen.popitem(last=False)
    return False


async def process_message(
    http: httpx.AsyncClient,
    settings: Settings,
    *,
    channel: Channel,
    sender_id: str,
    text: str | None = None,
    fetch_audio: AudioFetcher | None = None,
) -> Reply | None:
    try:
        if fetch_audio is not None:
            audio, mime_type = await fetch_audio()
            content = await transcribe(http, settings, audio, mime_type)
            message_type: Literal["voice", "text"] = "voice"
        else:
            content, message_type = (text or "").strip(), "text"

        if not content:
            logger.info("Empty %s message from %s; nothing to send", message_type, channel)
            return None

        reply_text = await ask_agent(
            http,
            settings,
            AgentRequest(
                sender_id=sender_id, message_type=message_type, content=content, channel=channel
            ),
        )
    except (httpx.HTTPError, ValueError, ValidationError):
        logger.exception("Pipeline failed for %s message", channel)
        return Reply(text=settings.fallback_reply_text) if settings.fallback_reply_text else None

    if not reply_text.strip():
        return None

    if message_type == "voice":
        try:
            filename = await synthesize(http, settings, reply_text)
            return Reply(audio_url=f"{settings.public_base}/static/audio/{filename}")
        except (httpx.HTTPError, OSError):
            logger.exception("TTS failed; falling back to a text reply")

    return Reply(text=reply_text)
