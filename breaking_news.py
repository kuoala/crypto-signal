"""
Breaking Crypto News — Standalone Telegram poster
Fetches top breaking news from free RSS feeds (CoinDesk, CoinTelegraph, Decrypt,
The Block, Bitcoin Magazine) + CryptoCompare, cross-validates sources, and posts
a formatted alert using the Breaking Crypto News template.

Usage:
  python breaking_news.py              # Post to Telegram
  python breaking_news.py --dry-run    # Print message without posting
  python breaking_news.py --top 3      # Post top N news items (default: 1)

Required env vars:
  TELEGRAM_BOT_TOKEN  — Telegram bot token
  TELEGRAM_CHANNEL_ID — Target channel/chat ID

LLM Prompt Template: templates/breaking_crypto_news_prompt.txt
"""

import re
import sys
import requests
from datetime import datetime, timezone

from telegram_poster import send_message, test_connection
from signals.crypto_rss import get_breaking_news

DRY_RUN = "--dry-run" in sys.argv

# Parse --top N argument
_top = 1
for i, arg in enumerate(sys.argv):
    if arg == "--top" and i + 1 < len(sys.argv):
        try:
            _top = int(sys.argv[i + 1])
        except ValueError:
            pass

HEADERS = {"User-Agent": "TelegramSignalBot/1.0"}


# ---------------------------------------------------------------------------
# CryptoCompare cross-validation
# ---------------------------------------------------------------------------

