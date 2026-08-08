#!/usr/bin/env python3
"""Event-driven backtest of the signal bot's strategy on real cached OHLCV.

Replays the live bot faithfully:
  * every 4h candle close is one "run" (the bot's cron cadence)
  * indicators use only closed bars up to that point (no lookahead)
  * the same filter stack runs (regime / HTF / freshness / RSI / min-SL)
  * run-level selection is reproduced (rank, MAX_SIGNALS_PER_RUN, meme slots,
    one open trade per symbol)
  * exits resolve on 1h bars with the staged TP1 -> breakeven -> TP2 logic,
    checking the stop before the target within a bar (worst case)

Usage:  python research/backtest.py [--variant NAME] [--json OUT]
"""
import os
import sys
import json
import pickle
import argparse
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

CACHE_DIR = os.environ.get(
    "BT_CACHE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ohlcv_cache"),
)

H4 = 4 * 3600 * 1000


# ===================== PARAMETERS =====================
@dataclass
class Params:
    name: str = "live"
    # indicators
    ema_fast: int = 20
    ema_slow: int = 50
    rsi_period: int = 14
    atr_period: int = 14
    # entry gates (live defaults)
    rsi_long_min: float = 45
    rsi_short_max: float = 55
    rsi_long_max: float = 72
    rsi_short_min: float = 28
    regime_filter: bool = True
    htf_confirm: bool = True
    fresh_bars: int = 3
    min_sl_pct: float = 1.0
    # risk
    atr_sl_mult: float = 1.5
    meme_atr_sl_mult: float = 2.0
    rr_ratio: float = 2.0
    # portfolio
    max_signals_per_run: int = 5
    meme_max_per_run: int = 2
    max_open: int = 10_000          # live bot has no cap
    max_open_per_side: int = 10_000  # live bot has no cap
    meme_enabled: bool = True
    # exit management
    tp1_r: float = 1.0            # first target, in R
    tp1_fraction: float = 0.5     # portion closed at TP1
    be_after_tp1: bool = True     # move stop to breakeven after TP1
    trail_atr_mult: float = 0.0   # >0 = trail the runner by N*ATR instead of fixed TP2
    max_hold_bars: int = 0        # 0 = no time stop; else close on 4h bars elapsed
    # candidate filters (off = live behaviour)
    adx_min: float = 0.0          # trend-strength gate
    min_atr_pct: float = 0.0      # skip dead-vol symbols
    max_atr_pct: float = 0.0      # skip blow-off vol
    vol_ratio_min: float = 0.0    # volume vs 20-bar average
    rank_by: str = "live"         # live | score_freshness | adx | none
    require_ema_slope: bool = False
    btc_corr_cap: int = 10_000    # max concurrent trades (alias of max_open, kept explicit)


