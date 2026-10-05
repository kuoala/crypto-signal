"""Post-hoc test: does funding-rate crowding at entry predict trade R? (trades.json from backtest.py --json)"""
import json, sys, pandas as pd, numpy as np, ccxt
trades = json.load(open(sys.argv[1]))
ex = ccxt.binanceusdm({"enableRateLimit": True}); ex.load_markets()
since = min(t["ts"] for t in trades) - 8 * 3600 * 1000
fund = {}
for s in sorted({t["symbol"] for t in trades}):
    rows, cur = [], since
    while True:
        b = ex.fetch_funding_rate_history(s, since=cur, limit=1000)
        if not b: break
        rows += b; nxt = b[-1]["timestamp"] + 1
        if len(b) < 2 or nxt <= cur: break
        cur = nxt
    fund[s] = pd.Series({r["timestamp"]: r["fundingRate"] for r in rows}).sort_index()
for t in trades:
    f = fund[t["symbol"]]; f = f[f.index <= t["ts"]]
    t["f"] = f.iloc[-1] if len(f) else np.nan
    t["crowded"] = (t["f"] > 0 if t["direction"] == "LONG" else t["f"] < 0) and abs(t["f"]) >= float(sys.argv[2])
df = pd.DataFrame(trades).dropna(subset=["f"])
print(f"{len(df)} trades with funding; threshold |f|>={sys.argv[2]}")
for k, g in df.groupby("crowded"):
    print(f"  crowded={k}: n={len(g)} avg R {g.r.mean():+.3f} win {(g.r > 0).mean():.0%}")
