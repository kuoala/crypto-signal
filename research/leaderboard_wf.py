"""Does Hyperliquid leaderboard rank persist? Walk-forward: pick traders on days 90..45 ago, score them on the last 45 days.
Usage: python research/leaderboard_wf.py leaderboard.json [max_candidates]   (fills cached in research/hl_fills/)"""
import json, os, sys, time, requests
API, DAY = "https://api.hyperliquid.xyz/info", 86400_000
NOW = int(time.time() * 1000); CUT = NOW - 45 * DAY; START = NOW - 90 * DAY

def fills(addr):
    f = f"research/hl_fills/{addr}.json"
    if os.path.exists(f): return json.load(open(f))
    out, t = [], START
    while True:
        for _ in range(5):
            r = requests.post(API, json={"type": "userFillsByTime", "user": addr, "startTime": t}, timeout=30)
            if r.status_code == 200: break
            time.sleep(5)
        else: raise RuntimeError(r.status_code)
        page = r.json()
        out += [{k: x[k] for k in ("coin", "time", "dir", "closedPnl", "fee", "px", "sz")} for x in page]
        if len(page) < 2000: break
        t = page[-1]["time"] + 1
        time.sleep(1)
    os.makedirs("research/hl_fills", exist_ok=True); json.dump(out, open(f, "w")); time.sleep(0.7)
    return out

def stats(fs):
    c = [float(x["closedPnl"]) - float(x["fee"]) for x in fs if x["dir"].startswith("Close")]
    win, loss = sum(p for p in c if p > 0), -sum(p for p in c if p < 0)
    return dict(n=len(c), pnl=sum(c), pf=win / loss if loss else 9, top=max(c, default=0) / win if win else 1)

def candidates(path, cap):
    out = []
    for r in json.load(open(path))["leaderboardRows"]:
        w = {k: v for k, v in r["windowPerformances"]}; av = float(r["accountValue"])
        g = lambda k, f: float(w[k][f])
        if av >= 100_000 and 0.2 <= g("month", "roi") <= 5 and g("allTime", "pnl") > 100_000 and 0.5 <= g("month", "vlm") / av <= 40:
            out.append((g("month", "roi"), r["ethAddress"]))
    return [a for _, a in sorted(out, reverse=True)[:cap]]

if __name__ == "__main__":
    cands, rows = candidates(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 300), []
    for i, a in enumerate(cands):
        try: fs = fills(a)
        except Exception as e: print("skip", a, e); continue
        A = stats([x for x in fs if x["time"] < CUT]); B = stats([x for x in fs if x["time"] >= CUT])
        rows.append((a, A, B)); print(i, a[:10], A["n"], f"{A['pnl']:.0f}", B["n"], f"{B['pnl']:.0f}", flush=True)
    sel = [r for r in rows if r[1]["n"] >= 30 and r[1]["pnl"] > 0 and r[1]["pf"] >= 1.5 and r[1]["top"] < 0.4]
    act = lambda rs: [r for r in rs if r[2]["n"] >= 10]
    prof = lambda rs: sum(r[2]["pnl"] > 0 for r in rs) / max(len(rs), 1)
    print(f"\ncandidates {len(rows)}; selected on days 90-45: {len(sel)}")
    print(f"next 45d profitable: selected {prof(act(sel)):.0%} of {len(act(sel))} active | all candidates {prof(act(rows)):.0%} of {len(act(rows))}")
    print(f"next 45d total PnL: selected {sum(r[2]['pnl'] for r in sel):,.0f} | median {sorted(r[2]['pnl'] for r in sel)[len(sel)//2] if sel else 0:,.0f}")
    json.dump([r[0] for r in sel if r[2]["pnl"] > 0], open("research/hl_selected.json", "w"))
