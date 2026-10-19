"""Backtest Rule A variants on Hyperliquid history (reuses alt_breakout.py's indicators/step/costs).
Compares: baseline 20 coins vs wider universe vs looser params. Design on first half, judge on second."""
import sys, time, json, pickle, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import alt_breakout as a

CACHE = "/tmp/bo_cache.pkl"
now = int(time.time() * 1000)

def load():
    if os.path.exists(CACHE): return pickle.load(open(CACHE, "rb"))
    meta, ctx = a.hl({"type": "metaAndAssetCtxs"})
    vol = sorted(((float(c["dayNtlVlm"]), m["name"]) for m, c in zip(meta["universe"], ctx) if not m.get("isDelisted")), reverse=True)
    names = [n for _, n in vol if n not in ("BTC", "ETH", "SOL")][:70]
    D = {}
    for c in ["BTC"] + names:
        try:
            d4 = a.candles(c, "4h", 900, now); d1 = a.candles(c, "1d", 900, now)
            if len(d4) > 600 and len(d1) > 260: D[c] = (a.indicators(d4).set_index("T", drop=False), a.daily_up(d1))
        except Exception as e: print("skip", c, e)
    pickle.dump((D, names), open(CACHE, "wb")); return D, names

def signals(D, coins, donch, adx_min, chop_max, rule_b=False):
    out = []
    btc = D["BTC"][1]
    for c in coins:
        if c not in D: continue
        f, du = D[c]
        f = f.copy(); f["don"] = f.h.shift().rolling(donch).max()
        up = pd.merge_asof(f[["T"]].reset_index(drop=True), du.rename(columns={"up": "dup"}), on="T").dup.fillna(False).to_numpy()
        m = (f.c > f.don) & (f.adx > adx_min) & (f.chop < chop_max) & up
        if rule_b:
            m &= pd.merge_asof(f[["T"]].reset_index(drop=True), btc.rename(columns={"up": "b"}), on="T").b.fillna(False).to_numpy()
        for i in np.flatnonzero(m.to_numpy()):
            out.append((int(f["T"].iloc[i]), -f.adx.iloc[i], f.chop.iloc[i], c, f.iloc[i]))
    return out

def simulate(D, sigs, cap):
    sigs.sort(key=lambda x: x[:3]); i = 0; open_ = []; closed = []
    Ts = sorted({int(t) for c in D for t in D[c][0]["T"] if t > min(s[0] for s in sigs)}) if sigs else []
    by = {}
    for s in sigs: by.setdefault(s[0], []).append(s)
    for T in Ts:
        still = []
        for tr in open_:
            f = D[tr["coin"]][0]
            if T not in f.index: still.append(tr); continue
            res = a.step(tr, f.loc[T]); tr["last_T"] = T
            if res: closed.append(a.close_trade(tr, *res, T, 0.0))   # funding ignored (≈0.01R/trade earlier)
            else: still.append(tr)
        open_ = still
        held = {t["coin"] for t in open_}
        for _, _, _, c, r in by.get(T, []):
            if len(open_) >= cap: break
            if c in held: continue
            open_.append(a.new_trade("A", len(closed) + len(open_), c, r)); held.add(c)
    return closed

def report(name, closed, split):
    def s(cs):
        st = a.stats(cs); return f"n={st['n']:3d} PF={st['pf']:.2f} avgR={st['avg_r']:+.3f} DD={st['dd']:.1f}%"
    first = [c for c in closed if c["entry_T"] < split]; second = [c for c in closed if c["entry_T"] >= split]
    weeks = (now - min(c["entry_T"] for c in closed)) / 6048e5 if closed else 1
    print(f"{name:34s} ALL {s(closed)} /wk={len(closed)/weeks:.1f} | 1st {s(first)} | 2nd(OOS) {s(second)}")

if __name__ == "__main__":
    D, names = load()
    all_t = sorted(t for c in D for t in D[c][0]["T"]); split = all_t[len(all_t) // 2]
    base = [c for c in a.UNIVERSE if c in D]
    print(f"coins loaded: {len(D)-1}  baseline coins found: {len(base)}  split={a.utc(split)}")
    top40 = [c for c in names if c in D][:40]; top60 = [c for c in names if c in D][:60]
    cfgs = [("A base (20 coins)", base, 16, 25, 45, 4, False),
            ("A top-40 by volume", top40, 16, 25, 45, 4, False),
            ("A top-60 by volume", top60, 16, 25, 45, 4, False),
            ("A top-40, cap 6", top40, 16, 25, 45, 6, False),
            ("A 20 coins, ADX>22", base, 16, 22, 45, 4, False),
            ("A 20 coins, CHOP<50", base, 16, 25, 50, 4, False),
            ("A 20 coins, donch 12", base, 12, 25, 45, 4, False),
            ("A 20 coins, donch 12 ADX22 CHOP50", base, 12, 22, 50, 4, False),
            ("B 20 coins (BTC regime)", base, 16, 25, 45, 4, True)]
    for n, coins, d, ax, ch, cap, rb in cfgs:
        report(n, simulate(D, signals(D, coins, d, ax, ch, rb), cap), split)
