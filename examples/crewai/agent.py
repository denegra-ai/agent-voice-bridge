"""CrewAI agent for agent-voice-bridge: a one-agent crew that writes the WhatsApp reply.

    pip install -r requirements.txt
    export OPENAI_API_KEY=sk-...
    uvicorn agent:app --port 9000

Then set AGENT_WEBHOOK_URL=http://localhost:9000/agent in the bridge's .env
(http://host.docker.internal:9000/agent when the bridge runs in Docker).
This crew has no memory between messages; each one is answered on its own.
"""

import os
import secrets
from typing import Literal

from crewai import Agent, Crew, Task
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")


def make_crew() -> Crew:
    # Agents and crews keep state while they run, so each message gets its own.
    assistant = Agent(
        role="WhatsApp customer assistant",
        goal="Answer each customer message helpfully and briefly",
        backstory="You answer WhatsApp messages for a small business.",
        llm=MODEL,
    )
    reply_task = Task(
        # {message} is filled from kickoff inputs, never formatted into the prompt by hand.
        description="A customer sent this WhatsApp message: {message}\nWrite the reply.",
        expected_output=(
            "A short, friendly reply in the customer's language, plain text without markdown, "
            "suitable to be read aloud as a voice note."
        ),
        agent=assistant,
    )
    return Crew(agents=[assistant], tasks=[reply_task])


class BridgeMessage(BaseModel):
    sender_id: str
    message_type: Literal["voice", "text"]
    content: str
    channel: Literal["meta", "twilio"]


app = FastAPI()
AUTH_TOKEN = os.environ.get("AGENT_AUTH_TOKEN")


@app.post("/agent")
async def agent(msg: BridgeMessage, authorization: str = Header(default="")) -> dict:
    expected = f"Bearer {AUTH_TOKEN}".encode()
    if AUTH_TOKEN and not secrets.compare_digest(authorization.encode(), expected):
        raise HTTPException(status_code=401)
    result = await make_crew().kickoff_async(inputs={"message": msg.content})
    return {"reply_text": result.raw}
