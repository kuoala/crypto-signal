"""
Source 5: Reddit — r/cryptocurrency hot posts sentiment analysis (free, no API key)
Uses Reddit's JSON feed (no auth required for public data).
"""

import requests
import re
from collections import Counter

HEADERS = {"User-Agent": "TelegramSignalBot/1.0 (signal aggregator)"}

BULLISH_WORDS = {
    "moon", "bullish", "buy", "long", "rally", "breakout", "pump",
    "ath", "all-time high", "accumulate", "hodl", "support", "bounce",
    "uptrend", "green", "surge", "soar", "gain", "profit"
}
BEARISH_WORDS = {
    "bear", "bearish", "sell", "short", "dump", "crash", "drop",
    "fear", "panic", "loss", "correction", "downtrend", "red",
    "resistance", "decline", "fall", "collapse", "rug"
}

CRYPTO_SYMBOLS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX",
    "DOT", "LINK", "MATIC", "UNI", "ATOM", "LTC", "BCH"
]


def analyze_sentiment(text: str) -> tuple:
    text_lower = text.lower()
    bull = sum(1 for w in BULLISH_WORDS if w in text_lower)
    bear = sum(1 for w in BEARISH_WORDS if w in text_lower)
    return bull, bear


def get_reddit_sentiment():
    """Scrape r/cryptocurrency hot posts and analyze sentiment."""
    try:
        r = requests.get(
            "https://www.reddit.com/r/cryptocurrency/hot.json?limit=25",
            headers=HEADERS, timeout=10
        )
        r.raise_for_status()
        posts = r.json()["data"]["children"]

        total_bull = 0
        total_bear = 0
        mentioned_coins = Counter()

        for post in posts:
            data = post["data"]
            title = data.get("title", "")
            bull, bear = analyze_sentiment(title)
            total_bull += bull
            total_bear += bear

            for sym in CRYPTO_SYMBOLS:
                if re.search(r'\b' + sym + r'\b', title, re.IGNORECASE):
                    mentioned_coins[sym] += 1

        total = total_bull + total_bear
        if total == 0:
            sentiment_pct = 50
            sentiment_label = "NEUTRAL"
            emoji = "😐"
        else:
            sentiment_pct = int((total_bull / total) * 100)
            if sentiment_pct >= 65:
                sentiment_label = "BULLISH"
                emoji = "🐂"
            elif sentiment_pct >= 50:
                sentiment_label = "SLIGHTLY BULLISH"
                emoji = "🟡"
            elif sentiment_pct >= 35:
                sentiment_label = "SLIGHTLY BEARISH"
                emoji = "🟠"
            else:
                sentiment_label = "BEARISH"
                emoji = "🐻"

        top_mentioned = ", ".join([f"#{sym}" for sym, _ in mentioned_coins.most_common(3)])

        return (
            f"  {emoji} Community Sentiment: {sentiment_label} ({sentiment_pct}% bullish signals)\n"
            f"  📢 Most discussed: {top_mentioned or 'N/A'}"
        )
    except Exception:
        return None
