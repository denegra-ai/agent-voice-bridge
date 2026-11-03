import logging
import mimetypes
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import Settings, get_settings
from app.routers import meta, twilio

# Slim images ship without /etc/mime.types; WhatsApp rejects audio served with the wrong type.
mimetypes.add_type("audio/ogg", ".ogg")
mimetypes.add_type("audio/mpeg", ".mp3")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        timeout = httpx.Timeout(30.0, connect=5.0)
        limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
        async with httpx.AsyncClient(timeout=timeout, limits=limits) as http:
            app.state.http = http
            yield

    app = FastAPI(title="agent-voice-bridge", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.seen_ids = OrderedDict()

    settings.audio_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static/audio", StaticFiles(directory=settings.audio_dir), name="audio")

    channels = []
    if settings.meta_enabled:
        app.include_router(meta.router)
        channels.append("meta")
    if settings.twilio_enabled:
        app.include_router(twilio.router)
        channels.append("twilio")

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, object]:
        return {"status": "ok", "version": __version__, "channels": channels}

    return app
