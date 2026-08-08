#!/usr/bin/env python3
"""Round 2: build up the winning combination, then validate it out of sample.

Round 1 showed ADX is the one large lever, memecoins dilute, and the
concurrency caps trade expectancy for drawdown. This narrows that down and
checks the result holds on data not used to pick the parameters.
"""
import sys
import os
from dataclasses import replace

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import Params, load_cache, prepare, run, summarize, print_summary  # noqa: E402

# In-sample / out-of-sample boundary: parameters are chosen on IS only.
SPLIT = int(pd.Timestamp("2026-03-01T00:00:00Z").timestamp() * 1000)


def main():
    meta, data = load_cache()
    base = Params()
    prepped = prepare(data, base)
    rank = {s: i for i, s in enumerate(meta["main"])}

    def go(p, label, **kw):
        t = run(prepped, meta, p, **kw)
        print_summary(summarize(t, label))
        return t

    print("=" * 100)
    print("BUILDING THE COMBINATION  (full sample, no concurrency caps)")
    print("=" * 100)
    go(base, "0. live baseline")
    a = replace(base, adx_min=22)
    go(a, "1. + ADX>=22")
    b = replace(a, meme_enabled=False)
    go(b, "2. + no memecoins")
    c = replace(b, tp1_fraction=0.3)
    go(c, "3. + book 30% at TP1")
    d = replace(c, max_atr_pct=9.0)
    go(d, "4. + skip ATR%>9")
    e = replace(d, rr_ratio=3.0)
    go(e, "5. + TP2 at 3R")

    print("\n  variants around step 4:")
    for adx in (20, 22, 25, 28):
        go(replace(d, adx_min=adx), f"   ADX>={adx}")
    go(replace(d, meme_enabled=True), "   with memecoins back")
    go(replace(d, meme_enabled=True, adx_min=25), "   memes + ADX>=25 for all")

    print("\n" + "=" * 100)
    print("MEMECOINS: can they be salvaged with a stricter gate?")
    print("=" * 100)
    meme_only = replace(base, max_signals_per_run=0)
    go(meme_only, "memes only, live rules")
    go(replace(meme_only, adx_min=25), "memes only, ADX>=25")
    go(replace(meme_only, adx_min=30), "memes only, ADX>=30")
    go(replace(meme_only, adx_min=25, max_atr_pct=9.0), "memes only, ADX>=25 + ATR%<9")

    print("\n" + "=" * 100)
    print("UNIVERSE LIQUIDITY  (live runs on MEXC with a far wider, thinner list)")
    print("=" * 100)
    for topn in (20, 40, 60):
        keep = set(meta["main"][:topn])
        sub = {s: v for s, v in prepped.items() if s in keep or s == "BTC/USDT:USDT"}
        sub_meta = {"main": meta["main"][:topn], "meme": []}
        t = run(sub, sub_meta, replace(base, meme_enabled=False))
        print_summary(summarize(t, f"top {topn} by volume only"))
        t = run(sub, sub_meta, replace(base, meme_enabled=False, adx_min=22))
        print_summary(summarize(t, f"top {topn} + ADX>=22"))

    print("\n" + "=" * 100)
    print(f"OUT-OF-SAMPLE VALIDATION  (IS: before 2026-03, OOS: 2026-03 onward)")
    print("=" * 100)
    finalists = {
        "live baseline": base,
        "ADX>=22": a,
        "ADX>=22, no meme": b,
        "ADX>=22, no meme, 30% TP1": c,
        "step4 (+ATR%<9)": d,
    }
    print("\n-- in sample --")
    for label, p in finalists.items():
        go(p, label, end_ts=SPLIT)
    print("\n-- out of sample --")
    for label, p in finalists.items():
        go(p, label, start_ts=SPLIT)

    print("\n" + "=" * 100)
    print("MONTHLY STABILITY of the chosen config")
    print("=" * 100)
    t = run(prepped, meta, d)
    df = pd.DataFrame(t)
    df["m"] = df["ts"].map(lambda x: pd.Timestamp(x, unit="ms").strftime("%Y-%m"))
    base_t = pd.DataFrame(run(prepped, meta, base))
    base_t["m"] = base_t["ts"].map(lambda x: pd.Timestamp(x, unit="ms").strftime("%Y-%m"))
    print(f"  {'month':<9} {'n':>4} {'avg R':>8}   |  {'live n':>6} {'live avg':>9}")
    for m in sorted(set(df["m"]) | set(base_t["m"])):
        g = df[df["m"] == m]
        b_ = base_t[base_t["m"] == m]
        print(f"  {m:<9} {len(g):>4} {g['r'].mean():>+8.3f}   |  {len(b_):>6} {b_['r'].mean():>+9.3f}")


if __name__ == "__main__":
    main()
