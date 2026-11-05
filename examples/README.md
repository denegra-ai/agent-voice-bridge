# Example agents

Three agents that speak the bridge's [agent contract](../README.md#the-agent-contract): they take
`{sender_id, message_type, content, channel}` and answer `{reply_text}`. Start one, point
`AGENT_WEBHOOK_URL` at it, and message the bridge on WhatsApp.

| Example | What it shows | Agent URL |
|---|---|---|
| [`langgraph/`](langgraph/agent.py) | A LangGraph graph with one conversation per WhatsApp sender | `http://localhost:9000/agent` |
| [`crewai/`](crewai/agent.py) | A one-agent CrewAI crew that writes each reply | `http://localhost:9000/agent` |
| [`n8n/`](n8n/workflow.json) | An n8n workflow: Webhook → AI Agent (with memory) → Respond to Webhook | `http://localhost:5678/webhook/agent` |

All three use OpenAI for the model. Set `OPENAI_MODEL` (Python examples) or pick the model in the
n8n node to use another one. When the bridge runs in Docker, use `host.docker.internal` instead
of `localhost` in `AGENT_WEBHOOK_URL`.

**Azure OpenAI** works through its OpenAI-compatible v1 endpoint, with no code changes: set
`OPENAI_BASE_URL=https://<your-resource>.openai.azure.com/openai/v1`, your Azure key as
`OPENAI_API_KEY`, and your deployment name as `OPENAI_MODEL`. In n8n, put the same URL in the
OpenAI credential's **Base URL** field and type the deployment name as the model.

## LangGraph and CrewAI

```bash
cd examples/langgraph          # or examples/crewai
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...
uvicorn agent:app --port 9000
```

If you set `AGENT_AUTH_TOKEN` in the bridge, export the same value before starting the agent and
it will refuse calls without it.

The LangGraph example remembers each sender's conversation with `InMemorySaver`, which is lost on
restart; swap in a database checkpointer for production. The CrewAI example answers each message
on its own.

## n8n

1. In n8n, **Workflows → Import from File** and pick `n8n/workflow.json`.
2. Open **OpenAI Chat Model** and select (or create) your OpenAI credential.
3. **Activate** the workflow. Its production URL is `https://<your-n8n>/webhook/agent`.

The **Simple Memory** node keeps the last 10 messages per sender. To check the bridge's
`AGENT_AUTH_TOKEN`, set **Authentication → Header Auth** on the Webhook node with
`Authorization` / `Bearer <token>`.

## Try an agent without WhatsApp

```bash
curl -s http://localhost:9000/agent -H 'Content-Type: application/json' \
  -d '{"sender_id":"whatsapp:+905551112233","message_type":"text","content":"What time do you open tomorrow?","channel":"twilio"}'
```
