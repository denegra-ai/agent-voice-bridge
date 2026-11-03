#!/usr/bin/env python3
"""Interactive setup: asks for your own channel and AI keys, checks them, writes .env.

    python3 scripts/configure.py

Needs only the Python standard library (3.9+), so it runs before anything is installed.
Secrets are typed hidden and never printed. Re-running keeps current values as defaults.
"""

from __future__ import annotations

import base64
import getpass
import os
import re
import secrets
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / ".env.example"
ENV_FILE = ROOT / ".env"

Ask = Callable[[str], str]


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            match = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
            if match:
                values[match.group(1)] = match.group(2)
    return values


def render_env(template: str, values: dict[str, str]) -> str:
    """Fill the commented .env.example with values; keys the template lacks go at the end."""
    lines, used = [], set()
    for line in template.splitlines():
        match = re.match(r"^([A-Z0-9_]+)=", line)
        if match and match.group(1) in values:
            key = match.group(1)
            lines.append(f"{key}={values[key]}")
            used.add(key)
        else:
            lines.append(line)
    lines += [f"{key}={value}" for key, value in values.items() if key not in used]
    return "\n".join(lines) + "\n"


def http_check(url: str, headers: dict[str, str]) -> str | None:
    """GET url; return None if it answers 2xx, else a short reason."""
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 (fixed https URLs)
    try:
        with urllib.request.urlopen(request, timeout=15):  # noqa: S310
            return None
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}" + (" (wrong key?)" if exc.code in (401, 403) else "")
    except (urllib.error.URLError, TimeoutError) as exc:
        return f"not reachable ({exc})"


def credential_checks(v: dict[str, str]) -> list[tuple[str, str, dict[str, str]]]:
    """(label, url, headers) for a cheap authenticated GET against each configured service."""
    checks = []
    if v.get("TWILIO_ACCOUNT_SID"):
        basic = base64.b64encode(
            f"{v['TWILIO_ACCOUNT_SID']}:{v.get('TWILIO_AUTH_TOKEN', '')}".encode()
        ).decode()
        checks.append(
            (
                "Twilio",
                f"https://api.twilio.com/2010-04-01/Accounts/{v['TWILIO_ACCOUNT_SID']}.json",
                {"Authorization": f"Basic {basic}"},
            )
        )
    if v.get("META_ACCESS_TOKEN"):
        version = v.get("META_GRAPH_API_VERSION") or "v23.0"
        checks.append(
            (
                "Meta",
                f"https://graph.facebook.com/{version}/me",
                {"Authorization": f"Bearer {v['META_ACCESS_TOKEN']}"},
            )
        )
    providers = {v.get("STT_PROVIDER"), v.get("TTS_PROVIDER")}
    if "openai" in providers:
        checks.append(
            (
                "OpenAI",
                "https://api.openai.com/v1/models",
                {"Authorization": f"Bearer {v.get('OPENAI_API_KEY', '')}"},
            )
        )
    if "azure" in providers:
        endpoint = v.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
        checks.append(
            (
                "Azure OpenAI",
                f"{endpoint}/openai/models?api-version=2024-10-21",
                {"api-key": v.get("AZURE_OPENAI_API_KEY", "")},
            )
        )
    if "deepgram" in providers:
        checks.append(
            (
                "Deepgram",
                "https://api.deepgram.com/v1/projects",
                {"Authorization": f"Token {v.get('DEEPGRAM_API_KEY', '')}"},
            )
        )
    if "elevenlabs" in providers:
        checks.append(
            (
                "ElevenLabs",
                f"https://api.elevenlabs.io/v1/voices/{v.get('ELEVENLABS_VOICE_ID', '')}",
                {"xi-api-key": v.get("ELEVENLABS_API_KEY", "")},
            )
        )
    return checks


