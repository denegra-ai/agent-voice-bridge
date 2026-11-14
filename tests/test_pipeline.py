from email.parser import BytesParser
from email.policy import HTTP

import httpx
import pytest

from app.pipeline import Reply, process_message
from app.services.agent_client import AgentRequest, ask_agent
from tests.conftest import make_settings

REQUEST = AgentRequest(sender_id="1", message_type="text", content="hi", channel="meta")


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.anyio
async def test_agent_retries_on_503_then_succeeds(tmp_path):
    responses = iter([httpx.Response(503), httpx.Response(200, json={"reply_text": "ok"})])
    async with _client(lambda r: next(responses)) as http:
        assert await ask_agent(http, make_settings(tmp_path), REQUEST) == "ok"


@pytest.mark.anyio
async def test_agent_does_not_retry_on_500(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    async with _client(handler) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await ask_agent(http, make_settings(tmp_path), REQUEST)
    assert len(calls) == 1


@pytest.mark.anyio
async def test_agent_gives_up_after_max_retries(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("down")

    async with _client(handler) as http:
        with pytest.raises(httpx.ConnectError):
            await ask_agent(http, make_settings(tmp_path, agent_max_retries=2), REQUEST)
    assert len(calls) == 3


@pytest.mark.anyio
async def test_tts_failure_falls_back_to_text(tmp_path):
    def handler(request):
        if request.url.path == "/v1/audio/speech":
            return httpx.Response(500)
        if request.url.path == "/v1/audio/transcriptions":
            return httpx.Response(200, json={"text": "hello"})
        return httpx.Response(200, json={"reply_text": "answer"})

    async def fetch_audio():
        return b"ogg", "audio/ogg"

    async with _client(handler) as http:
        reply = await process_message(
            http, make_settings(tmp_path), channel="meta", sender_id="1", fetch_audio=fetch_audio
        )
    assert reply == Reply(text="answer")


@pytest.mark.anyio
async def test_agent_failure_sends_configured_fallback(tmp_path):
    settings = make_settings(tmp_path, fallback_reply_text="Sorry, try again later.")
    async with _client(lambda r: httpx.Response(500)) as http:
        reply = await process_message(http, settings, channel="meta", sender_id="1", text="hi")
    assert reply == Reply(text="Sorry, try again later.")


@pytest.mark.anyio
async def test_agent_failure_without_fallback_stays_silent(tmp_path):
    async with _client(lambda r: httpx.Response(500)) as http:
        reply = await process_message(
            http, make_settings(tmp_path), channel="meta", sender_id="1", text="hi"
        )
    assert reply is None


@pytest.mark.anyio
async def test_deepgram_transcription(tmp_path):
    seen = {}

    def handler(request):
        if request.url.host == "api.deepgram.com":
            seen["auth"] = request.headers["Authorization"]
            body = {"results": {"channels": [{"alternatives": [{"transcript": "merhaba"}]}]}}
            return httpx.Response(200, json=body)
        return httpx.Response(200, json={"reply_text": ""})

    async def fetch_audio():
        return b"ogg", "audio/ogg; codecs=opus"

    settings = make_settings(tmp_path, stt_provider="deepgram", deepgram_api_key="dg")
    async with _client(handler) as http:
        reply = await process_message(
            http, settings, channel="meta", sender_id="1", fetch_audio=fetch_audio
        )
    assert seen["auth"] == "Token dg"
    assert reply is None  # empty reply_text means nothing is sent


def _multipart_fields(content_type: str, body: bytes) -> dict[str, str]:
    message = BytesParser(policy=HTTP).parsebytes(
        f"Content-Type: {content_type}\r\n\r\n".encode() + body
    )
    return {
        part.get_param("name", header="content-disposition"): part.get_content()
        for part in message.iter_parts()
        if part.get_filename() is None
    }


@pytest.mark.anyio
async def test_elevenlabs_transcription(tmp_path):
    seen = {}

    def handler(request):
        if request.url.host == "api.elevenlabs.io":
            seen["key"] = request.headers["xi-api-key"]
            seen["body"] = request.content
            seen["content_type"] = request.headers["Content-Type"]
            return httpx.Response(200, json={"language_code": "tur", "text": "merhaba"})
        seen["agent"] = request.content
        return httpx.Response(200, json={"reply_text": ""})

    async def fetch_audio():
        return b"ogg", "audio/ogg; codecs=opus"

    settings = make_settings(
        tmp_path, stt_provider="elevenlabs", stt_language="tr", elevenlabs_api_key="el"
    )
    async with _client(handler) as http:
        await process_message(
            http, settings, channel="meta", sender_id="1", fetch_audio=fetch_audio
        )
    assert seen["key"] == "el"
    form = _multipart_fields(seen["content_type"], seen["body"])
    assert form["model_id"] == "scribe_v2"
    assert form["language_code"] == "tr"
    assert form["tag_audio_events"] == "false"
    assert b'"content":"merhaba"' in seen["agent"]


@pytest.mark.anyio
async def test_azure_openai_stt_and_tts(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "merhaba"})
        if request.url.path.endswith("/audio/speech"):
            return httpx.Response(200, content=b"OggS")
        return httpx.Response(200, json={"reply_text": "selam"})

    async def fetch_audio():
        return b"ogg", "audio/ogg"

    settings = make_settings(
        tmp_path,
        openai_api_key=None,
        stt_provider="azure",
        tts_provider="azure",
        azure_openai_endpoint="https://my-resource.openai.azure.com/",
        azure_openai_api_key="az-key",
        azure_openai_tts_deployment="my-tts",
    )
    async with _client(handler) as http:
        reply = await process_message(
            http, settings, channel="meta", sender_id="1", fetch_audio=fetch_audio
        )

    stt, _, tts = seen
    assert str(stt.url) == (
        "https://my-resource.openai.azure.com/openai/deployments/whisper/audio/transcriptions"
        "?api-version=2025-03-01-preview"
    )
    assert stt.headers["api-key"] == "az-key"
    assert "Authorization" not in stt.headers
    assert tts.url.path == "/openai/deployments/my-tts/audio/speech"
    assert reply is not None and reply.audio_url is not None


def test_azure_provider_requires_endpoint_and_key(tmp_path):
    with pytest.raises(ValueError, match="AZURE_OPENAI_ENDPOINT.*AZURE_OPENAI_API_KEY"):
        make_settings(tmp_path, stt_provider="azure")


def _deepgram(transcript: str) -> httpx.Response:
    body = {"results": {"channels": [{"alternatives": [{"transcript": transcript}]}]}}
    return httpx.Response(200, json=body)


def _fallback_settings(tmp_path, **overrides):
    return make_settings(
        tmp_path,
        stt_provider="deepgram",
        deepgram_api_key="dg",
        stt_fallback_provider="openai",
        **overrides,
    )


async def _voice(http, settings):
    async def fetch_audio():
        return b"ogg", "audio/ogg"

    return await process_message(
        http, settings, channel="meta", sender_id="1", fetch_audio=fetch_audio
    )


@pytest.mark.anyio
async def test_empty_transcript_is_retried_with_the_fallback_provider(tmp_path):
    seen = {}

    def handler(request):
        if request.url.host == "api.deepgram.com":
            return _deepgram("")
        if request.url.path == "/v1/audio/transcriptions":
            seen["model"] = _multipart_fields(request.headers["Content-Type"], request.content)
            return httpx.Response(200, json={"text": "merhaba"})
        if request.url.path == "/v1/audio/speech":
            return httpx.Response(500)
        seen["agent"] = request.content
        return httpx.Response(200, json={"reply_text": "selam"})

    async with _client(handler) as http:
        reply = await _voice(http, _fallback_settings(tmp_path))
    assert seen["model"]["model"] == "gpt-4o-transcribe"
    assert b'"content":"merhaba"' in seen["agent"]
    assert reply == Reply(text="selam")


@pytest.mark.anyio
async def test_failed_transcription_is_retried_with_the_fallback_provider(tmp_path):
    def handler(request):
        if request.url.host == "api.deepgram.com":
            return httpx.Response(503)
        if request.url.path == "/v1/audio/transcriptions":
            return httpx.Response(200, json={"text": "merhaba"})
        if request.url.path == "/v1/audio/speech":
            return httpx.Response(500)
        return httpx.Response(200, json={"reply_text": "selam"})

    async with _client(handler) as http:
        reply = await _voice(http, _fallback_settings(tmp_path))
    assert reply == Reply(text="selam")


@pytest.mark.anyio
async def test_fallback_provider_is_not_called_when_the_primary_hears_speech(tmp_path):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.host == "api.deepgram.com":
            return _deepgram("merhaba")
        return httpx.Response(200, json={"reply_text": ""})

    async with _client(handler) as http:
        await _voice(http, _fallback_settings(tmp_path))
    assert "/v1/audio/transcriptions" not in paths


@pytest.mark.anyio
async def test_voice_note_heard_as_nothing_gets_the_fallback_reply(tmp_path):
    def handler(request):
        if request.url.host == "api.deepgram.com":
            return _deepgram("")
        if request.url.path == "/v1/audio/transcriptions":
            return httpx.Response(200, json={"text": "  "})
        raise AssertionError("the agent must not be called with an empty message")

    settings = _fallback_settings(tmp_path, fallback_reply_text="Sorry, I couldn't hear that.")
    async with _client(handler) as http:
        reply = await _voice(http, settings)
    assert reply == Reply(text="Sorry, I couldn't hear that.")


def test_fallback_provider_needs_its_own_key(tmp_path):
    with pytest.raises(ValueError, match="DEEPGRAM_API_KEY"):
        make_settings(tmp_path, stt_fallback_provider="deepgram")


def test_fallback_provider_must_differ_from_the_primary(tmp_path):
    with pytest.raises(ValueError, match="must differ"):
        make_settings(tmp_path, stt_fallback_provider="openai")


@pytest.mark.anyio
async def test_misconfigured_primary_is_logged_as_an_error(tmp_path, caplog):
    def handler(request):
        if request.url.host == "api.deepgram.com":
            return httpx.Response(401)
        if request.url.path == "/v1/audio/transcriptions":
            return httpx.Response(200, json={"text": "merhaba"})
        return httpx.Response(200, json={"reply_text": ""})

    async with _client(handler) as http:
        await _voice(http, _fallback_settings(tmp_path))
    failed = [r for r in caplog.records if "Transcription via deepgram failed" in r.message]
    assert [r.levelname for r in failed] == ["ERROR"]


@pytest.mark.anyio
async def test_deepgram_without_a_language_uses_multilingual_mode(tmp_path):
    seen = {}

    def handler(request):
        if request.url.host == "api.deepgram.com":
            seen["params"] = dict(request.url.params)
            return _deepgram("merhaba")
        return httpx.Response(200, json={"reply_text": ""})

    settings = make_settings(tmp_path, stt_provider="deepgram", deepgram_api_key="dg")
    async with _client(handler) as http:
        await _voice(http, settings)
    assert seen["params"]["language"] == "multi"
    assert "detect_language" not in seen["params"]
