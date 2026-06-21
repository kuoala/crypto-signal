"""
Source 10: Messari — Asset metrics + market data (free public endpoints, no key needed)
"""

import requests

HEADERS = {
    "User-Agent": "TelegramSignalBot/1.0",
    "Accept": "application/json",
}

ASSETS = ["bitcoin", "ethereum", "solana"]


def get_metrics():
    """Get key metrics for top assets from Messari."""
    results = []
    for asset in ASSETS:
        try:
            r = requests.get(
                f"https://data.messari.io/api/v1/assets/{asset}/metrics",
                headers=HEADERS, timeout=10
            )
            r.raise_for_status()
            data = r.json().get("data", {})
            market = data.get("market_data", {})
            roi = data.get("roi_data", {})
            on_chain = data.get("on_chain_data", {})

            symbol = data.get("symbol", asset.upper())
            price = market.get("price_usd", 0)
            vol_change = market.get("volume_change_24h", 0)
            price_change_7d = market.get("percent_change_usd_last_7_days", 0)
            tx_count = on_chain.get("txn_count_last_24_hours", 0)

            icon = "🟢" if price_change_7d >= 0 else "🔴"
            line = f"  {symbol}: 7d {icon}{abs(price_change_7d):.1f}%"
            if tx_count:
                line += f" | TXs: {int(tx_count):,}"
            if vol_change:
                line += f" | Vol Δ: {vol_change:+.1f}%"
            results.append(line)
        except Exception:
            continue

    return "\n".join(results) if results else None
