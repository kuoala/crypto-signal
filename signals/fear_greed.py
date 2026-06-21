"""
Source 2: Alternative.me — Crypto Fear & Greed Index (free, no API key)
"""

import requests

HEADERS = {"User-Agent": "TelegramSignalBot/1.0"}


def get_fear_greed():
    """Returns current Fear & Greed index value + classification."""
    try:
        r = requests.get(
            "https://api.alternative.me/fng/?limit=1",
            headers=HEADERS, timeout=10
        )
        r.raise_for_status()
        data = r.json()["data"][0]
        value = int(data["value"])
        classification = data["value_classification"]

        if value <= 25:
            emoji = "😱"
        elif value <= 45:
            emoji = "😰"
        elif value <= 55:
            emoji = "😐"
        elif value <= 75:
            emoji = "😄"
        else:
            emoji = "🤑"

        signal = "BUY (Extreme Fear = opportunity)" if value <= 25 else \
                 "CAUTION (Fear zone)" if value <= 45 else \
                 "NEUTRAL" if value <= 55 else \
                 "CAUTION (Greed zone)" if value <= 75 else \
                 "SELL PRESSURE (Extreme Greed)"

        return {
            "value": value,
            "classification": classification,
            "emoji": emoji,
            "signal": signal,
            "display": f"{emoji} {value}/100 — {classification}\n  Signal: {signal}"
        }
    except Exception:
        return None
