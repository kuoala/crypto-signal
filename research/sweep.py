#!/usr/bin/env python3
"""Diagnose where the signal bot's expectancy leaks, then test candidate fixes.

Every variant is the live strategy with one thing changed, run over identical
data, so the difference in avg R is attributable to that change.
"""
import sys
import os
from dataclasses import replace

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import Params, load_cache, prepare, run, summarize, print_summary  # noqa: E402

pd.options.mode.chained_assignment = None


def bucket_report(trades, title, keyfn):
    print(f"\n--- {title} ---")
    df = pd.DataFrame(trades)
    if df.empty:
        print("  no trades")
        return
    df["bucket"] = df.apply(keyfn, axis=1)
    for b, g in df.groupby("bucket"):
        oc = " ".join(f"{k}:{v}" for k, v in sorted(g["outcome"].value_counts().items()))
        print(f"  {str(b):<18} n={len(g):<5} avg={g['r'].mean():+.3f}R  "
              f"total={g['r'].sum():+7.1f}R  [{oc}]")


def main():
    meta, data = load_cache()
    base = Params()
    prepped = prepare(data, base)
    print(f"[+] {len(prepped)} symbols, "
          f"{min(len(d['h4']) for d in prepped.values())}-"
          f"{max(len(d['h4']) for d in prepped.values())} 4h bars each\n")

    cache = {}

    def go(p, label):
        if label in cache:
            return cache[label]
        t = run(prepped, meta, p)
        cache[label] = t
        print_summary(summarize(t, label))
        return t

    print("=" * 100)
    print("BASELINE")
    print("=" * 100)
    live = go(base, "live config")

    if not live:
        print("no trades produced — check cache")
        return

    bucket_report(live, "by direction", lambda r: r["direction"])
    bucket_report(live, "meme vs main", lambda r: "meme" if r["is_meme"] else "main")
    bucket_report(live, "by ADX at entry",
                  lambda r: "nan" if pd.isna(r["adx"]) else
                  f"{int(min(r['adx'],45)//10*10):02d}-{int(min(r['adx'],45)//10*10)+10}")
    bucket_report(live, "by |RSI-50| at entry",
                  lambda r: f"{int(min(abs(r['rsi']-50),30)//5*5):02d}+")
    bucket_report(live, "by stop distance %",
                  lambda r: f"{min(r['sl_pct'],10)//2*2:.0f}-{min(r['sl_pct'],10)//2*2+2:.0f}%")
    bucket_report(live, "by month", lambda r: pd.Timestamp(r["ts"], unit="ms").strftime("%Y-%m"))

    rank = {s: i for i, s in enumerate(meta["main"])}
    bucket_report(live, "by 24h-volume rank",
                  lambda r: "meme" if r["is_meme"] else
                  f"top{(rank.get(r['symbol'], 99) // 20 + 1) * 20}")

    print("\n" + "=" * 100)
    print("FILTER ABLATION  (is each existing filter actually helping?)")
    print("=" * 100)
    go(replace(base, name="x", regime_filter=False), "no BTC regime filter")
    go(replace(base, htf_confirm=False), "no 1d HTF confirm")
    go(replace(base, fresh_bars=0), "no freshness gate")
    go(replace(base, rsi_long_max=100, rsi_short_min=0), "no RSI exhaustion guard")
    go(replace(base, regime_filter=False, htf_confirm=False, fresh_bars=0,
               rsi_long_max=100, rsi_short_min=0), "no quality filters at all")

    print("\n" + "=" * 100)
    print("SELECTION / PORTFOLIO")
    print("=" * 100)
    go(replace(base, rank_by="score_freshness"), "rank: prefer RSI near 50")
    go(replace(base, rank_by="adx"), "rank: by ADX")
    go(replace(base, meme_enabled=False), "no memecoins")
    for cap in (4, 6, 8):
        go(replace(base, max_open=cap), f"max {cap} concurrent trades")
    for cap in (3, 4, 5):
        go(replace(base, max_open_per_side=cap), f"max {cap} per side")
    for n in (1, 2, 3):
        go(replace(base, max_signals_per_run=n, meme_max_per_run=1),
           f"max {n} main +1 meme per run")

    print("\n" + "=" * 100)
    print("ENTRY QUALITY FILTERS")
    print("=" * 100)
    for a in (18, 22, 25, 30):
        go(replace(base, adx_min=a), f"ADX >= {a}")
    for v in (1.0, 1.3):
        go(replace(base, vol_ratio_min=v), f"volume >= {v}x 20-bar avg")
    go(replace(base, require_ema_slope=True), "EMA20 sloping with trade")
    for lo in (1.5, 2.0):
        go(replace(base, min_atr_pct=lo), f"ATR% >= {lo}")
    go(replace(base, max_atr_pct=8.0), "ATR% <= 8")

    print("\n" + "=" * 100)
    print("EXIT MANAGEMENT  (TP2 conversion is the known weak point)")
    print("=" * 100)
    for rr in (1.5, 2.0, 2.5, 3.0):
        go(replace(base, rr_ratio=rr), f"fixed TP2 at {rr}R")
    for tm in (1.5, 2.0, 2.5, 3.0):
        go(replace(base, trail_atr_mult=tm), f"trail runner {tm}x ATR after TP1")
    for f in (0.3, 0.5, 0.7):
        go(replace(base, tp1_fraction=f), f"book {f:.0%} at TP1")
    go(replace(base, be_after_tp1=False), "no breakeven move after TP1")
    for h in (12, 24, 42):
        go(replace(base, max_hold_bars=h), f"time stop after {h} 4h bars")

    print("\n" + "=" * 100)
    print("COMBINED CANDIDATES")
    print("=" * 100)
    cand_a = replace(base, adx_min=22, max_open=6, max_open_per_side=4,
                     trail_atr_mult=2.5, max_hold_bars=42, meme_enabled=False,
                     rank_by="adx")
    go(cand_a, "candidate A")
    cand_b = replace(base, adx_min=25, max_open=6, max_open_per_side=4,
                     rr_ratio=1.5, max_hold_bars=30, rank_by="adx")
    go(cand_b, "candidate B")


if __name__ == "__main__":
    main()
