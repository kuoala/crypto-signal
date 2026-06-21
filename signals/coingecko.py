"""
Source 1: CoinGecko — Trending coins + Top Gainers/Losers (free, no API key)
"""

import requests

HEADERS = {"Accept": "application/json", "User-Agent": "TelegramSignalBot/1.0"}


def get_trending():
    """Top 7 trending coins on CoinGecko in last 24h."""
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/search/trending",
            headers=HEADERS, timeout=10
        )
        r.raise_for_status()
        coins = r.json().get("coins", [])[:5]
        lines = []
        for item in coins:
            c = item["item"]
            lines.append(f"  #{c['market_cap_rank']} {c['symbol'].upper()} — {c['name']}")
        return "\n".join(lines) if lines else None
    except Exception as e:
        return None


def get_top_movers():
    """Top 3 gainers and losers in last 24h from top-250 coins."""
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 250,
                "page": 1,
                "price_change_percentage": "24h",
            },
            headers=HEADERS, timeout=10
        )
        r.raise_for_status()
        coins = r.json()
        coins = [c for c in coins if c.get("price_change_percentage_24h") is not None]
        sorted_coins = sorted(coins, key=lambda x: x["price_change_percentage_24h"], reverse=True)

        gainers = sorted_coins[:3]
        losers = sorted_coins[-3:][::-1]

        g_lines = [f"  🟢 {c['symbol'].upper()} +{c['price_change_percentage_24h']:.1f}% (${c['current_price']:,.4g})" for c in gainers]
        l_lines = [f"  🔴 {c['symbol'].upper()} {c['price_change_percentage_24h']:.1f}% (${c['current_price']:,.4g})" for c in losers]

        return {"gainers": "\n".join(g_lines), "losers": "\n".join(l_lines)}
    except Exception:
        return None


def get_btc_eth_prices():
    """Current BTC and ETH prices with 24h change."""
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": "bitcoin,ethereum",
                "vs_currencies": "usd",
                "include_24hr_change": "true",
            },
            headers=HEADERS, timeout=10
        )
        r.raise_for_status()
        data = r.json()
        btc = data.get("bitcoin", {})
        eth = data.get("ethereum", {})
        result = {}
        if btc:
            chg = btc.get("usd_24h_change", 0)
            icon = "🟢" if chg >= 0 else "🔴"
            result["btc"] = f"BTC ${btc['usd']:,.0f} {icon} {chg:+.2f}%"
        if eth:
            chg = eth.get("usd_24h_change", 0)
            icon = "🟢" if chg >= 0 else "🔴"
            result["eth"] = f"ETH ${eth['usd']:,.0f} {icon} {chg:+.2f}%"
        return result
    except Exception:
        return None
