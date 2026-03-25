#!/usr/bin/env python3
"""
Register the API Gateway URL as the Telegram bot webhook.

Usage:
    python scripts/setup_telegram_webhook.py

Prerequisites:
    - TELEGRAM_BOT_TOKEN set in .env
    - WEBHOOK_URL set in .env (the WebhookURL CDK output, e.g.
      https://<id>.execute-api.us-east-1.amazonaws.com/prod/webhook)

The script calls the Telegram Bot API's setWebhook method and prints the
result.  If the webhook is already registered to a different URL it will be
updated.
"""
import os
import sys
import requests
from pathlib import Path
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).parent.parent
load_dotenv(REPO_ROOT / ".env.local")
load_dotenv(REPO_ROOT / ".env")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")

if not TOKEN:
    print("ERROR: TELEGRAM_BOT_TOKEN is not set in .env")
    print("  Create a bot via @BotFather on Telegram and paste the token into .env")
    sys.exit(1)

if not WEBHOOK_URL:
    print("ERROR: WEBHOOK_URL is not set in .env")
    print("  After running `cdk deploy`, copy the WebhookURL output into .env")
    sys.exit(1)

if not WEBHOOK_URL.startswith("https://"):
    print("ERROR: WEBHOOK_URL must start with https://")
    print("  Telegram only accepts HTTPS webhook URLs.")
    sys.exit(1)


def get_current_webhook(token: str) -> str:
    """Return the currently registered webhook URL (empty string if none)."""
    resp = requests.get(
        f"https://api.telegram.org/bot{token}/getWebhookInfo",
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("result", {}).get("url", "")


def set_webhook(token: str, url: str) -> dict:
    """Register a new webhook URL and return the Telegram API response."""
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json={
            "url": url,
            "max_connections": 40,
            "allowed_updates": ["message"],
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


# ── Current state ──────────────────────────────────────────────────────────
try:
    current = get_current_webhook(TOKEN)
except requests.RequestException as exc:
    print(f"ERROR: Failed to contact Telegram API: {exc}")
    sys.exit(1)

if current:
    if current == WEBHOOK_URL:
        print(f"Webhook is already registered to the correct URL:")
        print(f"  {WEBHOOK_URL}")
        sys.exit(0)
    else:
        print(f"Replacing existing webhook:")
        print(f"  Old: {current}")
        print(f"  New: {WEBHOOK_URL}")
else:
    print(f"Registering webhook:")
    print(f"  URL: {WEBHOOK_URL}")

# ── Register ───────────────────────────────────────────────────────────────
try:
    data = set_webhook(TOKEN, WEBHOOK_URL)
except requests.RequestException as exc:
    print(f"ERROR: HTTP request failed: {exc}")
    sys.exit(1)

if data.get("ok"):
    print("\n[OK] Webhook registered successfully!")
    print(f"     {data.get('description', '')}")
    print("\nYour bot is ready. Send it a message on Telegram to test.")
else:
    print(f"\n[FAIL] Telegram returned an error: {data}")
    sys.exit(1)
