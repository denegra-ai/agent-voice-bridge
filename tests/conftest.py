import hashlib
import hmac
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

AGENT_URL = "https://agent.test/agent"
PUBLIC_BASE = "https://bridge.test"
APP_SECRET = "meta-app-secret"
VERIFY_TOKEN = "verify-me"
TWILIO_SID = "AC123"
TWILIO_TOKEN = "twilio-token"

Handler = Callable[[httpx.Request], httpx.Response]


@dataclass
class FakeUpstream:
    """Routes outgoing requests by (method, host, path-prefix) and records them."""

    routes: dict[tuple[str, str, str], Handler] = field(default_factory=dict)
    calls: list[httpx.Request] = field(default_factory=list)

    def on(self, method: str, host: str, path_prefix: str, handler: Handler) -> None:
        self.routes[(method, host, path_prefix)] = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        for (method, host, prefix), handler in self.routes.items():
            if (
                request.method == method
                and request.url.host == host
                and request.url.path.startswith(prefix)
            ):
                return handler(request)
        return httpx.Response(404, json={"error": f"no fake for {request.url}"})

    def find(self, host: str, path_prefix: str = "") -> list[httpx.Request]:
        return [c for c in self.calls if c.url.host == host and c.url.path.startswith(path_prefix)]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_settings(tmp_path, **overrides) -> Settings:
    values = {
        "agent_webhook_url": AGENT_URL,
        "public_base_url": PUBLIC_BASE,
        "meta_access_token": "meta-token",
        "meta_verify_token": VERIFY_TOKEN,
        "meta_app_secret": APP_SECRET,
        "twilio_account_sid": TWILIO_SID,
        "twilio_auth_token": TWILIO_TOKEN,
        "openai_api_key": "sk-test",
        "agent_retry_backoff_seconds": 0,
        "audio_dir": tmp_path / "audio",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def upstream() -> FakeUpstream:
    fake = FakeUpstream()
    fake.on(
        "POST",
        "agent.test",
        "/agent",
        lambda r: httpx.Response(
            200, json={"reply_text": "reply: " + json.loads(r.content)["content"]}
        ),
    )
    fake.on(
        "POST",
        "api.openai.com",
        "/v1/audio/transcriptions",
        lambda r: httpx.Response(200, json={"text": " hello from voice "}),
    )
    fake.on(
        "POST",
        "api.openai.com",
        "/v1/audio/speech",
        lambda r: httpx.Response(200, content=b"OggS-fake-opus"),
    )
    return fake


@pytest.fixture
def make_client(tmp_path, upstream) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def factory(**overrides) -> TestClient:
        app = create_app(make_settings(tmp_path, **overrides))
        client = TestClient(app)
        client.__enter__()
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


def meta_signature(body: bytes) -> str:
    return "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
