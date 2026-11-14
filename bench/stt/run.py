"""Send every benchmark clip through the bridge's own transcribe() and record the result.

    .venv/bin/python -m bench.stt.run --provider deepgram --run 1
    .venv/bin/python -m bench.stt.run --provider azure --run 1 --min-interval 21

Credentials come from the environment or .env, exactly as the bridge reads them
(DEEPGRAM_API_KEY; AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_STT_DEPLOYMENT;
OPENAI_API_KEY). --hint passes each clip's language; without it the provider detects the
language, which is how the bridge runs by default.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

from app.config import Settings
from app.services.transcription import transcribe

HERE = Path(__file__).parent
NOT_MEASURED_STATUS = {408, 429, 500, 502, 503, 504}
RATE_LIMIT_WAITS = (10, 30, 60)


def make_settings(provider: str, language: str | None) -> Settings:
    # Only speech-to-text runs here; the channel and TTS values just satisfy validation.
    placeholders = {"twilio_account_sid": "benchmark", "twilio_auth_token": "benchmark"}
    if provider != "elevenlabs":
        placeholders["elevenlabs_api_key"] = "benchmark"
    return Settings(  # type: ignore[call-arg]
        agent_webhook_url="http://benchmark.invalid/agent",
        public_base_url="http://benchmark.invalid",
        tts_provider="elevenlabs",
        elevenlabs_voice_id="benchmark",
        stt_provider=provider,
        stt_fallback_provider=None,  # measure the provider alone, whatever .env says
        stt_language=language,
        **placeholders,
    )


async def transcribe_clip(http: httpx.AsyncClient, settings: Settings, audio: bytes) -> dict:
    attempts = 0
    for wait in (*RATE_LIMIT_WAITS, None):
        attempts += 1
        started = time.perf_counter()
        try:
            text = await transcribe(http, settings, audio, "audio/ogg")
            latency = (time.perf_counter() - started) * 1000
            return {
                "status": "ok",
                "attempts": attempts,
                "latency_ms": round(latency),
                "text": text,
            }
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code != 429 or wait is None:
                status = "not_measured" if code in NOT_MEASURED_STATUS else "error"
                return {"status": status, "attempts": attempts, "detail": f"HTTP {code}"}
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return {"status": "not_measured", "attempts": attempts, "detail": type(exc).__name__}
        except ValueError as exc:
            return {"status": "error", "attempts": attempts, "detail": str(exc)}
        await asyncio.sleep(wait)
    raise AssertionError("the last attempt always returns")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--provider", required=True, choices=["deepgram", "azure", "openai", "elevenlabs"]
    )
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--hint", action="store_true", help="pass each clip's language")
    parser.add_argument("--min-interval", type=float, default=0, help="seconds between calls")
    parser.add_argument("--limit", type=int, default=0, help="first N clips only (dry run)")
    parser.add_argument("--name", help="result name, e.g. the model behind an Azure deployment")
    args = parser.parse_args()

    clips = [json.loads(line) for line in (HERE / "data" / "manifest.jsonl").open()]
    if args.limit:
        clips = clips[: args.limit]
    label = (args.name or args.provider) + ("+hint" if args.hint else "")
    out_dir = HERE / "results" / f"run{args.run}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{label}.jsonl"

    async with httpx.AsyncClient() as http:
        with out_path.open("w", encoding="utf-8") as out:
            for clip in clips:
                settings = make_settings(args.provider, clip["lang"] if args.hint else None)
                audio = (HERE / "data" / clip["file"]).read_bytes()
                started = time.monotonic()
                result = await transcribe_clip(http, settings, audio)
                row = {
                    "purpose": "eval_stt_benchmark",
                    "id": clip["id"],
                    "lang": clip["lang"],
                    "seconds": clip["seconds"],
                    "provider": label,
                    "run": args.run,
                    **result,
                }
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
                print(f"{clip['id']}: {result['status']}", flush=True)
                pause = args.min_interval - (time.monotonic() - started)
                if pause > 0:
                    await asyncio.sleep(pause)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
