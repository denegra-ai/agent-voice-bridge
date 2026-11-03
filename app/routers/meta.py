import hashlib
import hmac
import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings
from app.pipeline import Reply, is_duplicate, process_message
from app.services.transcription import download_media

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook/meta", tags=["meta"])


class _Text(BaseModel):
    body: str


class _Audio(BaseModel):
    id: str
    mime_type: str = "audio/ogg"


class _Message(BaseModel):
    id: str
    from_: str = Field(alias="from")
    type: str
    text: _Text | None = None
    audio: _Audio | None = None


class _Metadata(BaseModel):
    phone_number_id: str


class _Value(BaseModel):
    metadata: _Metadata | None = None
    messages: list[_Message] = []


class _Change(BaseModel):
    value: _Value


class _Entry(BaseModel):
    changes: list[_Change] = []


class _Webhook(BaseModel):
    entry: list[_Entry] = []


def _graph_url(settings: Settings, path: str) -> str:
    return f"https://graph.facebook.com/{settings.meta_graph_api_version}/{path}"


def _auth_header(settings: Settings) -> dict[str, str]:
    assert settings.meta_access_token is not None
    return {"Authorization": f"Bearer {settings.meta_access_token.get_secret_value()}"}


@router.get("", response_class=PlainTextResponse)
async def verify_webhook(
    request: Request,
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
) -> str:
    settings: Settings = request.app.state.settings
    assert settings.meta_verify_token is not None
    expected = settings.meta_verify_token.get_secret_value()
    if mode == "subscribe" and hmac.compare_digest(token.encode(), expected.encode()):
        return challenge
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("")
async def receive_webhook(request: Request, background: BackgroundTasks) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    body = await request.body()

    assert settings.meta_app_secret is not None
    signature = request.headers.get("X-Hub-Signature-256", "")
    expected = (
        "sha256="
        + hmac.new(
            settings.meta_app_secret.get_secret_value().encode(), body, hashlib.sha256
        ).hexdigest()
    )
    if not hmac.compare_digest(signature.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = _Webhook.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="Malformed payload") from exc

    # Meta expects a fast 200; the slow STT/agent/TTS work runs after the response is sent.
    for entry in payload.entry:
        for change in entry.changes:
            metadata = change.value.metadata
            for message in change.value.messages:
                if metadata is None or is_duplicate(request.app.state.seen_ids, message.id):
                    continue
                background.add_task(
                    _handle_message,
                    request.app.state.http,
                    settings,
                    metadata.phone_number_id,
                    message,
                )
    return {"status": "ok"}


async def _handle_message(
    http: httpx.AsyncClient, settings: Settings, phone_number_id: str, message: _Message
) -> None:
    if message.type == "text" and message.text is not None:
        reply = await process_message(
            http, settings, channel="meta", sender_id=message.from_, text=message.text.body
        )
    elif message.type == "audio" and message.audio is not None:
        audio = message.audio

        async def fetch_audio() -> tuple[bytes, str]:
            info = await http.get(_graph_url(settings, audio.id), headers=_auth_header(settings))
            info.raise_for_status()
            media_url = info.json().get("url")
            if not media_url:
                raise ValueError("Meta media lookup returned no url")
            data = await download_media(http, media_url, headers=_auth_header(settings))
            return data, audio.mime_type

        reply = await process_message(
            http, settings, channel="meta", sender_id=message.from_, fetch_audio=fetch_audio
        )
    else:
        logger.info("Ignoring unsupported Meta message type %r", message.type)
        return

    if reply is not None:
        await _send_reply(http, settings, phone_number_id, message.from_, reply)


async def _send_reply(
    http: httpx.AsyncClient, settings: Settings, phone_number_id: str, to: str, reply: Reply
) -> None:
    payload: dict[str, object] = {"messaging_product": "whatsapp", "to": to}
    if reply.audio_url:
        payload |= {"type": "audio", "audio": {"link": reply.audio_url}}
    else:
        payload |= {"type": "text", "text": {"body": reply.text}}

    try:
        response = await http.post(
            _graph_url(settings, f"{phone_number_id}/messages"),
            headers=_auth_header(settings),
            json=payload,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "WhatsApp send failed: HTTP %s %s", exc.response.status_code, exc.response.text
        )
    except httpx.HTTPError:
        logger.exception("WhatsApp send failed")
