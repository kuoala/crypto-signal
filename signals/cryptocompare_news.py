"""
Source 6: CryptoCompare — Latest crypto news headlines (free tier, no key needed for basic)
"""

import requests

HEADERS = {"User-Agent": "TelegramSignalBot/1.0"}


def get_latest_news():
    """Fetch top 5 latest crypto news headlines."""
    try:
        r = requests.get(
            "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&sortOrder=popular",
            headers=HEADERS, timeout=10
        )
        r.raise_for_status()
        articles = r.json().get("Data", [])[:5]
        lines = []
        for a in articles:
            title = a.get("title", "")[:80]
            source = a.get("source_info", {}).get("name", "")
            lines.append(f"  📰 {title}… [{source}]")
        return "\n".join(lines) if lines else None
    except Exception:
        return None
