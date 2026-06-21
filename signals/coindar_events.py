"""
Source 7: Coindar — Upcoming crypto events calendar (free API, no key needed)
"""

import requests
from datetime import datetime, timezone

HEADERS = {"User-Agent": "TelegramSignalBot/1.0"}


def get_upcoming_events():
    """Get upcoming crypto events in the next 3 days."""
    try:
        now = datetime.now(timezone.utc)
        r = requests.get(
            "https://coindar.org/api/v2/events",
            params={
                "access_token": "free",
                "sort_by": "date_event",
                "order_by": "asc",
                "page": 1,
                "page_size": 10,
            },
            headers=HEADERS, timeout=10
        )
        # Coindar free endpoint
        if r.status_code != 200:
            # Fallback to CoinGecko events (none in free tier), return gracefully
            return None

        events = r.json()
        if not isinstance(events, list):
            return None

        lines = []
        for event in events[:5]:
            caption = event.get("caption", "Unknown")[:60]
            coin = event.get("tags", [{}])[0].get("caption", "") if event.get("tags") else ""
            date_str = event.get("date_event", "")
            try:
                event_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                days_away = (event_date - now).days
                date_label = f"in {days_away}d" if days_away > 0 else "TODAY"
            except Exception:
                date_label = date_str[:10]

            prefix = f"[{coin}] " if coin else ""
            lines.append(f"  📅 {prefix}{caption} — {date_label}")

        return "\n".join(lines) if lines else "  No major events in the next 3 days"
    except Exception:
        return None
