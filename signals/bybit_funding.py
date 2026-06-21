"""
Source 8: Bybit Public API — Funding rates for perpetual contracts (free, no API key)
High positive funding = longs paying shorts = potential reversal signal
High negative funding = shorts paying longs = potential squeeze signal
"""

import requests

HEADERS = {"User-Agent": "TelegramSignalBot/1.0"}
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]


def get_funding_rates():
    """Get current funding rates. Flag extreme values as signals."""
    lines = []
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/funding/history",
            params={"category": "linear", "symbol": "BTCUSDT", "limit": 1},
            headers=HEADERS, timeout=10
        )
        r.raise_for_status()
    except Exception:
        return None

    for symbol in SYMBOLS:
        try:
            r = requests.get(
                "https://api.bybit.com/v5/market/tickers",
                params={"category": "linear", "symbol": symbol},
                headers=HEADERS, timeout=10
            )
            r.raise_for_status()
            data = r.json()
            ticker_list = data.get("result", {}).get("list", [])
            if not ticker_list:
                continue
            ticker = ticker_list[0]
            funding = float(ticker.get("fundingRate", 0)) * 100  # as %

            ticker_name = symbol.replace("USDT", "")

            if funding >= 0.1:
                signal = "🔴 HIGH (shorts may squeeze soon)"
            elif funding >= 0.05:
                signal = "🟠 Elevated (bullish bias)"
            elif funding <= -0.05:
                signal = "🟢 NEGATIVE (longs may squeeze soon)"
            else:
                signal = "⚪ Neutral"

            lines.append(f"  {ticker_name}: {funding:+.4f}% → {signal}")
        except Exception:
            continue

    return "\n".join(lines) if lines else None
