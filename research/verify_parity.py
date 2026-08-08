#!/usr/bin/env python3
"""Cross-check the backtest against the real signal_bot.analyze().

The backtest re-implements the strategy for speed. That is only trustworthy if
it agrees with the production code, so this drives the *actual* analyze() with a
stub exchange serving cached candles as of a point in time, and compares its
verdict against the backtest's on the same bars.
"""
import os
import sys
import random
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import signal_bot as sb           # noqa: E402
from backtest import (Params, load_cache, prepare, candidate)  # noqa: E402

H4 = 4 * 3600 * 1000
TF_MS = {"4h": H4, "1h": 3600 * 1000, "1d": 86_400_000}


class StubExchange:
    """Serves cached candles as they would have looked at time `now_ms`."""

    def __init__(self, data, now_ms):
        self.data = data
        self.now_ms = now_ms

    def parse_timeframe(self, tf):
        return TF_MS[tf] // 1000

    def milliseconds(self):
        return self.now_ms

    def fetch_ohlcv(self, symbol, timeframe="4h", limit=None, since=None):
        tfs = self.data.get(symbol)
        if not tfs or timeframe not in tfs:
            raise RuntimeError(f"no data for {symbol} {timeframe}")
        df = tfs[timeframe]
        df = df[df["ts"] <= self.now_ms]
        rows = df[["ts", "open", "high", "low", "close", "volume"]].values.tolist()
        if limit:
            rows = rows[-limit:]
        return rows


def main():
    # Default to the baseline params; --prod checks the shipped filter values so
    # the ADX and ATR% code paths are covered too.
    if "--prod" in sys.argv:
        p = Params(adx_min=22.0, max_atr_pct=9.0)
    else:
        p = Params()
    meta, data = load_cache()
    prepped = prepare(data, p)

    btc4 = prepped["BTC/USDT:USDT"]["h4"]
    regime = {}
    for ts, htf in zip(btc4["ts"], btc4["htf"]):
        v = 0 if htf != htf else int(htf)
        regime[int(ts)] = {1: "LONG", -1: "SHORT", 0: "NEUTRAL"}[v]

    for sym, d in prepped.items():
        df = d["h4"]
        df["hist_prev"] = df["hist"].shift(1)
        df["ema_f_prev"] = df["ema_f"].shift(1)
        df["ema_s_prev"] = df["ema_s"].shift(1)

    # Match the live config the backtest is modelling.
    sb.CONFIG["ADX_MIN"] = p.adx_min
    sb.CONFIG["MAX_ATR_PCT"] = p.max_atr_pct
    sb.CONFIG["DRY_RUN"] = True

    rng = random.Random(7)
    symbols = [s for s in prepped if s != "BTC/USDT:USDT"]
    all_ts = [int(t) for t in btc4["ts"]][200:]

    checked = agree = 0
    mismatches = []
    for _ in range(400):
        sym = rng.choice(symbols)
        ts = rng.choice(all_ts)
        idx = prepped[sym]["h4"].set_index("ts", drop=False)
        if ts not in idx.index:
            continue
        row = idx.loc[ts]
        if hasattr(row, "iloc") and getattr(row, "ndim", 1) > 1:
            row = row.iloc[0]
        rowd = {c: row[c] for c in row.index}

        bias = regime.get(ts, "NEUTRAL")
        is_meme = sym in set(meta["meme"])
        expected = candidate(rowd, p, bias, is_meme)

        sb.MARKET_BIAS = bias
        sb._HTF_CACHE.clear()
        ex = StubExchange(data, ts + H4)     # as of that candle's close
        try:
            got = sb.analyze(ex, sym,
                             sl_mult=p.meme_atr_sl_mult if is_meme else p.atr_sl_mult,
                             max_lev=5 if is_meme else 10)
        except Exception as e:
            mismatches.append((sym, ts, f"analyze raised {type(e).__name__}: {e}"))
            continue

        checked += 1
        if (expected is None) != (got is None):
            mismatches.append((sym, ts,
                               f"backtest={'signal' if expected else 'none'} "
                               f"live={'signal' if got else 'none'}"))
            continue
        if expected is None:
            agree += 1
            continue
        same_dir = expected["direction"] == got["direction"]
        same_entry = abs(expected["entry"] - got["entry"]) < 1e-9
        risk_bt = expected["sl_dist"]
        risk_live = abs(got["entry"] - got["stop"])
        same_risk = abs(risk_bt - risk_live) / max(risk_live, 1e-12) < 1e-6
        if same_dir and same_entry and same_risk:
            agree += 1
        else:
            mismatches.append((sym, ts,
                               f"dir {expected['direction']}/{got['direction']} "
                               f"entry {expected['entry']}/{got['entry']} "
                               f"risk {risk_bt:.10g}/{risk_live:.10g}"))

    print(f"checked {checked} (symbol, bar) pairs")
    print(f"agree:  {agree}")
    print(f"differ: {len(mismatches)}")
    for m in mismatches[:15]:
        print("   ", m)
    if checked and agree == checked:
        print("\nPARITY OK — the backtest matches production analyze() on every sampled bar.")


if __name__ == "__main__":
    main()
