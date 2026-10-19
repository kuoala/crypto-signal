"""Follow Hyperliquid leaderboard traders in SHADOW mode: logs each new position they open (entry = mid price we
could have got on detection) and its outcome when they close it, never posts. hl_traders.json = traders that passed
the walk-forward screen (research/leaderboard_wf.py). Promote only after --report shows enough positive trades."""
import json, os, sys, time, requests

API, LOG, STATE = "https://api.hyperliquid.xyz/info", "hl_shadow.jsonl", "hl_state.json"
MIN_NOTIONAL, FEE = 5_000, 0.001           # ignore dust positions; 0.1% round-trip taker fees
PROMOTE_N, PROMOTE_RET = 40, 0.005         # closed shadow trades needed / avg net return needed
TIMEOUT_H = 72                             # still open after this long -> closed at current mid

def post(body):
    for wait in (3, 10, 30):
        r = requests.post(API, json=body, timeout=20)
        if r.status_code == 429: time.sleep(wait); continue
        r.raise_for_status(); return r.json()
    raise RuntimeError("rate limited")

def positions(addr):
    """{coin: signed size} for plain perp coins (skips xyz:/@ builder-deployed markets)."""
    st = post({"type": "clearinghouseState", "user": addr})
    return {p["position"]["coin"]: float(p["position"]["szi"]) * float(p["position"]["entryPx"])
            for p in st["assetPositions"] if ":" not in p["position"]["coin"] and float(p["position"]["szi"])}

def step(prev, now, mids, recs, addr, ts):
    """Diff one trader's positions: close what's gone/flipped, open what's new. Pure -> testable."""
    for coin in set(prev) | set(now):
        old, new = prev.get(coin, 0), now.get(coin, 0)
        if old and (not new or (old > 0) != (new > 0)):                       # closed or flipped
            for r in recs:
                if r["trader"] == addr and r["coin"] == coin and "ret" not in r and coin in mids:
                    r["exit"] = mids[coin]; r["ret"] = round(r["side"] * (mids[coin] / r["entry"] - 1) - FEE, 5)
        if new and abs(new) >= MIN_NOTIONAL and (not old or (old > 0) != (new > 0)) and coin in mids:
            recs.append(dict(trader=addr, coin=coin, side=1 if new > 0 else -1, entry=mids[coin], ts=ts, notional=round(abs(new))))

def main():
    traders = json.load(open("hl_traders.json"))
    state = json.load(open(STATE)) if os.path.exists(STATE) else None
    recs = [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []
    mids = {k: float(v) for k, v in post({"type": "allMids"}).items()}
    ts, new_state = int(time.time() * 1000), {}
    for a in traders:
        try: new_state[a] = positions(a)
        except Exception as e: print("skip", a[:8], e); new_state[a] = (state or {}).get(a, {}); continue
        if state is not None: step(state.get(a, {}), new_state[a], mids, recs, a, ts)   # first run: just snapshot
        time.sleep(0.4)
    for r in recs:                                                            # timeout stale open trades
        if "ret" not in r and ts - r["ts"] > TIMEOUT_H * 3600_000 and r["coin"] in mids:
            r["exit"] = mids[r["coin"]]; r["ret"] = round(r["side"] * (mids[r["coin"]] / r["entry"] - 1) - FEE, 5)
    json.dump(new_state, open(STATE, "w")); open(LOG, "w").write("".join(json.dumps(r) + "\n" for r in recs))
    report(recs)

def report(recs):
    done = [r["ret"] for r in recs if "ret" in r]
    avg = sum(done) / len(done) if done else 0.0
    print(f"hl shadow: {len(recs)} opened, {len(done)} closed, win {sum(x > 0 for x in done)}/{len(done)}, "
          f"avg net {avg:+.2%}, promote={'YES' if len(done) >= PROMOTE_N and avg >= PROMOTE_RET else 'no'}")

if __name__ == "__main__":
    if "--report" in sys.argv: report([json.loads(l) for l in open(LOG)])
    else:
        recs, mids = [], {"BTC": 110.0, "ETH": 50.0}
        step({}, {"BTC": 20_000}, mids, recs, "t", 1); assert recs[0]["side"] == 1 and recs[0]["entry"] == 110.0
        step({"BTC": 20_000}, {"BTC": 20_000}, mids, recs, "t", 2); assert len(recs) == 1          # unchanged -> nothing
        step({"BTC": 20_000}, {"BTC": -9_000}, {"BTC": 121.0}, recs, "t", 3)                       # flip: close long, open short
        assert recs[0]["ret"] == round(0.1 - FEE, 5) and recs[1]["side"] == -1
        step({}, {"ETH": 100}, mids, recs, "t", 4); assert len(recs) == 2                          # dust ignored
        if "--run" in sys.argv: main()
        else: print("ok (use --run)")
