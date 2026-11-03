import json

import httpx

from tests.conftest import VERIFY_TOKEN, meta_signature


def _webhook(message: dict) -> bytes:
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "PN1"},
                                "messages": [message],
                            },
                        }
                    ],
                }
            ],
        }
    ).encode()


def _post(client, body: bytes, signature: str | None = None):
    return client.post(
        "/webhook/meta",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature or meta_signature(body),
        },
    )


def test_verify_returns_challenge(make_client):
    client = make_client()
    params = {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "42"}
    response = client.get("/webhook/meta", params=params)
    assert response.status_code == 200
    assert response.text == "42"


def test_verify_rejects_wrong_token(make_client):
    client = make_client()
    params = {"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"}
    assert client.get("/webhook/meta", params=params).status_code == 403


def test_rejects_bad_signature(make_client, upstream):
    client = make_client()
    body = _webhook({"id": "m1", "from": "905551112233", "type": "text", "text": {"body": "hi"}})
    assert _post(client, body, signature="sha256=deadbeef").status_code == 403
    assert upstream.calls == []


def test_text_message_gets_text_reply(make_client, upstream):
    upstream.on("POST", "graph.facebook.com", "/", lambda r: httpx.Response(200, json={}))
    client = make_client()
    body = _webhook({"id": "m2", "from": "905551112233", "type": "text", "text": {"body": "hi"}})

    assert _post(client, body).status_code == 200

    (agent_call,) = upstream.find("agent.test")
    assert json.loads(agent_call.content) == {
        "sender_id": "905551112233",
        "message_type": "text",
        "content": "hi",
        "channel": "meta",
    }
    (send,) = upstream.find("graph.facebook.com", "/v23.0/PN1/messages")
    assert send.headers["Authorization"] == "Bearer meta-token"
    assert json.loads(send.content) == {
        "messaging_product": "whatsapp",
        "to": "905551112233",
        "type": "text",
        "text": {"body": "reply: hi"},
    }
    assert upstream.find("api.openai.com") == []


def test_voice_message_gets_voice_reply(make_client, upstream, tmp_path):
    upstream.on(
        "GET",
        "graph.facebook.com",
        "/v23.0/MEDIA1",
        lambda r: httpx.Response(200, json={"url": "https://lookaside.fbsbx.com/media/1"}),
    )
    upstream.on("GET", "lookaside.fbsbx.com", "/", lambda r: httpx.Response(200, content=b"ogg"))
    upstream.on("POST", "graph.facebook.com", "/", lambda r: httpx.Response(200, json={}))
    client = make_client()
    body = _webhook(
        {
            "id": "m3",
            "from": "905551112233",
            "type": "audio",
            "audio": {"id": "MEDIA1", "mime_type": "audio/ogg; codecs=opus"},
        }
    )

    assert _post(client, body).status_code == 200

    (download,) = upstream.find("lookaside.fbsbx.com")
    assert download.headers["Authorization"] == "Bearer meta-token"
    (agent_call,) = upstream.find("agent.test")
    assert json.loads(agent_call.content)["message_type"] == "voice"
    assert json.loads(agent_call.content)["content"] == "hello from voice"
    (tts,) = upstream.find("api.openai.com", "/v1/audio/speech")
    assert json.loads(tts.content)["response_format"] == "opus"

    (send,) = upstream.find("graph.facebook.com", "/v23.0/PN1/messages")
    sent = json.loads(send.content)
    assert sent["type"] == "audio"
    link = sent["audio"]["link"]
    assert link.startswith("https://bridge.test/static/audio/") and link.endswith(".ogg")

    served = client.get(link.removeprefix("https://bridge.test"))
    assert served.status_code == 200
    assert served.content == b"OggS-fake-opus"
    assert served.headers["content-type"] == "audio/ogg"


def test_duplicate_delivery_is_answered_once(make_client, upstream):
    upstream.on("POST", "graph.facebook.com", "/", lambda r: httpx.Response(200, json={}))
    client = make_client()
    body = _webhook({"id": "m4", "from": "905551112233", "type": "text", "text": {"body": "hi"}})

    _post(client, body)
    _post(client, body)

    assert len(upstream.find("agent.test")) == 1


def test_status_updates_and_unsupported_types_are_ignored(make_client, upstream):
    client = make_client()
    body = _webhook({"id": "m5", "from": "905551112233", "type": "sticker"})
    assert _post(client, body).status_code == 200
    assert upstream.calls == []
