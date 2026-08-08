#!/usr/bin/env python3
"""Download and cache real OHLCV history for backtesting the signal bot.

Pulls the same universe the live bot scans (top USDT perps by 24h volume + the
memecoin list) at 4h (signal timeframe), 1h (exit resolution) and 1d (HTF trend).
Cached as pickled DataFrames so backtest iterations are fast and offline.
"""
import os
import sys
import time
import pickle
from datetime import datetime, timezone

import ccxt
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from signal_bot import MEME_BASES, normalize_base, is_tradable_base  # noqa: E402

CACHE_DIR = os.environ.get(
    "BT_CACHE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ohlcv_cache"),
)
os.makedirs(CACHE_DIR, exist_ok=True)

# History window: enough bars for a meaningful sample plus indicator warmup.
START = "2025-09-01T00:00:00Z"
TOP_N = 70              # top N non-meme perps by 24h volume
MEME_N = 25             # top N meme perps by 24h volume
TIMEFRAMES = ["4h", "1h", "1d"]


def ms(iso):
    return int(pd.Timestamp(iso).timestamp() * 1000)


def fetch_all(ex, symbol, timeframe, since):
    """Paginate fetch_ohlcv from `since` to now."""
    out = []
    cursor = since
    tf_ms = ex.parse_timeframe(timeframe) * 1000
    while True:
        try:
            batch = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=1500)
        except Exception as e:
            print(f"      ! {symbol} {timeframe}: {type(e).__name__} {str(e)[:80]}")
            time.sleep(2)
            return out
        if not batch:
            break
        out += batch
        if len(batch) < 2:
            break
        nxt = batch[-1][0] + tf_ms
        if nxt <= cursor:
            break
        cursor = nxt
        if cursor > ex.milliseconds():
            break
    return out


def to_df(rows):
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates(subset="ts").sort_values("ts").reset_index(drop=True)
    return df


def pick_universe(ex):
    markets = ex.load_markets()
    tickers = ex.fetch_tickers()
    main, meme = [], []
    for sym, m in markets.items():
        if not m.get("active") or m.get("quote") != "USDT":
            continue
        if not (m.get("swap") or m.get("future")):
            continue
        if m.get("linear") is False:
            continue
        settle = (m.get("settle") or "").upper()
        if settle and settle != "USDT":
            continue
        base = m.get("base", "")
        t = tickers.get(sym)
        if not t:
            continue
        qvol = t.get("quoteVolume") or 0
        if normalize_base(base) in MEME_BASES:
            if qvol >= 1_000_000:
                meme.append((sym, qvol))
        elif is_tradable_base(base) and qvol >= 5_000_000:
            main.append((sym, qvol))
    main.sort(key=lambda x: x[1], reverse=True)
    meme.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in main[:TOP_N]], [s for s, _ in meme[:MEME_N]]


def main():
    ex = ccxt.binanceusdm({"enableRateLimit": True, "options": {"defaultType": "future"}})
    ex.load_markets()
    print(f"[+] Connected to binanceusdm ({len(ex.markets)} markets)")

    main_syms, meme_syms = pick_universe(ex)
    symbols = list(dict.fromkeys(main_syms + meme_syms + ["BTC/USDT:USDT"]))
    print(f"[+] Universe: {len(main_syms)} majors + {len(meme_syms)} memes = {len(symbols)} symbols")

    meta = {"main": main_syms, "meme": meme_syms, "start": START,
            "fetched_at": datetime.now(timezone.utc).isoformat()}
    with open(os.path.join(CACHE_DIR, "meta.pkl"), "wb") as f:
        pickle.dump(meta, f)

    since = ms(START)
    for i, sym in enumerate(symbols, 1):
        safe = sym.replace("/", "_").replace(":", "-")
        path = os.path.join(CACHE_DIR, f"{safe}.pkl")
        if os.path.exists(path):
            print(f"  [{i}/{len(symbols)}] {sym} cached")
            continue
        data = {}
        for tf in TIMEFRAMES:
            df = to_df(fetch_all(ex, sym, tf, since))
            if df is not None:
                data[tf] = df
        if "4h" not in data or len(data["4h"]) < 300:
            print(f"  [{i}/{len(symbols)}] {sym} insufficient history, skipped")
            continue
        with open(path, "wb") as f:
            pickle.dump(data, f)
        bars = {tf: len(d) for tf, d in data.items()}
        print(f"  [{i}/{len(symbols)}] {sym} {bars}")

    print(f"[+] Cache at {CACHE_DIR}")


if __name__ == "__main__":
    main()
