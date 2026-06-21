"""
Source 4: CoinPaprika — Global market stats + top movers (free, no API key)
"""

import requests

HEADERS = {"User-Agent": "TelegramSignalBot/1.0"}


def get_global_market():
    """Get global crypto market stats."""
    try:
        r = requests.get(
            "https://api.coinpaprika.com/v1/global",
            headers=HEADERS, timeout=10
        )
        r.raise_for_status()
        data = r.json()
        mcap = data.get("market_cap_usd", 0)
        volume = data.get("volume_24h_usd", 0)
        btc_dom = data.get("bitcoin_dominance_percentage", 0)
        mcap_change = data.get("market_cap_change_24h", 0)
        icon = "🟢" if mcap_change >= 0 else "🔴"

        return (
            f"  💰 Total Market Cap: ${mcap/1e12:.2f}T {icon} {mcap_change:+.1f}%\n"
            f"  📊 24h Volume: ${volume/1e9:.1f}B\n"
            f"  ₿ BTC Dominance: {btc_dom:.1f}%"
        )
    except Exception:
        return None


def get_top_coins_24h():
    """Top 5 coins by percentage gain in last 24h from top-50."""
    try:
        r = requests.get(
            "https://api.coinpaprika.com/v1/tickers",
            params={"limit": 50},
            headers=HEADERS, timeout=15
        )
        r.raise_for_status()
        tickers = r.json()
        tickers = [t for t in tickers if t.get("quotes", {}).get("USD", {}).get("percent_change_24h") is not None]
        tickers.sort(key=lambda x: x["quotes"]["USD"]["percent_change_24h"], reverse=True)
        top = tickers[:3]
        lines = []
        for t in top:
            chg = t["quotes"]["USD"]["percent_change_24h"]
            price = t["quotes"]["USD"]["price"]
            lines.append(f"  🚀 {t['symbol']} +{chg:.1f}% @ ${price:,.4g}")
        return "\n".join(lines) if lines else None
    except Exception:
        return None
