#!/usr/bin/env python3
"""Round 3: confirm the final config out of sample, including the universe cap,
and check whether memecoin signals can be kept at all."""
import sys
import os
from dataclasses import replace

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import Params, load_cache, prepare, run, summarize, print_summary  # noqa: E402

SPLIT = int(pd.Timestamp("2026-03-01T00:00:00Z").timestamp() * 1000)


def main():
    meta, data = load_cache()
    base = Params()
    prepped = prepare(data, base)

    def subset(topn, with_meme):
        keep = set(meta["main"][:topn]) | {"BTC/USDT:USDT"}
        if with_meme:
            keep |= set(meta["meme"])
        sub = {s: v for s, v in prepped.items() if s in keep}
        sub_meta = {"main": meta["main"][:topn],
                    "meme": meta["meme"] if with_meme else []}
        return sub, sub_meta

    def go(p, label, sub=None, sub_meta=None, **kw):
        s, m = (sub, sub_meta) if sub is not None else (prepped, meta)
        t = run(s, m, p, **kw)
        print_summary(summarize(t, label))
        return t

    final = replace(base, adx_min=22, meme_enabled=False, max_atr_pct=9.0,
                    tp1_fraction=0.3)

    print("=" * 100)
    print("UNIVERSE CAP x FINAL CONFIG")
    print("=" * 100)
    for topn in (30, 50, 60, 70):
        sub, sm = subset(topn, False)
        print(f"\n  top {topn}:")
        go(final, f"    full sample", sub, sm)
        go(final, f"    in sample", sub, sm, end_ts=SPLIT)
        go(final, f"    OUT OF SAMPLE", sub, sm, start_ts=SPLIT)

    print("\n" + "=" * 100)
    print("CAN MEMECOINS BE KEPT?  (out of sample only — the honest test)")
    print("=" * 100)
    meme_only = replace(base, max_signals_per_run=0)
    for label, p in [
        ("memes, live rules", meme_only),
        ("memes, ADX>=22", replace(meme_only, adx_min=22)),
        ("memes, ADX>=25", replace(meme_only, adx_min=25)),
        ("memes, ADX>=25 ATR%<9", replace(meme_only, adx_min=25, max_atr_pct=9.0)),
        ("memes, ADX>=25 +regime", replace(meme_only, adx_min=25, max_atr_pct=9.0,
                                           vol_ratio_min=1.2)),
    ]:
        go(p, label + " [OOS]", start_ts=SPLIT)

    print("\n" + "=" * 100)
    print("FINAL vs LIVE, side by side")
    print("=" * 100)
    sub, sm = subset(60, False)
    for tag, kw in [("full sample", {}), ("in sample", {"end_ts": SPLIT}),
                    ("OUT OF SAMPLE", {"start_ts": SPLIT})]:
        print(f"\n  {tag}:")
        go(base, "    live config", **kw)
        go(final, "    final (top60+ADX22+nomeme)", sub, sm, **kw)


if __name__ == "__main__":
    main()
