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
