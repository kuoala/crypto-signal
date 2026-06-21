"""
Source 9: CoinGlass — Open Interest changes (free public endpoint)
Rising OI + rising price = strong trend confirmation
Rising OI + falling price = bearish signal
Falling OI = trend weakening
"""

import requests

HEADERS = {
    "User-Agent": "TelegramSignalBot/1.0",
    "Accept": "application/json",
}


def get_open_interest():
    """Get BTC & ETH open interest from CoinGlass public API."""
    results = []
    coins = [
        ("BTC", "https://open-api.coinglass.com/public/v2/open_interest?symbol=BTC&time_type=h4"),
        ("ETH", "https://open-api.coinglass.com/public/v2/open_interest?symbol=ETH&time_type=h4"),
    ]

    for name, url in coins:
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code != 200:
                # Fallback: use Binance futures OI
                r2 = requests.get(
                    f"https://fapi.binance.com/fapi/v1/openInterest",
                    params={"symbol": f"{name}USDT"},
                    headers=HEADERS, timeout=10
                )
                if r2.status_code == 200:
                    oi = float(r2.json().get("openInterest", 0))
                    results.append(f"  {name} OI: {oi:,.0f} contracts (Binance)")
                continue

            data = r.json().get("data", [])
            if not data:
                continue

            latest = data[-1] if isinstance(data, list) else data
            oi = latest.get("openInterest", 0)
            oi_change = latest.get("openInterestChange", 0)

            if oi_change > 2:
                signal = "📈 OI rising strongly (trend strength)"
            elif oi_change > 0:
                signal = "↗️ OI increasing"
            elif oi_change < -2:
                signal = "📉 OI falling sharply (trend weakening)"
            else:
                signal = "↘️ OI decreasing"

            results.append(f"  {name}: OI ${oi/1e9:.2f}B {oi_change:+.1f}% → {signal}")
        except Exception:
            continue

    return "\n".join(results) if results else None


def get_oi_from_binance():
    """Fallback: Get open interest from Binance Futures (free, no auth)."""
    results = []
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    for symbol in symbols:
        try:
            r = requests.get(
                "https://fapi.binance.com/fapi/v1/openInterest",
                params={"symbol": symbol},
                headers=HEADERS, timeout=10
            )
            r.raise_for_status()
            oi = float(r.json().get("openInterest", 0))
            ticker = symbol.replace("USDT", "")

            # Get price to convert OI to USD
            pr = requests.get(
                "https://fapi.binance.com/fapi/v1/ticker/price",
                params={"symbol": symbol},
                headers=HEADERS, timeout=10
            )
            price = float(pr.json().get("price", 1))
            oi_usd = oi * price
            results.append(f"  {ticker}: ${oi_usd/1e9:.2f}B open interest")
        except Exception:
            continue
    return "\n".join(results) if results else None
