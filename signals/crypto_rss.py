"""
Free crypto news via RSS/Atom feeds — no API key required.
Sources: CoinDesk, CoinTelegraph, Decrypt, The Block, Bitcoin Magazine
"""

import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

HEADERS = {"User-Agent": "TelegramSignalBot/1.0"}

# All free, no auth required
RSS_SOURCES = [
    {
        "name": "CoinDesk",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "trusted": True,
    },
    {
        "name": "CoinTelegraph",
        "url": "https://cointelegraph.com/rss",
        "trusted": True,
    },
    {
        "name": "Decrypt",
        "url": "https://decrypt.co/feed",
        "trusted": True,
    },
    {
        "name": "The Block",
        "url": "https://www.theblock.co/rss.xml",
        "trusted": True,
    },
    {
        "name": "Bitcoin Magazine",
        "url": "https://bitcoinmagazine.com/feed",
        "trusted": True,
    },
]

# XML namespaces that may appear in feeds
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def _parse_date(date_str: str) -> str:
    """Normalise any RFC-2822 or ISO date string to UTC ISO format."""
    if not date_str:
        return ""
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return date_str


def _fetch_feed(source: dict) -> list[dict]:
    """Fetch and parse one RSS/Atom feed. Returns list of news items."""
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=10)
        r.raise_for_status()
        root = ET.fromstring(r.content)

        items = []

        # --- RSS 2.0 ---
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = _parse_date(item.findtext("pubDate") or "")
            if title:
                items.append({
                    "headline": title,
                    "published_at": pub_date,
                    "source_name": source["name"],
                    "url": link,
                    "trusted": source["trusted"],
                })

        # --- Atom ---
        if not items:
            for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
                title_el = entry.find("{http://www.w3.org/2005/Atom}title")
                link_el = entry.find("{http://www.w3.org/2005/Atom}link")
                updated_el = entry.find("{http://www.w3.org/2005/Atom}updated")
                title = (title_el.text or "").strip() if title_el is not None else ""
                link = link_el.get("href", "") if link_el is not None else ""
                pub_date = (updated_el.text or "") if updated_el is not None else ""
                if title:
                    items.append({
                        "headline": title,
                        "published_at": pub_date,
                        "source_name": source["name"],
                        "url": link,
                        "trusted": source["trusted"],
                    })

        return items

    except Exception:
        return []


def get_breaking_news(limit: int = 10) -> list[dict]:
    """
    Fetch latest news from all RSS sources and return the most recent items
    sorted by publication time (newest first).

    Returns:
        List of dicts: headline, published_at, source_name, url, trusted
    """
    all_items: list[dict] = []
    for source in RSS_SOURCES:
        items = _fetch_feed(source)
        all_items.extend(items[:5])  # cap per source to spread diversity

    # Sort by published_at descending (ISO strings sort correctly)
    all_items.sort(key=lambda x: x.get("published_at", ""), reverse=True)

    return all_items[:limit]
