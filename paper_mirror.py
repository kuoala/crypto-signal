"""Paper-trade the mirrored source channels' signals (every channel in channels.json, only signals we actually posted) on real MEXC candles. Prints a report; posts nothing.

    python paper_mirror.py [pages=60]      # ~20 source posts per page, newest first

Rules (deliberately conservative): fill at the entry-zone midpoint when price first touches the zone within
FILL_H hours of the post; SL beats TP when one candle spans both; stop moves to entry after TP1 in the scale-out
model; anything open after MAX_H is closed at market; COST = round-trip fee+slippage as % of notional.
Result is in R (1R = entry->stop), so leverage is irrelevant. Source "profit %" claims are never used.
"""
import json, sys, time
from collections import defaultdict
from datetime import datetime, timezone
from sources import fetch_posts, parse_signal, mexc_symbol, exchange

FILL_H, MAX_H, COST, TF_MS = 12, 7 * 24, 0.001, 5 * 60_000


def sim(sig, ms0, candles):
    """candles: [[ms,o,h,l,c]] 5m from post time. -> dict(status, tp1_r, scale_r, hit) ; R values net of COST."""
    e0 = sig["entries"][:2]
    lo, hi, long = min(e0), max(e0), sig["side"] == "LONG"
    e, stop = (lo + hi) / 2, sig["stop"]
    tps = [t for t in sig["targets"] if (t > e) == long]
    risk = abs(e - stop)
    if not tps or (stop < e) != long or risk / e < 0.003:
        return dict(status="bad")
    fee = COST * e / risk
    i = next((k for k, c in enumerate(candles) if c[3] <= hi and c[2] >= lo and c[0] < ms0 + FILL_H * 3600_000), None)
    if i is None:
        return dict(status="unfilled")
    r = lambda p: (p - e) / risk if long else (e - p) / risk
    n, hit, stopped_at, legs = len(tps), 0, None, 0.0       # scale-out: equal legs, stop -> entry after TP1
    tp1_r = None
    for c in candles[i + 1:]:
        if c[0] > candles[i][0] + MAX_H * 3600_000: break
        sl = stop if hit == 0 else e
        if (c[3] <= sl) if long else (c[2] >= sl):           # SL first on an ambiguous candle
            if tp1_r is None: tp1_r = -1.0
            legs += (n - hit) / n * r(sl)
            return dict(status="closed", tp1_r=tp1_r - fee, scale_r=legs - fee, hit=hit, t=c[0])
        while hit < n and ((c[2] >= tps[hit]) if long else (c[3] <= tps[hit])):
            if hit == 0 and tp1_r is None: tp1_r = r(tps[0])
            legs += r(tps[hit]) / n; hit += 1
        if hit == n:
            return dict(status="closed", tp1_r=tp1_r - fee, scale_r=legs - fee, hit=hit, t=c[0])
    last = candles[-1][4]                                     # timed out: mark open remainder at market
    if tp1_r is None: tp1_r = r(last)
    return dict(status="closed", tp1_r=tp1_r - fee, scale_r=legs + (n - hit) / n * r(last) - fee, hit=hit, timeout=True, t=candles[-1][0])


def candles(sym, ms0):
    ex, out, since = exchange(), [], ms0
    while since < min(ms0 + (MAX_H + FILL_H) * 3600_000, time.time() * 1000):
        b = ex.fetch_ohlcv(sym, "5m", since, 1000)
        if not b: break
        out += b; since = b[-1][0] + TF_MS
        if len(b) < 2: break
    return out


def risk_pct(s):
    e = sum(s["entries"][:2]) / len(s["entries"][:2])
    return round(abs(e - s["stop"]) / e * 100, 3)


def signals(ch, pages):
    before, seen = None, {}
    for _ in range(pages):
        for k in range(4):                                    # t.me times out now and then
            try: pg = list(fetch_posts(ch, before)); break
            except Exception: time.sleep(2 * (k + 1)); pg = None
        if not pg: break
        for p in pg:
            s = parse_signal(p["text"])
            if s and not p["reply_to"] and not p.get("fwd"):
                seen[p["id"]] = (s, p["ms"])
        before = min(p["id"] for p in pg)
    return sorted(seen.items())


