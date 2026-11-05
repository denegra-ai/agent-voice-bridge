import json
from urllib.parse import parse_qs

import httpx

from app.routers.twilio import compute_signature
from app.services import transcription
from tests.conftest import PUBLIC_BASE, TWILIO_SID, TWILIO_TOKEN

MESSAGES_PATH = f"/2010-04-01/Accounts/{TWILIO_SID}/Messages.json"


def _post(client, form: dict[str, str], signature: str | None = None):
    url = f"{PUBLIC_BASE}/webhook/twilio"
    return client.post(
        "/webhook/twilio",
        data=form,
        headers={
            "X-Twilio-Signature": signature
            or compute_signature(TWILIO_TOKEN, url, list(form.items()))
        },
    )


def test_signature_matches_twilio_reference_example():
    # Example from Twilio's webhook security docs.
    params = {
        "CallSid": "CA1234567890ABCDE",
        "Caller": "+12349013030",
        "Digits": "1234",
        "From": "+12349013030",
        "To": "+18005551212",
    }
    signature = compute_signature(
        "12345", "https://mycompany.com/myapp.php?foo=1&bar=2", list(params.items())
    )
    assert signature == "0/KCTR6DLpKmkAf8muzZqo1nDgQ="


def test_rejects_bad_signature(make_client, upstream):
    client = make_client()
    response = _post(client, {"From": "whatsapp:+1", "Body": "hi"}, signature="bad")
    assert response.status_code == 403
    assert upstream.calls == []


def test_text_message_gets_text_reply(make_client, upstream):
    upstream.on("POST", "api.twilio.com", "/", lambda r: httpx.Response(201, json={}))
    client = make_client()
    form = {
        "MessageSid": "SM1",
        "From": "whatsapp:+905551112233",
        "To": "whatsapp:+14155238886",
        "Body": "hi",
        "NumMedia": "0",
    }

    response = _post(client, form)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    (agent_call,) = upstream.find("agent.test")
    assert json.loads(agent_call.content) == {
        "sender_id": "whatsapp:+905551112233",
        "message_type": "text",
        "content": "hi",
        "channel": "twilio",
    }
    (send,) = upstream.find("api.twilio.com", MESSAGES_PATH)
    assert parse_qs(send.content.decode()) == {
        "From": ["whatsapp:+14155238886"],
        "To": ["whatsapp:+905551112233"],
        "Body": ["reply: hi"],
    }


def test_voice_message_gets_voice_reply(make_client, upstream):
    upstream.on("GET", "api.twilio.com", "/media", lambda r: httpx.Response(200, content=b"ogg"))
    upstream.on("POST", "api.twilio.com", "/2010", lambda r: httpx.Response(201, json={}))
    client = make_client()
    form = {
        "MessageSid": "SM2",
        "From": "whatsapp:+905551112233",
        "To": "whatsapp:+14155238886",
        "Body": "",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/ME1",
        "MediaContentType0": "audio/ogg",
    }

    assert _post(client, form).status_code == 200

    (download,) = upstream.find("api.twilio.com", "/media")
    assert download.headers["Authorization"].startswith("Basic ")
    (agent_call,) = upstream.find("agent.test")
    assert json.loads(agent_call.content)["message_type"] == "voice"
    (send,) = upstream.find("api.twilio.com", MESSAGES_PATH)
    sent = parse_qs(send.content.decode())
    assert "Body" not in sent
    assert sent["MediaUrl"][0].startswith(f"{PUBLIC_BASE}/static/audio/")


def _voice_form(message_sid: str, media_id: str) -> dict[str, str]:
    return {
        "MessageSid": message_sid,
        "From": "whatsapp:+905551112233",
        "To": "whatsapp:+14155238886",
        "Body": "",
        "NumMedia": "1",
        "MediaUrl0": f"https://api.twilio.com/media/{media_id}",
        "MediaContentType0": "audio/ogg",
    }


def test_voice_note_waits_until_twilio_media_is_ready(make_client, upstream, monkeypatch):
    monkeypatch.setattr(transcription, "MEDIA_NOT_READY_DELAYS_SECONDS", (0, 0, 0, 0))
    media = iter([httpx.Response(404), httpx.Response(404), httpx.Response(200, content=b"ogg")])
    upstream.on("GET", "api.twilio.com", "/media", lambda r: next(media))
    upstream.on("POST", "api.twilio.com", "/2010", lambda r: httpx.Response(201, json={}))
    assert _post(make_client(), _voice_form("SM3", "ME2")).status_code == 200

    assert len(upstream.find("api.twilio.com", "/media")) == 3
    (agent_call,) = upstream.find("agent.test")
    assert json.loads(agent_call.content)["message_type"] == "voice"
    assert len(upstream.find("api.twilio.com", MESSAGES_PATH)) == 1


def test_voice_note_gives_up_when_twilio_media_never_appears(make_client, upstream, monkeypatch):
    monkeypatch.setattr(transcription, "MEDIA_NOT_READY_DELAYS_SECONDS", (0, 0, 0, 0))
    upstream.on("GET", "api.twilio.com", "/media", lambda r: httpx.Response(404))
    assert _post(make_client(), _voice_form("SM4", "ME3")).status_code == 200

    assert len(upstream.find("api.twilio.com", "/media")) == 5
    assert upstream.find("agent.test") == []
