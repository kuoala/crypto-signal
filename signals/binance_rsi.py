"""
Source 3: Binance Public API — RSI signals for BTC, ETH, BNB, SOL, XRP
No API key required for public market data.
"""

import requests
import numpy as np

HEADERS = {"User-Agent": "TelegramSignalBot/1.0"}
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
RSI_PERIOD = 14


def calculate_rsi(closes: list, period: int = RSI_PERIOD) -> float:
    if len(closes) < period + 1:
        return 50.0
    closes = np.array(closes, dtype=float)
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def get_rsi_signals():
    """Returns RSI-based signals for top coins on 4h timeframe."""
    signals = []
    for symbol in SYMBOLS:
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": symbol, "interval": "4h", "limit": 50},
                headers=HEADERS, timeout=10
            )
            r.raise_for_status()
            klines = r.json()
            closes = [float(k[4]) for k in klines]
            rsi = calculate_rsi(closes)

            ticker = symbol.replace("USDT", "")

            if rsi <= 30:
                signal = "🟢 STRONG BUY (Oversold)"
            elif rsi <= 45:
                signal = "🟡 BUY (Approaching oversold)"
            elif rsi >= 70:
                signal = "🔴 STRONG SELL (Overbought)"
            elif rsi >= 55:
                signal = "🟠 SELL (Approaching overbought)"
            else:
                signal = "⚪ NEUTRAL"

            signals.append(f"  {ticker}: RSI {rsi} → {signal}")
        except Exception:
            continue

    return "\n".join(signals) if signals else None


def get_volume_spikes():
    """Detect coins with unusually high 24h volume vs 7-day average."""
    results = []
    for symbol in SYMBOLS:
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": symbol, "interval": "1d", "limit": 8},
                headers=HEADERS, timeout=10
            )
            r.raise_for_status()
            klines = r.json()
            volumes = [float(k[5]) for k in klines]
            current_vol = volumes[-1]
            avg_vol = np.mean(volumes[:-1])
            ratio = current_vol / avg_vol if avg_vol > 0 else 1.0
            if ratio >= 1.5:
                ticker = symbol.replace("USDT", "")
                results.append(f"  ⚡ {ticker}: Volume {ratio:.1f}x above 7-day avg")
        except Exception:
            continue
    return "\n".join(results) if results else "  No significant volume spikes detected"