class Wizard:
    def __init__(self, current: dict[str, str], ask: Ask, ask_secret: Ask) -> None:
        self.values = dict(current)
        self._ask = ask
        self._ask_secret = ask_secret

    def text(self, key: str, prompt: str, default: str = "", required: bool = True) -> str:
        default = self.values.get(key) or default
        while True:
            suffix = f" [{default}]" if default else ""
            answer = self._ask(f"{prompt}{suffix}: ").strip() or default
            if answer or not required:
                self.values[key] = answer
                return answer
            print("  This one is required.")

    def secret(self, key: str, prompt: str, required: bool = True) -> str:
        current = self.values.get(key, "")
        while True:
            hint = " [already set, Enter keeps it]" if current else ""
            answer = self._ask_secret(f"{prompt}{hint}: ").strip() or current
            if answer or not required:
                self.values[key] = answer
                return answer
            print("  This one is required.")

    def yes(self, prompt: str, default: bool) -> bool:
        options = "Y/n" if default else "y/N"
        answer = self._ask(f"{prompt} [{options}]: ").strip().lower()
        return default if not answer else answer.startswith("y")

    def choice(self, key: str, prompt: str, options: list[str]) -> str:
        default = self.values.get(key) if self.values.get(key) in options else options[0]
        while True:
            answer = self._ask(f"{prompt} ({'/'.join(options)}) [{default}]: ").strip().lower()
            answer = answer or default
            if answer in options:
                self.values[key] = answer
                return answer
            print(f"  Pick one of: {', '.join(options)}")

    def run(self) -> dict[str, str]:
        print("\n1/4  Your agent")
        self.text("AGENT_WEBHOOK_URL", "Agent URL the bridge POSTs messages to")
        self.secret("AGENT_AUTH_TOKEN", "Bearer token for your agent (optional)", required=False)

        print("\n2/4  Public address")
        print("  The HTTPS URL Meta/Twilio use to reach this service (e.g. your ngrok URL).")
        self.text("PUBLIC_BASE_URL", "Public base URL")

        print("\n3/4  Channels (pick at least one)")
        while True:
            meta = self.yes(
                "Use WhatsApp Cloud API (Meta)?", bool(self.values.get("META_ACCESS_TOKEN"))
            )
            twilio = self.yes("Use Twilio?", bool(self.values.get("TWILIO_ACCOUNT_SID")))
            if meta or twilio:
                break
            print("  Choose at least one channel.")
        if meta:
            self.secret("META_ACCESS_TOKEN", "Meta access token")
            if not self.values.get("META_VERIFY_TOKEN"):
                self.values["META_VERIFY_TOKEN"] = secrets.token_urlsafe(24)
            self.secret("META_APP_SECRET", "Meta app secret")
        else:
            for key in ("META_ACCESS_TOKEN", "META_VERIFY_TOKEN", "META_APP_SECRET"):
                self.values[key] = ""
        if twilio:
            self.text("TWILIO_ACCOUNT_SID", "Twilio Account SID (starts with AC)")
            self.secret("TWILIO_AUTH_TOKEN", "Twilio Auth Token")
        else:
            self.values["TWILIO_ACCOUNT_SID"] = self.values["TWILIO_AUTH_TOKEN"] = ""

        print("\n4/4  Speech")
        stt = self.choice("STT_PROVIDER", "Speech-to-text", ["openai", "azure", "deepgram"])
        tts = self.choice("TTS_PROVIDER", "Text-to-speech", ["openai", "azure", "elevenlabs"])
        if "openai" in (stt, tts):
            self.secret("OPENAI_API_KEY", "OpenAI API key")
        if "azure" in (stt, tts):
            self.text(
                "AZURE_OPENAI_ENDPOINT", "Azure OpenAI endpoint (https://<name>.openai.azure.com/)"
            )
            self.secret("AZURE_OPENAI_API_KEY", "Azure OpenAI key")
            if stt == "azure":
                self.text(
                    "AZURE_OPENAI_STT_DEPLOYMENT", "Azure speech-to-text deployment name", "whisper"
                )
            if tts == "azure":
                self.text(
                    "AZURE_OPENAI_TTS_DEPLOYMENT",
                    "Azure text-to-speech deployment name",
                    "gpt-4o-mini-tts",
                )
        if stt == "deepgram":
            self.secret("DEEPGRAM_API_KEY", "Deepgram API key")
        if tts == "elevenlabs":
            self.secret("ELEVENLABS_API_KEY", "ElevenLabs API key")
            self.text("ELEVENLABS_VOICE_ID", "ElevenLabs voice ID")
        return self.values


def main(
    ask: Ask = input,
    ask_secret: Ask = getpass.getpass,
    check: Callable[[str, dict[str, str]], str | None] = http_check,
    env_file: Path = ENV_FILE,
) -> int:
    print("agent-voice-bridge setup — answers go to", env_file)
    current = read_env(env_file)
    wizard = Wizard(current, ask, ask_secret)
    if current and not wizard.yes(".env exists. Update it (current values become defaults)?", True):
        print("Nothing changed.")
        return 0

    values = wizard.run()

    checks = credential_checks(values)
    if checks and wizard.yes("\nCheck these keys against the real services now?", True):
        failed = False
        for label, url, headers in checks:
            problem = check(url, headers)
            print(f"  {'✗' if problem else '✓'} {label}" + (f": {problem}" if problem else ""))
            failed = failed or problem is not None
        if failed and not wizard.yes("Some checks failed. Save anyway?", False):
            print("Nothing saved. Re-run to try again.")
            return 1

    env_file.write_text(render_env(TEMPLATE.read_text(), values))
    os.chmod(env_file, 0o600)

    base = values["PUBLIC_BASE_URL"].rstrip("/")
    print(f"\nSaved {env_file} (readable only by you).\n\nNext steps:")
    if values.get("META_ACCESS_TOKEN"):
        print(f"  Meta webhook URL:    {base}/webhook/meta")
        print(f"  Meta verify token:   {values['META_VERIFY_TOKEN']}")
    if values.get("TWILIO_ACCOUNT_SID"):
        print(f"  Twilio webhook URL:  {base}/webhook/twilio  (HTTP POST)")
    print("  Start:               docker compose up -d --build")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled. Nothing saved.")
        sys.exit(1)
