import pytest
from pydantic import ValidationError

from app.config import Settings
from tests.conftest import make_settings


def test_health_lists_enabled_channels(make_client):
    response = make_client().get("/health")
    assert response.status_code == 200
    assert response.json()["channels"] == ["meta", "twilio"]


def test_disabled_channel_has_no_route(make_client):
    client = make_client(twilio_account_sid=None, twilio_auth_token=None)
    assert client.post("/webhook/twilio").status_code == 404


def test_missing_meta_secret_fails_fast(tmp_path):
    with pytest.raises(ValidationError, match="META_APP_SECRET"):
        make_settings(tmp_path, meta_app_secret=None)


def test_elevenlabs_requires_voice_id(tmp_path):
    with pytest.raises(ValidationError, match="ELEVENLABS_VOICE_ID"):
        make_settings(tmp_path, tts_provider="elevenlabs", elevenlabs_api_key="el")


def test_empty_env_value_means_unset(monkeypatch):
    for name, value in {
        "AGENT_WEBHOOK_URL": "https://agent.test/agent",
        "PUBLIC_BASE_URL": "https://bridge.test",
        "META_ACCESS_TOKEN": "token",
        "META_VERIFY_TOKEN": "verify",
        "META_APP_SECRET": "secret",
        "OPENAI_API_KEY": "sk-test",
        "TWILIO_ACCOUNT_SID": "",
        "TWILIO_AUTH_TOKEN": "",
    }.items():
        monkeypatch.setenv(name, value)
    assert Settings(_env_file=None).twilio_enabled is False
