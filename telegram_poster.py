"""
Telegram message poster — sends formatted signal messages to a channel.
Requires env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID
"""

import os
import requests
import time

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")

API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the Telegram channel. Returns True on success."""
    if not BOT_TOKEN or not CHANNEL_ID:
        print("ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID not set.")
        return False

    url = f"{API_BASE}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code == 200:
            print("✅ Message sent successfully.")
            return True
        else:
            print(f"❌ Failed to send: {r.status_code} — {r.text}")
            # If message too long, split and retry
            if r.status_code == 400 and "message is too long" in r.text.lower():
                return send_long_message(text, parse_mode)
            return False
    except Exception as e:
        print(f"❌ Exception sending message: {e}")
        return False


def send_long_message(text: str, parse_mode: str = "HTML") -> bool:
    """Split messages longer than 4096 chars and send in chunks."""
    MAX_LEN = 4000
    chunks = []
    while len(text) > MAX_LEN:
        split_at = text[:MAX_LEN].rfind("\n")
        if split_at == -1:
            split_at = MAX_LEN
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    chunks.append(text)

    success = True
    for i, chunk in enumerate(chunks):
        if i > 0:
            time.sleep(1)  # rate limit
        success = success and send_message(chunk, parse_mode)
    return success


def test_connection() -> bool:
    """Verify bot token works."""
    try:
        r = requests.get(f"{API_BASE}/getMe", timeout=10)
        if r.status_code == 200:
            bot = r.json()["result"]
            print(f"✅ Bot connected: @{bot['username']}")
            return True
        else:
            print(f"❌ Bot token invalid: {r.text}")
            return False
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return False
