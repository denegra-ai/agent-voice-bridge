"""LangGraph agent for agent-voice-bridge, with one conversation per WhatsApp sender.

    pip install -r requirements.txt
    export OPENAI_API_KEY=sk-...
    uvicorn agent:app --port 9000

Then set AGENT_WEBHOOK_URL=http://localhost:9000/agent in the bridge's .env
(http://host.docker.internal:9000/agent when the bridge runs in Docker).
"""

import os
import secrets
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from pydantic import BaseModel

SYSTEM_PROMPT = (
    "You answer WhatsApp messages for a business. Your reply may be read aloud as a voice "
    "note, so keep it short and conversational, in the customer's language, without markdown."
)

model = ChatOpenAI(model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"))
HISTORY_MESSAGES = 20  # the model sees the last 10 exchanges, so long chats stay cheap


async def respond(state: MessagesState) -> dict:
    history = state["messages"][-HISTORY_MESSAGES:]
    reply = await model.ainvoke([("system", SYSTEM_PROMPT), *history])
    return {"messages": [reply]}


builder = StateGraph(MessagesState)
builder.add_node("respond", respond)
builder.add_edge(START, "respond")
# InMemorySaver forgets everything on restart; use a database checkpointer in production.
graph = builder.compile(checkpointer=InMemorySaver())


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
    result = await graph.ainvoke(
        {"messages": [("user", msg.content)]},
        config={"configurable": {"thread_id": f"{msg.channel}:{msg.sender_id}"}},
    )
    return {"reply_text": result["messages"][-1].text}