def _get_cryptocompare_news(limit: int = 15) -> list[dict]:
    """Fetch structured news list from CryptoCompare (free, no key needed)."""
    try:
        r = requests.get(
            "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&sortOrder=popular",
            headers=HEADERS, timeout=10,
        )
        r.raise_for_status()
        articles = r.json().get("Data", [])[:limit]
        return [
            {
                "headline": a.get("title", ""),
                "published_at": datetime.utcfromtimestamp(
                    a.get("published_on", 0)
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source_name": a.get("source_info", {}).get("name", a.get("source", "")),
                "url": a.get("url", ""),
            }
            for a in articles
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Headline matching
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower())


def _headlines_overlap(h1: str, h2: str, threshold: int = 3) -> bool:
    """True if two headlines share at least `threshold` significant words."""
    stopwords = {
        "a", "an", "the", "and", "or", "of", "in", "to", "for",
        "is", "are", "on", "at", "as", "by", "its", "it", "with",
    }
    w1 = {w for w in _normalize(h1).split() if w not in stopwords and len(w) > 2}
    w2 = {w for w in _normalize(h2).split() if w not in stopwords and len(w) > 2}
    return len(w1 & w2) >= threshold


# ---------------------------------------------------------------------------
# Source validation
# ---------------------------------------------------------------------------

def _determine_source_status(
    rss_item: dict, all_rss: list[dict], cc_items: list[dict]
) -> tuple[str, str]:
    """
    Apply template source priority rules and return (label, tag).

    Rules:
      1. Appears in 2+ RSS sources with overlapping headlines → Multi-Source Report
      2. Confirmed by CryptoCompare as well           → Multi-Source Report
      3. Single trusted RSS source                    → Unconfirmed Report
      4. Unknown source                               → Unconfirmed Report
    """
    headline = rss_item["headline"]
    source = rss_item["source_name"]

    # Check for cross-validation among other RSS sources
    other_rss = [x for x in all_rss if x["source_name"] != source]
    matched_rss = next(
        (x for x in other_rss if _headlines_overlap(headline, x["headline"])),
        None,
    )

    # Check CryptoCompare
    matched_cc = next(
        (cc for cc in cc_items if _headlines_overlap(headline, cc["headline"])),
        None,
    )

    if matched_rss or matched_cc:
        second = (matched_rss or matched_cc)["source_name"]
        return f"Multi-Source Report ({source} + {second})", "Multi-Source Report"

    # Single source — all our RSS sources are trusted publications
    if rss_item.get("trusted"):
        return f"Single Trusted Source ({source})", "Unconfirmed Report"

    return "Unconfirmed Report", "Unconfirmed Report"


# ---------------------------------------------------------------------------
# Message builder
# ---------------------------------------------------------------------------

def _build_single_alert(
    item: dict, all_rss: list[dict], cc_items: list[dict]
) -> str:
    headline = item["headline"]
    source_name = item["source_name"]
    url = item.get("url", "")

    status_label, status_tag = _determine_source_status(item, all_rss, cc_items)

    # Find the cross-validated source name for the "What happened?" line
    other_rss = [x for x in all_rss if x["source_name"] != source_name]
    matched_second = next(
        (x for x in other_rss if _headlines_overlap(headline, x["headline"])),
        None,
    ) or next(
        (cc for cc in cc_items if _headlines_overlap(headline, cc["headline"])),
        None,
    )

    if "Multi-Source" in status_tag:
        confirmation_prefix = "✅ Confirmed by multiple sources:"
        what_happened_note = (
            f"Reported by {source_name}"
            + (f" and {matched_second['source_name']}" if matched_second else "")
            + "."
        )
    else:
        confirmation_prefix = "⚠️ Unconfirmed report:"
        what_happened_note = (
            "This story is from one outlet and has not yet been confirmed "
            "by a second independent source."
        )

    display_headline = headline[:120] + ("…" if len(headline) > 120 else "")

    lines = [
        "🚨 <b>CRYPTO NEWS ALERT</b>",
        "",
        f"<b>{display_headline}</b>",
        "",
        "📌 <b>What happened?</b>",
        f"{confirmation_prefix} {headline}. {what_happened_note}".strip(),
        "",
        "🧠 <b>Why it matters:</b>",
        "This development may be relevant to participants in the crypto market. "
        "Always verify through official channels before acting.",
        "",
        "📈 <b>Potential market impact:</b>",
        "The market impact remains uncertain. Price movements depend on broader "
        "context and market conditions.",
        "",
        "👀 <b>What to watch next:</b>",
        "Monitor official announcements and on-chain data for further confirmation.",
        "",
        f"<i>Source status: {status_tag}</i>",
        f"<i>Via: {source_name}</i>" + (f" | <a href=\"{url}\">Read more</a>" if url else ""),
        "",
        "#CryptoNews #Bitcoin #Blockchain",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_breaking_news_messages(top: int = 1) -> list[str]:
    """Fetch news, cross-validate, and build Telegram alert messages."""
    print("📡 Fetching RSS feeds (CoinDesk, CoinTelegraph, Decrypt, The Block, Bitcoin Magazine)...")
    rss_items = get_breaking_news(limit=20)

    print("📡 Fetching CryptoCompare news for cross-validation...")
    cc_items = _get_cryptocompare_news(limit=15)

    if not rss_items:
        print("⚠️  No news retrieved from RSS feeds.")
        return []

    print(f"✅ {len(rss_items)} RSS items, {len(cc_items)} CryptoCompare items fetched.")

    messages = []
    for item in rss_items[:top]:
        msg = _build_single_alert(item, rss_items, cc_items)
        messages.append(msg)

    return messages


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    print("=" * 50)
    print("  BREAKING CRYPTO NEWS BOT")
    print("=" * 50)

    if not DRY_RUN:
        if not test_connection():
            print("Aborting — fix bot token.")
            sys.exit(1)

    messages = build_breaking_news_messages(top=_top)

    if not messages:
        print("Nothing to post.")
        sys.exit(0)

    for i, msg in enumerate(messages, 1):
        print(f"\n--- Alert {i}/{len(messages)} ---")
        if DRY_RUN:
            plain = re.sub(r"<[^>]+>", "", msg)
            print(plain)
        else:
            send_message(msg)

    print("\n✅ Done.")


if __name__ == "__main__":
    main()
