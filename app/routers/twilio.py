import base64
import hashlib
import hmac
import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import Response

from app.config import Settings
from app.pipeline import Reply, is_duplicate, process_message
from app.services.transcription import download_media

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook/twilio", tags=["twilio"])

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def compute_signature(auth_token: str, url: str, params: list[tuple[str, str]]) -> str:
    """Twilio request signature: base64(HMAC-SHA1(token, url + sorted key/value pairs))."""
    data = url + "".join(key + value for key, value in sorted(params))
    digest = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def _twilio_auth(settings: Settings) -> tuple[str, str]:
    assert settings.twilio_account_sid is not None and settings.twilio_auth_token is not None
    return settings.twilio_account_sid, settings.twilio_auth_token.get_secret_value()


@router.post("")
async def receive_webhook(request: Request, background: BackgroundTasks) -> Response:
    settings: Settings = request.app.state.settings
    form = await request.form()
    params = [(key, value) for key, value in form.multi_items() if isinstance(value, str)]

    # Rebuild the URL Twilio called from PUBLIC_BASE_URL: behind a proxy or tunnel,
    # request.url carries the internal scheme and host and the signature would never match.
    url = settings.public_base + request.url.path
    if request.url.query:
        url += f"?{request.url.query}"
    signature = request.headers.get("X-Twilio-Signature", "")
    expected = compute_signature(_twilio_auth(settings)[1], url, params)
    if not hmac.compare_digest(signature.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Invalid signature")

    fields = dict(params)
    message_sid = fields.get("MessageSid", "")
    sender, recipient = fields.get("From", ""), fields.get("To", "")
    body = fields.get("Body", "")
    media_type = fields.get("MediaContentType0", "")
    media_url = fields.get("MediaUrl0", "")
    has_audio = fields.get("NumMedia", "0") != "0" and media_type.startswith("audio/")

    if not sender or (message_sid and is_duplicate(request.app.state.seen_ids, message_sid)):
        return Response(EMPTY_TWIML, media_type="application/xml")
    if not has_audio and not body.strip():
        logger.info("Ignoring Twilio message without text or audio")
        return Response(EMPTY_TWIML, media_type="application/xml")

    # Twilio times out after 15 s, so reply through the REST API instead of inline TwiML.
    background.add_task(
        _handle_message,
        request.app.state.http,
        settings,
        sender,
        recipient,
        body,
        (media_url, media_type) if has_audio else None,
    )
    return Response(EMPTY_TWIML, media_type="application/xml")


async def _handle_message(
    http: httpx.AsyncClient,
    settings: Settings,
    sender: str,
    recipient: str,
    body: str,
    media: tuple[str, str] | None,
) -> None:
    if media is not None:
        media_url, media_type = media

        async def fetch_audio() -> tuple[bytes, str]:
            return await download_media(http, media_url, auth=_twilio_auth(settings)), media_type

        reply = await process_message(
            http, settings, channel="twilio", sender_id=sender, fetch_audio=fetch_audio
        )
    else:
        reply = await process_message(http, settings, channel="twilio", sender_id=sender, text=body)

    if reply is not None:
        await _send_reply(http, settings, sender=recipient, to=sender, reply=reply)


async def _send_reply(
    http: httpx.AsyncClient, settings: Settings, *, sender: str, to: str, reply: Reply
) -> None:
    account_sid, _ = _twilio_auth(settings)
    data = {"From": sender, "To": to}
    if reply.audio_url:
        data["MediaUrl"] = reply.audio_url
    else:
        data["Body"] = reply.text or ""

    try:
        response = await http.post(
            f"{TWILIO_API_BASE}/Accounts/{account_sid}/Messages.json",
            auth=_twilio_auth(settings),
            data=data,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error("Twilio send failed: HTTP %s %s", exc.response.status_code, exc.response.text)
    except httpx.HTTPError:
        logger.exception("Twilio send failed")
