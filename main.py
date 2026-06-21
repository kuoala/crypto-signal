"""
Crypto Signal Bot — Main Orchestrator
Aggregates signals from 10 free sources and posts to Telegram every 4 hours.

Usage:
  python main.py              # Run and post to Telegram
  python main.py --dry-run    # Print message without posting
"""

import sys
from datetime import datetime, timezone

from telegram_poster import send_message, test_connection

from signals.coingecko import get_trending, get_top_movers, get_btc_eth_prices, get_trending_hashtags
from signals.fear_greed import get_fear_greed
from signals.binance_rsi import get_rsi_signals, get_volume_spikes
from signals.coinpaprika import get_global_market, get_top_coins_24h
from signals.reddit_sentiment import get_reddit_sentiment
from signals.cryptocompare_news import get_latest_news
from signals.coindar_events import get_upcoming_events
from signals.bybit_funding import get_funding_rates
from signals.coinglass_oi import get_oi_from_binance
from signals.messari import get_metrics

DRY_RUN = "--dry-run" in sys.argv


def section(title: str, content: str | None) -> str:
    """Format a signal section. Returns empty string if content is None."""
    if not content:
        return ""
    return f"\n{title}\n{content}\n"


def build_message() -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print("📡 Fetching signals from 10 sources...")

    lines = [f"<b>🚨 CRYPTO SIGNAL REPORT</b>"]
    lines.append(f"<i>{now}</i>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")

    # --- Source 1 & 4: Market Overview ---
    print("  [1/10] CoinGecko prices...")
    prices = get_btc_eth_prices()
    if prices:
        price_str = "\n".join([f"  {v}" for v in prices.values()])
        lines.append(section("💰 <b>Market Prices</b>", price_str))

    print("  [2/10] CoinPaprika global stats...")
    global_market = get_global_market()
    if global_market:
        lines.append(section("🌍 <b>Global Market</b>", global_market))

    # --- Source 2: Fear & Greed ---
    print("  [3/10] Fear & Greed Index...")
    fg = get_fear_greed()
    if fg:
        lines.append(section("🧠 <b>Fear & Greed Index</b>", f"  {fg['display']}"))

    # --- Source 3: RSI Signals ---
    print("  [4/10] Binance RSI (4h)...")
    rsi = get_rsi_signals()
    if rsi:
        lines.append(section("📊 <b>RSI Signals (4h)</b>", rsi))

    # --- Volume Spikes ---
    print("  [5/10] Volume spikes...")
    vol = get_volume_spikes()
    if vol:
        lines.append(section("⚡ <b>Volume Spikes</b>", vol))

    # --- Source 5: Reddit Sentiment ---
    print("  [6/10] Reddit sentiment...")
    reddit = get_reddit_sentiment()
    if reddit:
        lines.append(section("💬 <b>Community Sentiment (r/crypto)</b>", reddit))

    # --- Source 8: Funding Rates ---
    print("  [7/10] Bybit funding rates...")
    funding = get_funding_rates()
    if funding:
        lines.append(section("💸 <b>Funding Rates (Perps)</b>", funding))

    # --- Source 9: Open Interest ---
    print("  [8/10] Open Interest (Binance Futures)...")
    oi = get_oi_from_binance()
    if oi:
        lines.append(section("📈 <b>Open Interest</b>", oi))

    # --- Source 1 (trending) + Source 4 (gainers) ---
    print("  [9/10] Trending & top movers...")
    trending = get_trending()
    if trending:
        lines.append(section("🔥 <b>Trending Coins</b>", trending))

    movers = get_top_movers()
    if movers:
        lines.append(section("🏆 <b>Top Gainers</b>", movers["gainers"]))
        lines.append(section("💀 <b>Top Losers</b>", movers["losers"]))

    # --- Source 10: Messari 7d metrics ---
    print("  [10/10] Messari 7d metrics...")
    messari = get_metrics()
    if messari:
        lines.append(section("📉 <b>7-Day Performance</b>", messari))

    # --- News ---
    news = get_latest_news()
    if news:
        lines.append(section("📰 <b>Top Crypto News</b>", news))

    # --- Events ---
    events = get_upcoming_events()
    if events:
        lines.append(section("📅 <b>Upcoming Events</b>", events))

    # Trending hashtags
    hashtags = get_trending_hashtags()
    lines.append(f"\n{hashtags}")

    # Footer
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(
        "<i>⚠️ Not financial advice. DYOR. Signals are aggregated from public data sources.</i>"
    )

    return "\n".join([l for l in lines if l])


def main():
    print("=" * 50)
    print("  CRYPTO SIGNAL BOT")
    print("=" * 50)

    if not DRY_RUN:
        if not test_connection():
            print("Aborting — fix bot token.")
            sys.exit(1)

    message = build_message()

    if DRY_RUN:
        print("\n--- DRY RUN OUTPUT ---")
        # Print plain text version
        import re
        plain = re.sub(r"<[^>]+>", "", message)
        print(plain)
        print("--- END ---")
    else:
        success = send_message(message)
        if not success:
            sys.exit(1)

    print("✅ Done.")


if __name__ == "__main__":
    main()
