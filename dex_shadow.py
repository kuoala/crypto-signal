"""DEX memecoin scanner (GeckoTerminal) in SHADOW mode: logs would-be signals and grades them
on real candles, never posts. Memecoin rules lost money in our CEX backtest, so they must earn
their way to the channel: promote only after dex_shadow.py --report shows enough positive trades."""
import json, os, sys, time
import numpy as np, pandas as pd, requests

API = "https://api.geckoterminal.com/api/v2"
NETS = ["solana", "base", "eth", "bsc"]
LOG = "dex_shadow.jsonl"
MIN_LIQ, MIN_VOL24, MIN_AGE_D = 250_000, 500_000, 3
PROMOTE_N, PROMOTE_R = 40, 0.15      # shadow trades needed / avg R needed before posting

def get(path, **kw):
    for _ in range(3):
        r = requests.get(f"{API}{path}", params=kw, timeout=20)
        if r.status_code == 429: time.sleep(5); continue
        r.raise_for_status(); time.sleep(2.2); return r.json()  # free tier ~30 req/min
    raise RuntimeError("rate limited")

def candles(net, pool, tf="hour", agg=1, limit=200):
    rows = get(f"/networks/{net}/pools/{pool}/ohlcv/{tf}", aggregate=agg, limit=limit)["data"]["attributes"]["ohlcv_list"]
    df = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v"]).sort_values("ts").reset_index(drop=True)
    return df

def indicators(df):
    c = df["c"]; d = c.diff()
    rsi = 100 - 100 / (1 + d.clip(lower=0).ewm(alpha=1/14).mean() / (-d.clip(upper=0)).ewm(alpha=1/14).mean())
    tr = pd.concat([df["h"] - df["l"], (df["h"] - c.shift()).abs(), (df["l"] - c.shift()).abs()], axis=1).max(axis=1)
    return c.ewm(span=20).mean(), c.ewm(span=50).mean(), rsi, tr.ewm(alpha=1/14).mean()

def evaluate(df, a):
    """Long-only trend-continuation on closed 1h bars. Returns signal dict or None."""
    if len(df) < 60: return None
    df = df.iloc[:-1]                       # drop forming candle
    e20, e50, rsi, atr = (x.iloc[-1] for x in indicators(df))
    px = df["c"].iloc[-1]
    h1, h6 = float(a["price_change_percentage"]["h1"]), float(a["price_change_percentage"]["h6"])
    if not (e20 > e50 and px > e20 and 45 <= rsi <= 72 and 0 < h1 <= 25 and h6 > 0):
        return None
    risk = max(1.5 * atr, 0.06 * px)       # memes are noisy: never tighter than 6%
    return dict(entry=px, stop=px - risk, tp1=px + risk, tp2=px + 2 * risk, rsi=round(float(rsi), 1))

def scan():
    seen = {r["pool"] for r in map(json.loads, open(LOG))} if os.path.exists(LOG) else set()
    out = []
    for net in NETS:
        for p in get(f"/networks/{net}/trending_pools", page=1)["data"]:
            a, pool = p["attributes"], p["attributes"]["address"]
            age = (time.time() - pd.Timestamp(a["pool_created_at"]).timestamp()) / 86400
            if (pool in seen or float(a["reserve_in_usd"] or 0) < MIN_LIQ
                    or float(a["volume_usd"]["h24"]) < MIN_VOL24 or age < MIN_AGE_D):
                continue
            sig = evaluate(candles(net, pool), a)
            if sig:
                out.append(dict(pool=pool, net=net, name=a["name"], ts=int(time.time() * 1000),
                                liq=float(a["reserve_in_usd"]), **sig))
    return out

def grade(rec):
    """TP1 (+1R) before stop => +1R; stop first => -1R; same bar => stop. >7d unresolved => closed at last price."""
    df = candles(rec["net"], rec["pool"], "minute", 15, 1000)
    df = df[df["ts"] * 1000 >= rec["ts"]]
    if df.empty: return None
    for _, b in df.iterrows():
        if b.l <= rec["stop"]: return -1.0
        if b.h >= rec["tp1"]: return 1.0
    if time.time() * 1000 - rec["ts"] > 7 * 864e5:
        return round((df.c.iloc[-1] - rec["entry"]) / (rec["entry"] - rec["stop"]), 2)
    return None

def main():
    recs = [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []
    for r in recs:
        if "r" not in r:
            g = grade(r)
            if g is not None: r["r"] = g
    recs += scan()
    open(LOG, "w").write("".join(json.dumps(r) + "\n" for r in recs))
    report(recs)

def report(recs):
    done = [r["r"] for r in recs if "r" in r]
    avg = float(np.mean(done)) if done else 0.0
    print(f"shadow: {len(recs)} signals, {len(done)} graded, avg R {avg:+.2f}, "
          f"promote={'YES' if len(done) >= PROMOTE_N and avg >= PROMOTE_R else 'no'}")

if __name__ == "__main__":
    if "--report" in sys.argv: report([json.loads(l) for l in open(LOG)])
    else: main()
