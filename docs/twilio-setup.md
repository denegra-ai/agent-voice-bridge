# Twilio WhatsApp setup

This guide connects `agent-voice-bridge` to WhatsApp through Twilio. The free Twilio WhatsApp
Sandbox is enough for testing and takes about 10 minutes.

You will collect two values for your `.env`:

| Variable | Where it comes from |
|---|---|
| `TWILIO_ACCOUNT_SID` | Twilio Console home page, *Account Info* (starts with `AC`) |
| `TWILIO_AUTH_TOKEN` | Same place, click **Show** |

No sender number is configured: the bridge replies from whichever number the user wrote to.

## 1. Join the sandbox from your phone

1. Sign up at [twilio.com](https://www.twilio.com) and open the
   [Console](https://console.twilio.com).
2. Go to **Messaging** → **Try it out** → **Send a WhatsApp message**.
3. The page shows the sandbox number and a join phrase such as `join two-words`. Send exactly
   that phrase from your WhatsApp to the sandbox number. Twilio confirms that you joined.

Every phone that should talk to your agent has to join this way. Sandbox membership expires
after 72 hours; send the join phrase again when replies stop.

## 2. Start the bridge and a public URL

Put `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` in `.env` (or run
`python3 scripts/configure.py`), set `PUBLIC_BASE_URL` to your public HTTPS URL, and start the
bridge as described in the [Quickstart](../README.md#quickstart-docker-3-minutes). For local
testing, `ngrok http 8000` or `cloudflared tunnel --url http://localhost:8000` gives you that URL.

## 3. Point the sandbox at the bridge

1. On the same Twilio page, open the **Sandbox settings** tab.
2. **When a message comes in:** `https://<your-public-url>/webhook/twilio`, method **POST**.
3. Click **Save**.

Send a text and then a voice note to the sandbox number.

## How the signature check works

Twilio signs every webhook with your auth token over the exact URL it called. The bridge
rebuilds that URL from `PUBLIC_BASE_URL`, so:

- `PUBLIC_BASE_URL` must be the same scheme and host you typed into Twilio, for example
  `https://abc123.ngrok-free.app` for `https://abc123.ngrok-free.app/webhook/twilio`.
- When your tunnel URL changes, update **both** Twilio and `PUBLIC_BASE_URL`, then restart the
  bridge.

A mismatch shows up as `403 Invalid signature` in the bridge log and in Twilio's
**Monitor** → **Logs** → **Errors**.

## Going to production

The sandbox is for testing only. For real users, register your own WhatsApp sender:
**Messaging** → **Senders** → **WhatsApp senders** → **Create new sender**. Twilio walks you
through connecting a Meta business portfolio and verifying your number. Then set the same
*When a message comes in* URL on that sender.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `403 Invalid signature` | `PUBLIC_BASE_URL` differs from the URL in Twilio, or `TWILIO_AUTH_TOKEN` is wrong. |
| Error `63015` | The phone has not joined the sandbox, or its membership expired. Send the join phrase again. |
| Error `63016` | WhatsApp allows free-form replies only within 24 hours of the user's last message. Outside that window you need an approved template. |
| Voice note arrives as text | Text-to-speech failed (for example a missing TTS key or deployment), so the bridge fell back to a text reply. Check the bridge log. |
| Nothing happens at all | Check **Monitor** → **Logs** → **Errors** in Twilio; a `11200` error means Twilio could not reach your URL. |
