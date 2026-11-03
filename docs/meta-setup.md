# Meta WhatsApp Cloud API setup

This guide connects `agent-voice-bridge` to WhatsApp through Meta's Cloud API, using Meta's
free test number. You need a Facebook account and about 15 minutes.

You will collect three values for your `.env`:

| Variable | Where it comes from |
|---|---|
| `META_ACCESS_TOKEN` | Step 2 (test token) or step 7 (permanent token) |
| `META_APP_SECRET` | Step 4 |
| `META_VERIFY_TOKEN` | You make it up (step 5) |

The phone number ID is not needed: the bridge reads it from each incoming webhook and replies
from the same number.

## 1. Create the app

1. Go to [developers.facebook.com](https://developers.facebook.com) → **My Apps** → **Create App**.
2. **App details:** enter an app name and a contact email → **Next**.
3. **Use cases:** tick **Connect with customers through WhatsApp** → **Next**.
4. **Business:** pick a business portfolio (WhatsApp requires one). An unverified portfolio is
   fine for testing.
5. **Requirements:** nothing to do → **Next** → **Create app**. Meta may ask for your Facebook
   password again.

## 2. Claim the test number and generate a token

1. On the app dashboard, open **Use cases** → **Connect with customers through WhatsApp** →
   **Customize** → **Step 1. Try it out**.
2. Click **Claim test number**. You get a free `+1 555` number, a **Phone Number ID** and a
   **WhatsApp Business Account ID**.
3. Next to *Access token*, click **Generate token**. In the popup:
   - choose **Opt in to current WhatsApp accounts only** (the default grants access to *all
     current and future* WhatsApp accounts in your portfolio), and
   - tick only **Test Messaging Account**.

This token expires after about 24 hours. It is fine for a first test; use step 7 for anything
that should keep running.

## 3. Add your phone as a recipient

While the app is in development, the test number can only message numbers you list.

1. Still on **Step 1. Try it out**, open the **Recipient** dropdown → **Manage phone number list**.
2. Add your WhatsApp number with its country code. Meta sends a 5-digit code to it on
   WhatsApp; enter it. You can add up to five numbers.
3. Pick your number as the recipient, choose the **Hello World** template and click **Send
   message**. This opens the chat on your phone, so you can reply to the test number later.

If a reply later fails with error `131030`, the number you wrote from is not on this list.

## 4. Copy the app secret

**App settings** → **Basic** → **App secret** → **Show** (Meta asks for your password again).
This is `META_APP_SECRET`. The bridge uses it to check the `X-Hub-Signature-256` header on every
webhook and rejects anything unsigned with `403`.

## 5. Start the bridge and a public URL

Pick a verify token, any random string you keep secret:

```bash
openssl rand -hex 16
```

Put the three values in `.env` (or run `python3 scripts/configure.py`, which also generates the
verify token), set `PUBLIC_BASE_URL` to your public HTTPS URL, and start the bridge as described
in the [Quickstart](../README.md#quickstart-docker-3-minutes). For local testing, a tunnel such
as `ngrok http 8000` or `cloudflared tunnel --url http://localhost:8000` gives you that URL.

## 6. Connect the webhook

1. In the use case, open **Step 2. Production setup** → **Configure Webhooks**. (The
   *Configurations* entry in the left menu belongs to Facebook Login, not WhatsApp.)
2. **Callback URL:** `https://<your-public-url>/webhook/meta`
3. **Verify token:** the value of `META_VERIFY_TOKEN`.
4. Click **Verify and save**. Meta calls your URL once; the bridge answers only if the token
   matches.
5. Under **Webhook fields**, set **messages** to **Subscribed**.

The **Test** button next to *messages* sends a signed sample to your bridge. It is a good smoke
test: you should see `200` in the bridge log. The reply to that sample fails with
`Object with ID '123456123' does not exist`, because the sample uses a fake phone number ID.
That is expected.

Now send a text and then a voice note from your phone to the test number.

## 7. Permanent token for production

The token from step 2 expires. For a server that keeps running, use a system user token:

1. [business.facebook.com](https://business.facebook.com) → **Business settings** →
   **Users** → **System users** → **Add**.
2. **Assign assets:** give the system user your app and your WhatsApp account.
3. **Generate token** for your app with the permissions `whatsapp_business_messaging` and
   `whatsapp_business_management`, and choose no expiry.
4. Put it in `META_ACCESS_TOKEN` and restart the bridge.

To use your own phone number instead of the test number, follow **Step 2. Production setup**
in the use case (add a number, verify it, add a payment method). Real customers can message you
only after that.

## Troubleshooting

| Symptom | Cause |
|---|---|
| *Verify and save* fails | The bridge is not reachable at the callback URL, or `META_VERIFY_TOKEN` differs from what you typed in Meta. |
| `403 Invalid signature` in the bridge log | `META_APP_SECRET` is wrong or belongs to another app. |
| "Your phone number is still being set up" | A freshly claimed test number can take a few minutes. Wait and send the Hello World template again. |
| Error `131030` | The recipient is not on the phone number list (step 3). |
| Error `190` / `OAuthException` | The access token expired. Generate a new one or use a system user token (step 7). |
| Dashboard *Test* works, real messages never arrive | Check that **messages** is subscribed, and that the app's WhatsApp account is not disabled (WhatsApp Manager shows a red **Account Disabled** banner; you can request a review from Business Support Home). |
| Free-form replies fail after a day of silence | WhatsApp allows free-form replies only within 24 hours of the user's last message. Outside that window you need an approved template. |

## A note on logs

Meta sends the verify token in the query string of the verification request, as both
`hub.verify_token` and `hub_verify_token`. Uvicorn's access log prints that URL, so anyone who
can read your logs can read the token. Filter both forms out of your logs (for example with
`sed -E 's/(hub[._]verify_token=)[^& "]*/\1REDACTED/g'`), or start uvicorn with
`--no-access-log`.
