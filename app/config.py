from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_ignore_empty: "META_ACCESS_TOKEN=" in .env means unset, not an empty token.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    # Agent
    agent_webhook_url: AnyHttpUrl
    agent_auth_token: SecretStr | None = None
    agent_timeout_seconds: float = 30.0
    agent_max_retries: int = 2
    agent_retry_backoff_seconds: float = 0.5

    # Public URL of this service; used for audio links and Twilio signature checks.
    public_base_url: AnyHttpUrl

    # Meta WhatsApp Cloud API (enabled when META_ACCESS_TOKEN is set)
    meta_access_token: SecretStr | None = None
    meta_verify_token: SecretStr | None = None
    meta_app_secret: SecretStr | None = None
    meta_graph_api_version: str = "v23.0"

    # Twilio (enabled when TWILIO_ACCOUNT_SID is set)
    twilio_account_sid: str | None = None
    twilio_auth_token: SecretStr | None = None

    # Speech-to-text
    stt_provider: Literal["openai", "deepgram"] = "openai"
    stt_language: str | None = None
    openai_api_key: SecretStr | None = None
    openai_stt_model: str = "whisper-1"
    deepgram_api_key: SecretStr | None = None
    deepgram_model: str = "nova-3"

    # Text-to-speech
    tts_provider: Literal["openai", "elevenlabs"] = "openai"
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "alloy"
    elevenlabs_api_key: SecretStr | None = None
    elevenlabs_voice_id: str | None = None
    elevenlabs_model_id: str = "eleven_multilingual_v2"

    # Runtime
    audio_dir: Path = Path("data/audio")
    audio_ttl_seconds: int = 3600
    fallback_reply_text: str = ""
    log_level: str = "INFO"

    @property
    def meta_enabled(self) -> bool:
        return self.meta_access_token is not None

    @property
    def twilio_enabled(self) -> bool:
        return self.twilio_account_sid is not None

    @property
    def public_base(self) -> str:
        return str(self.public_base_url).rstrip("/")

    @model_validator(mode="after")
    def _check_required_secrets(self) -> "Settings":
        missing: list[str] = []
        if not (self.meta_enabled or self.twilio_enabled):
            missing.append("META_ACCESS_TOKEN or TWILIO_ACCOUNT_SID (at least one channel)")
        if self.meta_enabled:
            if self.meta_verify_token is None:
                missing.append("META_VERIFY_TOKEN")
            if self.meta_app_secret is None:
                missing.append("META_APP_SECRET")
        if self.twilio_enabled and self.twilio_auth_token is None:
            missing.append("TWILIO_AUTH_TOKEN")
        if self.stt_provider == "openai" and self.openai_api_key is None:
            missing.append("OPENAI_API_KEY (STT_PROVIDER=openai)")
        if self.stt_provider == "deepgram" and self.deepgram_api_key is None:
            missing.append("DEEPGRAM_API_KEY (STT_PROVIDER=deepgram)")
        if self.tts_provider == "openai" and self.openai_api_key is None:
            missing.append("OPENAI_API_KEY (TTS_PROVIDER=openai)")
        if self.tts_provider == "elevenlabs":
            if self.elevenlabs_api_key is None:
                missing.append("ELEVENLABS_API_KEY")
            if self.elevenlabs_voice_id is None:
                missing.append("ELEVENLABS_VOICE_ID")
        if missing:
            raise ValueError("Missing required settings: " + ", ".join(dict.fromkeys(missing)))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
