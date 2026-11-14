import importlib.util
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("configure", ROOT / "scripts" / "configure.py")
configure = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(configure)


def _scripted(answers):
    queue = list(answers)

    def ask(prompt):
        return queue.pop(0)

    return ask, queue


def test_twilio_with_azure_writes_env_and_checks_keys(tmp_path):
    env_file = tmp_path / ".env"
    ask, plain = _scripted(
        [
            "https://agent.example.com/hook",  # agent URL
            "https://bridge.example.com",  # public URL
            "n",  # Meta
            "y",  # Twilio
            "AC123",  # Twilio SID
            "azure",  # STT
            "azure",  # TTS
            "https://res.openai.azure.com/",  # Azure endpoint
            "",  # STT deployment -> default
            "my-tts",  # TTS deployment
            "y",  # run checks
        ]
    )
    secret, secrets_left = _scripted(["", "tw-token", "az-key"])  # agent token, twilio, azure
    checked = []

    def check(url, headers):
        checked.append((url, headers))
        return None

    assert configure.main(ask, secret, check, env_file) == 0
    assert plain == [] and secrets_left == []

    values = configure.read_env(env_file)
    assert values["AGENT_WEBHOOK_URL"] == "https://agent.example.com/hook"
    assert values["TWILIO_ACCOUNT_SID"] == "AC123"
    assert values["TWILIO_AUTH_TOKEN"] == "tw-token"
    assert values["META_ACCESS_TOKEN"] == ""
    assert values["STT_PROVIDER"] == values["TTS_PROVIDER"] == "azure"
    assert values["AZURE_OPENAI_API_KEY"] == "az-key"
    assert values["AZURE_OPENAI_STT_DEPLOYMENT"] == "whisper"
    assert values["AZURE_OPENAI_TTS_DEPLOYMENT"] == "my-tts"
    assert "# ─── Agent" in env_file.read_text()  # template comments are kept
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

    urls = [url for url, _ in checked]
    assert urls == [
        "https://api.twilio.com/2010-04-01/Accounts/AC123.json",
        "https://res.openai.azure.com/openai/models?api-version=2024-10-21",
    ]
    assert checked[1][1] == {"api-key": "az-key"}


def test_rerun_keeps_existing_secrets_on_enter(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AGENT_WEBHOOK_URL=https://agent.example.com/hook\n"
        "PUBLIC_BASE_URL=https://bridge.example.com\n"
        "META_ACCESS_TOKEN=meta-token\nMETA_VERIFY_TOKEN=keep-me\nMETA_APP_SECRET=app-secret\n"
        "STT_PROVIDER=openai\nTTS_PROVIDER=openai\nOPENAI_API_KEY=sk-old\n"
    )
    ask, _ = _scripted(["y", "", "", "", "n", "", "", "n"])  # update, defaults, no Twilio, no check
    secret, _ = _scripted(["", "", "", ""])

    assert configure.main(ask, secret, lambda u, h: None, env_file) == 0

    values = configure.read_env(env_file)
    assert values["META_ACCESS_TOKEN"] == "meta-token"
    assert values["META_VERIFY_TOKEN"] == "keep-me"
    assert values["OPENAI_API_KEY"] == "sk-old"


def test_failed_check_does_not_save_by_default(tmp_path):
    env_file = tmp_path / ".env"
    ask, _ = _scripted(
        ["https://a.example.com", "https://b.example.com", "n", "y", "AC1", "", "", "y", ""]
    )
    secret, _ = _scripted(["", "bad-token", "sk-x"])

    assert configure.main(ask, secret, lambda u, h: "HTTP 401 (wrong key?)", env_file) == 1
    assert not env_file.exists()


def test_elevenlabs_speech_to_text_key_is_flagged_as_unchecked(tmp_path, capsys):
    env_file = tmp_path / ".env"
    ask, plain = _scripted(
        [
            "https://agent.example.com/hook",  # agent URL
            "https://bridge.example.com",  # public URL
            "n",  # Meta
            "y",  # Twilio
            "AC123",  # Twilio SID
            "elevenlabs",  # STT
            "openai",  # TTS
            "y",  # run checks
        ]
    )
    secret, secrets_left = _scripted(["", "tw-token", "sk-test", "el-key"])
    checked = []

    def check(url, headers):
        checked.append(url)
        return None

    assert configure.main(ask, secret, check, env_file) == 0
    assert plain == [] and secrets_left == []
    values = configure.read_env(env_file)
    assert values["STT_PROVIDER"] == "elevenlabs"
    assert values["ELEVENLABS_API_KEY"] == "el-key"
    assert values["ELEVENLABS_VOICE_ID"] == ""
    assert not any("elevenlabs" in url for url in checked)
    assert "ElevenLabs: not checked" in capsys.readouterr().out


def test_hand_set_fallback_provider_key_is_asked_and_checked(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AGENT_WEBHOOK_URL=https://agent.example.com/hook\n"
        "PUBLIC_BASE_URL=https://bridge.example.com\n"
        "TWILIO_ACCOUNT_SID=AC123\nTWILIO_AUTH_TOKEN=tw\n"
        "STT_PROVIDER=openai\nTTS_PROVIDER=openai\nOPENAI_API_KEY=sk-old\n"
        "STT_FALLBACK_PROVIDER=deepgram\n"
    )
    ask, plain = _scripted(["y", "", "", "n", "y", "", "", "", "y"])
    secret, secrets_left = _scripted(["", "", "", "dg-key"])  # agent, twilio, openai, deepgram
    checked = []

    def check(url, headers):
        checked.append(url)
        return None

    assert configure.main(ask, secret, check, env_file) == 0
    assert plain == [] and secrets_left == []
    values = configure.read_env(env_file)
    assert values["STT_FALLBACK_PROVIDER"] == "deepgram"
    assert values["DEEPGRAM_API_KEY"] == "dg-key"
    assert "https://api.deepgram.com/v1/projects" in checked