def report(rows):
    done = [r for r in rows if r["status"] == "closed"]
    print(f"\nsignals {len(rows)} | filled+closed {len(done)} | unfilled {sum(r['status']=='unfilled' for r in rows)}"
          f" | unusable {sum(r['status']=='bad' for r in rows)} | timed out {sum(bool(r.get('timeout')) for r in done)}")
    for key, name in (("tp1_r", "ALL-IN, exit at TP1 / SL"), ("scale_r", "SCALE-OUT equal legs, BE after TP1")):
        v = [r[key] for r in done]
        if not v: continue
        eq = peak = dd = 0.0
        for x in v: eq += x; peak = max(peak, eq); dd = max(dd, peak - eq)
        print(f"{name:40} win {sum(x>0 for x in v)/len(v):.0%}  avg {sum(v)/len(v):+.2f}R  total {sum(v):+.1f}R  maxDD {dd:.1f}R")
    print("hit TP1+:", sum(r["hit"] >= 1 for r in done), "of", len(done), "| SL before any TP:", sum(r["hit"] == 0 for r in done))
    m = defaultdict(list)
    for r in done: m[r["month"]].append(r["scale_r"])
    for k in sorted(m): print(f"  {k}: n={len(m[k])} scale-out avg {sum(m[k])/len(m[k]):+.2f}R total {sum(m[k]):+.1f}R")


def demo():
    sg = dict(side="LONG", entries=[100, 100], stop=95, targets=[105, 110])
    C = lambda *x: [0, *x]
    assert sim(sg, 0, [C(100, 100, 99, 100), C(100, 106, 100, 105), C(105, 111, 104, 110)])["hit"] == 2
    assert sim(sg, 0, [C(100, 100, 99, 100), C(100, 106, 94, 95)])["tp1_r"] < -0.9          # SL wins ambiguous candle
    assert sim(sg, 0, [C(110, 111, 109, 110)])["status"] == "unfilled"
    print("ok")


def load():
    try: return {(r.setdefault("ch", "erfann_finance"), r["id"]): r for r in map(json.loads, open("paper_mirror.jsonl"))}
    except OSError: return {}


if __name__ == "__main__":
    demo()
    done, now = load(), time.time() * 1000          # settled rows are cached; only 'open' ones are replayed again
    try: copied = {ch: v.get("sig", {}) for ch, v in json.load(open("copy_state.json")).items()}
    except OSError: copied = {}
    for ch in json.load(open("channels.json")):
        for pid, (s, ms0) in signals(ch, int(sys.argv[1]) if len(sys.argv) > 1 else 60):
            posted = str(pid) in copied.get(ch, {})
            old = done.get((ch, pid))
            if old:
                if posted: old.update(posted=True, risk_pct=risk_pct(s))    # backfill rows cached before these fields
                if old.get("status") in ("closed", "unfilled", "bad"): continue
            elif not posted: continue                 # never reached our channel: not ours to score
            sym = mexc_symbol(s["symbol"])
            month = datetime.fromtimestamp(ms0 / 1000, timezone.utc).strftime("%Y-%m")
            row = dict(id=pid, ch=ch, ms=int(ms0), symbol=s["symbol"], side=s["side"], month=month, posted=posted, risk_pct=risk_pct(s))
            if not sym: done[(ch, pid)] = dict(row, status="bad"); continue
            try: res = sim(s, ms0, candles(sym, ms0))
            except Exception as ex: print("skip", ch, pid, s["symbol"], ex); continue
            age_h = (now - ms0) / 3600_000
            if (res.get("timeout") and age_h < MAX_H) or (res["status"] == "unfilled" and age_h < FILL_H):
                res = dict(status="open")             # not settled yet
            done[(ch, pid)] = dict(row, **res)
    rows = [done[k] for k in sorted(done)]
    open("paper_mirror.jsonl", "w").write("".join(json.dumps(r) + "\n" for r in rows))
    report(rows)
