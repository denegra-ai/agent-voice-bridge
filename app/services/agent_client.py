import asyncio
import logging
import time
from typing import Literal

import httpx
from pydantic import BaseModel

from app.config import Settings

logger = logging.getLogger(__name__)

# Only statuses where the agent has not processed the message. A 500 or a read timeout may
# mean the agent already acted, so retrying could make it act twice.
RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
RETRYABLE_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


class AgentRequest(BaseModel):
    sender_id: str
    message_type: Literal["voice", "text"]
    content: str
    channel: Literal["meta", "twilio"]


class AgentResponse(BaseModel):
    reply_text: str = ""


async def ask_agent(http: httpx.AsyncClient, settings: Settings, request: AgentRequest) -> str:
    headers = {}
    if settings.agent_auth_token is not None:
        headers["Authorization"] = f"Bearer {settings.agent_auth_token.get_secret_value()}"

    attempt = 0
    while True:
        started = time.perf_counter()
        try:
            response = await http.post(
                str(settings.agent_webhook_url),
                json=request.model_dump(),
                headers=headers,
                timeout=settings.agent_timeout_seconds,
            )
            if (
                response.status_code not in RETRYABLE_STATUS
                or attempt >= settings.agent_max_retries
            ):
                response.raise_for_status()
                reply = AgentResponse.model_validate(response.json()).reply_text
                logger.info(
                    "Agent replied to %s/%s in %.0f ms (attempt %d)",
                    request.channel,
                    request.message_type,
                    (time.perf_counter() - started) * 1000,
                    attempt + 1,
                )
                return reply
            reason = f"HTTP {response.status_code}"
        except RETRYABLE_ERRORS as exc:
            if attempt >= settings.agent_max_retries:
                raise
            reason = type(exc).__name__

        attempt += 1
        delay = settings.agent_retry_backoff_seconds * 2 ** (attempt - 1)
        logger.warning("Agent call failed (%s); retry %d in %.1fs", reason, attempt, delay)
        await asyncio.sleep(delay)