# ===================== INDICATORS =====================
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff()
    gain = d.clip(lower=0).rolling(n).mean()
    loss = (-d.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(s, fast=12, slow=26, signal=9):
    ml = ema(s, fast) - ema(s, slow)
    sl = ema(ml, signal)
    return ml, sl, ml - sl


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def adx(df, n=14):
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr_
    mdi = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr_
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


# ===================== DATA =====================
def load_cache():
    with open(os.path.join(CACHE_DIR, "meta.pkl"), "rb") as f:
        meta = pickle.load(f)
    data = {}
    for fn in os.listdir(CACHE_DIR):
        if fn == "meta.pkl" or not fn.endswith(".pkl"):
            continue
        sym = fn[:-4].replace("_", "/").replace("-", ":")
        with open(os.path.join(CACHE_DIR, fn), "rb") as f:
            data[sym] = pickle.load(f)
    return meta, data


def prepare(data, p: Params):
    """Precompute indicator columns per symbol (causal, so full-series is safe)."""
    prepped = {}
    for sym, tfs in data.items():
        if "4h" not in tfs or "1h" not in tfs:
            continue
        df = tfs["4h"].copy()
        if len(df) < 120:
            continue
        c = df["close"]
        df["ema_f"] = ema(c, p.ema_fast)
        df["ema_s"] = ema(c, p.ema_slow)
        df["rsi"] = rsi(c, p.rsi_period)
        _, _, df["hist"] = macd(c)
        df["atr"] = atr(df, p.atr_period)
        df["adx"] = adx(df, 14)
        df["vol_ma"] = df["volume"].rolling(20).mean()
        # higher-timeframe (1d) trend, as known at each 4h close
        d1 = tfs.get("1d")
        if d1 is not None and len(d1) > p.ema_slow + 2:
            dc = d1["close"]
            def_ = ema(dc, p.ema_fast)
            des = ema(dc, p.ema_slow)
            trend = np.where((def_ > des) & (dc >= des), 1,
                             np.where((def_ < des) & (dc <= des), -1, 0))
            # signal_bot._ema_trend_from_ohlcv reports NEUTRAL until it has
            # ema_slow + 2 daily bars; mirror that so the warmup period matches.
            trend[:p.ema_slow + 2] = 0
            # a daily bar stamped T closes at T+1d, so it is only known from then on
            htf = pd.DataFrame({"ts": d1["ts"] + 86_400_000, "htf": trend})
            df = pd.merge_asof(df.sort_values("ts"), htf.sort_values("ts"),
                               on="ts", direction="backward")
        else:
            df["htf"] = 0
        df["fresh_long"] = _fresh(df, 1, p.fresh_bars)
        df["fresh_short"] = _fresh(df, -1, p.fresh_bars)
        prepped[sym] = {"h4": df.reset_index(drop=True),
                        "h1": tfs["1h"].reset_index(drop=True)}
    return prepped


def _fresh(df, side, bars):
    """MACD histogram flipped sign OR EMAs crossed within the last `bars` bars."""
    h, ef, es = df["hist"], df["ema_f"], df["ema_s"]
    if side > 0:
        flip = (h > 0) & (h.shift(1) <= 0)
        cross = (ef > es) & (ef.shift(1) <= es.shift(1))
    else:
        flip = (h < 0) & (h.shift(1) >= 0)
        cross = (ef < es) & (ef.shift(1) >= es.shift(1))
    ev = flip | cross
    return ev.rolling(bars, min_periods=1).max().fillna(0).astype(bool)


# ===================== SIGNAL =====================
def candidate(row, p: Params, bias, is_meme):
    """Return a signal dict for this bar, or None. Mirrors signal_bot.analyze().

    `row` is a plain dict of floats (see Series.pack) — kept dict-shaped rather
    than positional so the filter logic stays readable next to the live bot's.
    """
    c, ef, es = row["close"], row["ema_f"], row["ema_s"]
    rv, h, av = row["rsi"], row["hist"], row["atr"]
    if c != c or ef != ef or es != es or rv != rv or h != h or av != av:
        return None

    long_ok = ef > es and rv >= p.rsi_long_min and h > 0 and h > row["hist_prev"]
    short_ok = ef < es and rv <= p.rsi_short_max and h < 0 and h < row["hist_prev"]
    if long_ok:
        direction, fresh = "LONG", row["fresh_long"]
    elif short_ok:
        direction, fresh = "SHORT", row["fresh_short"]
    else:
        return None

    if p.regime_filter and bias in ("LONG", "SHORT") and direction != bias:
        return None
    if direction == "LONG" and rv > p.rsi_long_max:
        return None
    if direction == "SHORT" and rv < p.rsi_short_min:
        return None
    if p.fresh_bars and not fresh:
        return None

    sl_mult = p.meme_atr_sl_mult if is_meme else p.atr_sl_mult
    sl_dist = av * sl_mult
    if sl_dist <= 0:
        return None
    sl_pct = sl_dist / c * 100
    if sl_pct < p.min_sl_pct:
        return None

    if p.htf_confirm:
        ht = row["htf"]
        if ht == 1 and direction == "SHORT":
            return None
        if ht == -1 and direction == "LONG":
            return None

    # --- candidate (non-live) filters ---
    if p.adx_min and not (row["adx"] >= p.adx_min):
        return None
    atr_pct = av / c * 100
    if p.min_atr_pct and atr_pct < p.min_atr_pct:
        return None
    if p.max_atr_pct and atr_pct > p.max_atr_pct:
        return None
    if p.vol_ratio_min:
        vm = row["vol_ma"]
        if not (vm > 0) or row["volume"] / vm < p.vol_ratio_min:
            return None
    if p.require_ema_slope:
        if direction == "LONG" and not row["ema_f"] > row["ema_f_prev"]:
            return None
        if direction == "SHORT" and not row["ema_f"] < row["ema_f_prev"]:
            return None

    score = int(ef > es or ef < es) + 1 + 1  # trend + rsi + macd always true here
    if direction == "LONG" and row["ema_f_prev"] <= row["ema_s_prev"] and ef > es:
        score += 1
    if direction == "SHORT" and row["ema_f_prev"] >= row["ema_s_prev"] and ef < es:
        score += 1

    return {"direction": direction, "entry": c, "sl_dist": sl_dist, "sl_pct": sl_pct,
            "rsi": rv, "score": score, "atr": av, "adx": row["adx"], "is_meme": is_meme}


# ===================== EXIT SIMULATION =====================
def simulate_exit(h1, entry_ts, sig, p: Params, atr_at_entry):
    """Walk 1h bars after entry; return (outcome, net_R, exit_ts)."""
    entry = sig["entry"]
    risk = sig["sl_dist"]
    long = sig["direction"] == "LONG"
    t1 = entry + risk * p.tp1_r if long else entry - risk * p.tp1_r
    t2 = entry + risk * p.rr_ratio if long else entry - risk * p.rr_ratio

    # The 4h bar stamped entry_ts closes at entry_ts + 4h; that is when the bot
    # actually enters, so only 1h bars from then on may resolve the trade.
    fill_ts = entry_ts + H4
    start = int(np.searchsorted(h1[:, 0], fill_ts, side="left"))
    bars = h1[start:]
    if len(bars) == 0:
        return None, 0.0, None

    deadline = fill_ts + p.max_hold_bars * H4 if p.max_hold_bars else None
    stop = entry - risk if long else entry + risk
    tp1 = False
    booked = 0.0
    remaining = 1.0
    peak = entry

    for ts, o, hi, lo, cl in bars:
        # --- stop first (worst case within the bar) ---
        if (long and lo <= stop) or (not long and hi >= stop):
            exit_r = ((stop - entry) if long else (entry - stop)) / risk
            net = booked + remaining * exit_r
            if not tp1:
                return "SL", net, ts
            return ("BE" if abs(exit_r) < 1e-9 else "TRAIL"), net, ts

        # --- first target ---
        if not tp1 and ((long and hi >= t1) or (not long and lo <= t1)):
            tp1 = True
            booked += p.tp1_fraction * p.tp1_r
            remaining = 1.0 - p.tp1_fraction
            if p.be_after_tp1:
                stop = entry
            peak = t1

        # --- runner management ---
        if tp1:
            if p.trail_atr_mult > 0:
                peak = max(peak, hi) if long else min(peak, lo)
                trail = (peak - atr_at_entry * p.trail_atr_mult) if long else \
                        (peak + atr_at_entry * p.trail_atr_mult)
                stop = max(stop, trail) if long else min(stop, trail)
            elif (long and hi >= t2) or (not long and lo <= t2):
                net = booked + remaining * p.rr_ratio
                return "TP2", net, ts

        if deadline and ts >= deadline:
            exit_r = ((cl - entry) if long else (entry - cl)) / risk
            return "TIME", booked + remaining * exit_r, ts

    return None, 0.0, None   # still open at end of data


# ===================== BACKTEST LOOP =====================
def run(prepped, meta, p: Params, start_ts=None, end_ts=None):
    meme_set = set(meta["meme"])
    btc = prepped.get("BTC/USDT:USDT")
    if btc is None:
        raise SystemExit("BTC data missing from cache")

    # BTC daily regime, as known at each 4h close
    btc4 = btc["h4"]
    regime = {}
    for ts, htf in zip(btc4["ts"], btc4["htf"]):
        v = 0 if pd.isna(htf) else int(htf)
        regime[ts] = {1: "LONG", -1: "SHORT", 0: "NEUTRAL"}[v]

    # add prev-bar columns once
    for sym, d in prepped.items():
        df = d["h4"]
        if "hist_prev" not in df.columns:
            df["hist_prev"] = df["hist"].shift(1)
            df["ema_f_prev"] = df["ema_f"].shift(1)
            df["ema_s_prev"] = df["ema_s"].shift(1)

    all_ts = sorted(set(btc4["ts"]))
    if start_ts:
        all_ts = [t for t in all_ts if t >= start_ts]
    if end_ts:
        all_ts = [t for t in all_ts if t <= end_ts]

    # Pre-unpack each symbol into ts -> dict-of-floats. Pandas .loc per bar
    # dominated runtime; this makes a full replay a few seconds.
    COLS = ["close", "volume", "ema_f", "ema_s", "rsi", "hist", "atr", "adx",
            "vol_ma", "htf", "fresh_long", "fresh_short",
            "hist_prev", "ema_f_prev", "ema_s_prev"]
    rows = {}
    for sym, d in prepped.items():
        df = d["h4"]
        cols = {c: df[c].to_numpy() for c in COLS}
        tss = df["ts"].to_numpy()
        rows[sym] = {int(t): {c: cols[c][i] for c in COLS}
                     for i, t in enumerate(tss)}
    h1_arrays = {sym: d["h1"][["ts", "open", "high", "low", "close"]].to_numpy()
                 for sym, d in prepped.items()}

    open_trades = {}   # symbol -> trade dict
    closed = []

    for ts in all_ts:
        bias = regime.get(ts, "NEUTRAL")

        # 1) resolve open trades whose exit has arrived
        for sym in list(open_trades):
            tr = open_trades[sym]
            if tr["exit_ts"] is not None and tr["exit_ts"] <= ts:
                closed.append(tr)
                del open_trades[sym]

        # 2) scan for candidates
        cands = []
        for sym, idx in rows.items():
            if sym in open_trades:
                continue
            row = idx.get(ts)
            if row is None:
                continue
            is_meme = sym in meme_set
            if is_meme and not p.meme_enabled:
                continue
            sig = candidate(row, p, bias, is_meme)
            if sig:
                sig["symbol"] = sym
                sig["ts"] = ts
                cands.append(sig)

        if not cands:
            continue

        # 3) rank and take the run's slots (mirrors the live bot)
        if p.rank_by == "live":
            key = lambda s: (s["score"], abs(s["rsi"] - 50))
        elif p.rank_by == "score_freshness":
            key = lambda s: (s["score"], -abs(s["rsi"] - 50))
        elif p.rank_by == "adx":
            key = lambda s: (0 if pd.isna(s["adx"]) else s["adx"],)
        else:
            key = lambda s: (0,)
        mains = sorted([c for c in cands if not c["is_meme"]], key=key, reverse=True)
        memes = sorted([c for c in cands if c["is_meme"]], key=key, reverse=True)
        picked = mains[:p.max_signals_per_run] + memes[:p.meme_max_per_run]

        # 4) portfolio caps
        for sig in picked:
            if len(open_trades) >= min(p.max_open, p.btc_corr_cap):
                break
            side = sig["direction"]
            n_side = sum(1 for t in open_trades.values() if t["direction"] == side)
            if n_side >= p.max_open_per_side:
                continue
            h1 = h1_arrays[sig["symbol"]]
            outcome, net_r, exit_ts = simulate_exit(h1, ts, sig, p, sig["atr"])
            open_trades[sig["symbol"]] = {
                "symbol": sig["symbol"], "direction": side, "ts": ts,
                "entry": sig["entry"], "sl_pct": sig["sl_pct"], "rsi": sig["rsi"],
                "score": sig["score"], "adx": sig["adx"], "is_meme": sig["is_meme"],
                "outcome": outcome, "r": net_r, "exit_ts": exit_ts,
            }

    # trades never resolved by end of data are dropped (open book)
    for tr in open_trades.values():
        if tr["exit_ts"] is not None:
            closed.append(tr)
    return [t for t in closed if t["outcome"] is not None]


# ===================== REPORTING =====================
def summarize(trades, label=""):
    if not trades:
        return {"label": label, "n": 0}
    df = pd.DataFrame(trades)
    n = len(df)
    total_r = df["r"].sum()
    avg_r = df["r"].mean()
    wins = (df["r"] > 0).sum()
    counts = df["outcome"].value_counts().to_dict()
    df = df.sort_values("ts")
    eq = df["r"].cumsum()
    dd = (eq.cummax() - eq).max()
    return {
        "label": label, "n": n, "avg_r": round(avg_r, 3), "total_r": round(total_r, 1),
        "win_pct": round(wins / n * 100, 1), "max_dd_r": round(float(dd), 1),
        "outcomes": {k: int(v) for k, v in counts.items()},
    }


def print_summary(s):
    if s.get("n", 0) == 0:
        print(f"{s['label']:<28} no trades")
        return
    oc = " ".join(f"{k}:{v}" for k, v in sorted(s["outcomes"].items()))
    print(f"{s['label']:<28} n={s['n']:<5} avg={s['avg_r']:+.3f}R  total={s['total_r']:+8.1f}R  "
          f"win={s['win_pct']:>5.1f}%  maxDD={s['max_dd_r']:>6.1f}R  [{oc}]")


def shipped_params():
    """The config signal_bot.py actually runs, mirrored for the backtest."""
    return replace(Params(name="shipped"), adx_min=22.0, meme_enabled=False,
                   max_atr_pct=9.0, tp1_fraction=0.3)


SHIPPED_UNIVERSE = 40      # CONFIG["MAX_SCAN_SYMBOLS"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write trades to this file")
    ap.add_argument("--baseline", action="store_true",
                    help="run the pre-2026-08 config instead of the shipped one")
    ap.add_argument("--start", help="ISO date to start from, e.g. 2026-03-01")
    args = ap.parse_args()

    meta, data = load_cache()
    print(f"[+] Loaded {len(data)} symbols from cache")
    p = Params() if args.baseline else shipped_params()
    prepped = prepare(data, p)

    if args.baseline:
        label = "baseline (pre-2026-08)"
    else:
        keep = set(meta["main"][:SHIPPED_UNIVERSE]) | {"BTC/USDT:USDT"}
        prepped = {s: v for s, v in prepped.items() if s in keep}
        meta = {"main": meta["main"][:SHIPPED_UNIVERSE], "meme": []}
        label = f"shipped (top{SHIPPED_UNIVERSE}, ADX>={p.adx_min:g}, no meme)"

    print(f"[+] Prepared {len(prepped)} symbols")
    start_ts = int(pd.Timestamp(args.start).timestamp() * 1000) if args.start else None
    trades = run(prepped, meta, p, start_ts=start_ts)
    print_summary(summarize(trades, label))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(trades, f, default=str)


if __name__ == "__main__":
    main()
