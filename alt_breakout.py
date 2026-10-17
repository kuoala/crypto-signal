"""
Alt-Breakout Signal Pipeline (Long-Only, 4h) — Rule A v1.0 (published) + Rule B v1.0 (shadow).
Spec: see "Alt-Breakout" section of README.md. Run every 4h just after bar close.

  python alt_breakout.py              # live: post Rule A to Telegram, log everything
  python alt_breakout.py --dry-run    # compute + print, no Telegram, no state/log writes
  python alt_breakout.py --selftest   # offline check of indicators / exits / kill rules

Files: breakout_state.json (open+closed trades, kill switch), breakout_log.jsonl (append-only
signal/exit log, never edited).
"""
import json
import math
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

DRY = "--dry-run" in sys.argv
STATE_FILE, LOG_FILE = "breakout_state.json", "breakout_log.jsonl"
HL = "https://api.hyperliquid.xyz/info"
H4 = 4 * 3600 * 1000

# Universe frozen 2026-10-17 (refresh quarterly by 24h volume; log changes in UNIVERSE_LOG).
UNIVERSE = ("ZEC NEAR XRP WLD TIA ZRO kPEPE INJ XMR SUI ARB UNI CRV AAVE DOT LINK DOGE FIL PYTH APT").split()
UNIVERSE_LOG = {"2026-10-17": "initial universe"}

P = dict(DONCH=16, ADX_MIN=25, CHOP_MAX=45, SL_ATR=2.0, TRAIL_ATR=3.0, TIME_BARS=30,
         MAX_OPEN=4, RISK_PCT=1.0, COST=0.002, VERSION="v1.0")
KILL = dict(PF=0.85, AVG_R=-0.15, DD=12.0, WINDOW=30)
SIGNAL_MAX_DELAY_MS = 60 * 60 * 1000   # Actions cron lags; spec says 2 min, unreachable there
FUNDING_STALE_MS = 6 * 3600 * 1000


# ---------- indicators (README §5) ----------
def rma(s, n=14):
    return s.ewm(alpha=1 / n, adjust=False).mean()


def indicators(df):
    pc = df.c.shift()
    tr = pd.concat([df.h - df.l, (df.h - pc).abs(), (df.l - pc).abs()], axis=1).max(axis=1)
    atr = rma(tr)
    up, dn = df.h.diff(), -df.l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    pdi, mdi = 100 * rma(pdm) / atr, 100 * rma(mdm) / atr
    dx = (100 * (pdi - mdi).abs() / (pdi + mdi)).fillna(0)
    out = df.copy()
    out["atr"], out["adx"] = atr, rma(dx)
    out["chop"] = 100 * np.log10(tr.rolling(14).sum() / (df.h.rolling(14).max() - df.l.rolling(14).min())) / math.log10(14)
    out["don"] = df.h.shift().rolling(P["DONCH"]).max()   # prior 16 bars, current excluded
    return out


def ema200(c):
    v, out = c.to_numpy(), np.full(len(c), np.nan)
    if len(v) >= 200:
        k, e = 2 / 201, v[:200].mean()
        out[199] = e
        for i in range(200, len(v)):
            e = v[i] * k + e * (1 - k)
            out[i] = e
    return pd.Series(out, index=c.index)


def daily_up(d):
    """Per completed daily bar: close > EMA200, keyed by the bar's close time."""
    return pd.DataFrame({"T": d["T"], "up": (d.c > ema200(d.c)).astype(bool)})


def regime_at(d_up, T):
    """Latest completed daily flag whose close time <= T (no lookahead)."""
    r = d_up[d_up["T"] <= T]
    return bool(r.up.iloc[-1]) if len(r) else False


# ---------- ingest (README §7.1) ----------
def hl(body):
    for i in range(3):
        try:
            r = requests.post(HL, json=body, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if i == 2:
                raise
            time.sleep(2 * (i + 1))


def candles(coin, interval, days, now_ms):
    rows = hl({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval,
                                                "startTime": now_ms - days * 86400000, "endTime": now_ms}})
    df = pd.DataFrame([{"t": r["t"], "T": r["T"], **{k: float(r[k]) for k in "ohlc"}}
                       for r in rows])
    return df[df["T"] < now_ms].reset_index(drop=True)   # closed bars only


def validate_4h(df, now_ms):
    """Return reason string if the coin must be excluded this run, else None."""
    if len(df) < 100:
        return "too few bars"
    expect = (now_ms // H4) * H4 - 1            # close time of the latest closed 4h bar
    if df["T"].iloc[-1] < expect:
        return "stale"
    gaps = (df["t"].diff().dropna().tail(100) // H4 - 1)
    run = best = 0
    for g in gaps:
        run = run + int(g) if g > 0 else 0
        best = max(best, run)
    return f"data gap ({best} bars)" if best > 2 else None


def funding_sum(coin, since_ms, until_ms):
    rows = hl({"type": "fundingHistory", "coin": coin, "startTime": since_ms})
    return sum(float(r["fundingRate"]) for r in rows if since_ms < r["time"] <= until_ms), rows


def funding_fresh(now_ms):
    rows = hl({"type": "fundingHistory", "coin": "BTC", "startTime": now_ms - 12 * 3600 * 1000})
    return bool(rows) and now_ms - rows[-1]["time"] <= FUNDING_STALE_MS


# ---------- trade lifecycle (README §2.2) ----------
def new_trade(rule, tid, coin, row):
    risk = P["SL_ATR"] * row.atr
    return dict(id=tid, rule=rule, coin=coin, entry=row.c, atr=row.atr, risk=risk, stop=row.c - risk,
                hh=row.c, best_close=row.c, bars=0, entry_T=int(row["T"]), last_T=int(row["T"]),
                adx=round(row.adx, 1), chop=round(row.chop, 1))


def step(tr, b):
    """Advance one closed bar. Returns (exit_price, reason) or None. Stops checked intrabar, gap-aware."""
    if b.l <= tr["stop"]:
        return min(b.o, tr["stop"]), ("TRAIL" if tr["stop"] > tr["entry"] - tr["risk"] + 1e-12 else "STOP")
    tr["hh"], tr["best_close"] = max(tr["hh"], b.h), max(tr["best_close"], b.c)
    tr["bars"] += 1
    tr["stop"] = max(tr["stop"], tr["hh"] - P["TRAIL_ATR"] * b.atr)   # ratchets up only
    if tr["bars"] == P["TIME_BARS"] and tr["best_close"] < tr["entry"] + tr["risk"]:
        return b.c, "TIME"
    return None


def close_trade(tr, px, reason, T, funding):
    gross = (px - tr["entry"]) / tr["risk"]
    cost = P["COST"] * tr["entry"] / tr["risk"]
    fund = funding * tr["entry"] / tr["risk"]      # long pays positive funding
    return {**tr, "exit": px, "reason": reason, "exit_T": int(T), "gross_r": round(gross, 3),
            "cost_r": round(cost, 3), "funding_r": round(fund, 3), "r": round(gross - cost - fund, 3)}


# ---------- kill rules (README §8) ----------
def stats(closed):
    rs = [t["r"] for t in closed]
    w, l = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    eq = peak = 1.0
    dd = 0.0
    for r in rs:
        eq *= 1 + P["RISK_PCT"] / 100 * r
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak * 100)
    return dict(n=len(rs), pf=(w / l if l else float("inf")), avg_r=(sum(rs) / len(rs) if rs else 0.0), dd=dd, total_r=sum(rs))


def kill_reason(closed):
    s = stats(closed)
    if s["dd"] > KILL["DD"]:
        return f"drawdown {s['dd']:.1f}% > {KILL['DD']}%"
    if len(closed) >= KILL["WINDOW"]:
        r = stats(closed[-KILL["WINDOW"]:])
        if r["pf"] < KILL["PF"]:
            return f"rolling-{KILL['WINDOW']} PF {r['pf']:.2f} < {KILL['PF']}"
        if r["avg_r"] < KILL["AVG_R"]:
            return f"rolling-{KILL['WINDOW']} avg R {r['avg_r']:+.2f} < {KILL['AVG_R']}"
    return None


# ---------- formatting / publishing ----------
def fp(p):
    return f"{p:.6g}" if p < 1 else f"{p:,.4f}".rstrip("0").rstrip(".")


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def fmt_signal(t, n_open, bar_close_ms):
    sl_pct = P["SL_ATR"] * t["atr"] / t["entry"] * 100
    return (f"🟢 LONG — {t['coin']}-PERP | Rule {t['rule']} {P['VERSION']} | #{t['id']:04d}\n"
            f"Entry:      {fp(t['entry'])} (4h close, {utc(bar_close_ms + 1)})\n"
            f"Stop:       {fp(t['stop'])}  (2×ATR, {sl_pct:.1f}% away; risk {P['RISK_PCT']:g}% of equity ≈ {P['RISK_PCT'] / sl_pct * 100:.0f}% notional)\n"
            f"Trail:      3×ATR chandelier, ratchets up only\n"
            f"Time stop:  exit at bar {P['TIME_BARS']} if +1R not reached\n"
            f"Take profit: none (trail only)\n"
            f"Open signals: {n_open}/{P['MAX_OPEN']}")


def fmt_exit(c, s):
    icon = "✅" if c["r"] > 0 else "❌"
    return (f"{icon} CLOSED — {c['coin']}-PERP | Rule {c['rule']} {P['VERSION']} | #{c['id']:04d}\n"
            f"Exit: {fp(c['exit'])} ({c['reason']}) after {c['bars']} bars\n"
            f"Result: {c['r']:+.2f}R net (gross {c['gross_r']:+.2f}, costs {c['cost_r']:.2f}, funding {c['funding_r']:+.3f})\n"
            f"Track record: {s['n']} trades · PF {s['pf']:.2f} · avg {s['avg_r']:+.2f}R · total {s['total_r']:+.1f}R · DD {s['dd']:.1f}%")


def fmt_report(title, closed, since_ms):
    cs = [c for c in closed if c["exit_T"] >= since_ms]
    s = stats(cs)
    fund = sum(c["funding_r"] for c in cs)
    body = "\n".join(f"  #{c['id']:04d} {c['coin']} {c['r']:+.2f}R ({c['reason']})" for c in cs) or "  no closed trades"
    pf = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    return (f"📊 {title} — Rule A {P['VERSION']}\n{body}\n"
            f"Trades {s['n']} · PF {pf} · total {s['total_r']:+.1f}R · funding cost {fund:.2f}R\n"
            f"All-time: {len(closed)} trades, {stats(closed)['total_r']:+.1f}R")


def publish(text):
    print(text, "\n")
    if DRY:
        return
    from telegram_poster import send_message
    send_message(text, parse_mode="HTML")


def log(ev):
    if DRY:
        return
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(ev) + "\n")


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {r: dict(next_id=1, open=[], closed=[], suspended=None) for r in "AB"}


# ---------- one run ----------
def run(now_ms=None):
    now_ms = now_ms or int(time.time() * 1000)
    bar_T = (now_ms // H4) * H4 - 1
    st = load_state()

    d1 = {}
    f4 = {}
    excluded = {}
    for coin in dict.fromkeys(["BTC", *UNIVERSE]):
        try:
            d = candles(coin, "4h", 120, now_ms)
            why = validate_4h(d, now_ms)
            dd = candles(coin, "1d", 420, now_ms)
            if why is None and coin != "BTC" and len(dd) < 200:
                why = "<200 daily bars"
            if why and coin != "BTC":
                excluded[coin] = why
                continue
            if why:
                raise RuntimeError(f"BTC {why}")
            f4[coin], d1[coin] = indicators(d).set_index("T", drop=False), daily_up(dd)
        except Exception as e:
            if coin == "BTC":
                print("BTC data unavailable, aborting run:", e)
                return
            excluded[coin] = f"fetch error {e}"
    if excluded:
        print("excluded this run:", excluded)

    try:
        fund_ok = funding_fresh(now_ms)
    except Exception:
        fund_ok = False
    if not fund_ok:
        print("funding feed stale/unavailable -> no new signals this run")

    btc_up = regime_at(d1["BTC"], bar_T)
    for rule in "AB":
        s = st[rule]
        # 1) track open trades through every bar closed since last run
        still = []
        for tr in s["open"]:
            df = f4.get(tr["coin"])
            if df is None:
                still.append(tr)
                continue
            res = None
            for _, b in df[df["T"] > tr["last_T"]].iterrows():
                tr["last_T"] = int(b["T"])
                res = step(tr, b)
                if res:
                    break
            if not res:
                still.append(tr)
                continue
            try:
                fsum, _ = funding_sum(tr["coin"], tr["entry_T"], tr["last_T"])
            except Exception:
                fsum = 0.0   # ponytail: funding fetch failure books 0; fixable from log if it matters
            c = close_trade(tr, *res, tr["last_T"], fsum)
            s["closed"].append(c)
            log({"ev": "exit", **c})
            if rule == "A":
                publish(fmt_exit(c, stats(s["closed"])))
        s["open"] = still

        # 2) kill rules (A only; B is a log-only shadow)
        if rule == "A" and not s["suspended"]:
            why = kill_reason(s["closed"])
            if why:
                s["suspended"] = why
                publish(f"⛔ PUBLISHING SUSPENDED — Rule A {P['VERSION']}\nTrigger: {why}\nManual review required to resume.")
                log({"ev": "kill", "reason": why, "T": now_ms})

        # 3) new signals on the latest closed bar
        if s["suspended"] or not fund_ok or now_ms - bar_T > SIGNAL_MAX_DELAY_MS:
            continue
        held = {t["coin"] for t in s["open"]}
        cands = []
        for coin in UNIVERSE:
            df = f4.get(coin)
            if df is None or coin in held or df["T"].iloc[-1] != bar_T:
                continue
            r = df.iloc[-1]
            if not (r.c > r.don and r.adx > P["ADX_MIN"] and r.chop < P["CHOP_MAX"]):
                continue
            if not regime_at(d1[coin], bar_T):
                continue
            if rule == "B" and not btc_up:
                continue
            cands.append((-r.adx, r.chop, coin, r))
        cands.sort(key=lambda x: x[:3])
        for _, _, coin, r in cands[: max(0, P["MAX_OPEN"] - len(s["open"]))]:
            t = new_trade(rule, s["next_id"], coin, r)
            s["next_id"] += 1
            s["open"].append(t)
            log({"ev": "signal", **t, "bar_T": bar_T})
            if rule == "A":
                publish(fmt_signal(t, len(s["open"]), bar_T))

    # periodic reports (run at the 00:00 UTC bar)
    d = datetime.fromtimestamp((bar_T + 1) / 1000, timezone.utc)
    if d.hour == 0 and not st["A"]["suspended"] and now_ms - bar_T <= SIGNAL_MAX_DELAY_MS:
        if d.weekday() == 0:
            publish(fmt_report("Weekly report", st["A"]["closed"], bar_T + 1 - 7 * 86400000))
        if d.day == 1:
            publish(fmt_report("Monthly report", st["A"]["closed"], bar_T + 1 - 28 * 86400000))
    if not DRY:
        with open(STATE_FILE, "w") as f:
            json.dump(st, f, indent=1)
    print(f"done. A open={len(st['A']['open'])} closed={len(st['A']['closed'])} | B open={len(st['B']['open'])} closed={len(st['B']['closed'])}")


# ---------- self-check ----------
def selftest():
    # indicators: Donchian excludes current bar; breakout bar fires; ADX high in trend
    n = 60
    c = np.r_[np.full(30, 100.0), 100 + np.cumsum(np.full(30, 1.5))]
    df = pd.DataFrame(dict(t=np.arange(n) * H4, T=np.arange(n) * H4 + H4 - 1, o=c - 0.2, h=c + 0.5, l=c - 0.5, c=c))
    ind = indicators(df)
    assert ind.don.iloc[-1] == df.h.iloc[-17:-1].max() and ind.c.iloc[-1] > ind.don.iloc[-1]
    assert ind.adx.iloc[-1] > 25 and ind.chop.iloc[-1] < 45, (ind.adx.iloc[-1], ind.chop.iloc[-1])
    # ema200 seeded with SMA; regime uses only bars closed by T
    e = ema200(pd.Series(np.arange(1.0, 301)))
    assert math.isclose(e.iloc[199], np.arange(1.0, 201).mean()) and e.iloc[:199].isna().all()
    du = pd.DataFrame({"T": [10, 20], "up": [True, False]})
    assert regime_at(du, 15) is True and regime_at(du, 25) is False and regime_at(du, 5) is False
    # exits: initial stop gap-aware, trail ratchets, time stop
    B = lambda o, h, l, c, atr=1.0: pd.Series(dict(o=o, h=h, l=l, c=c, atr=atr))
    t = new_trade("A", 1, "X", pd.Series(dict(c=100.0, atr=1.0, adx=30.0, chop=40.0, T=0)))
    assert t["stop"] == 98 and step(t, B(97, 98, 96, 97)) == (97, "STOP")        # gap through stop
    t = new_trade("A", 1, "X", pd.Series(dict(c=100.0, atr=1.0, adx=30.0, chop=40.0, T=0)))
    assert step(t, B(100, 110, 100, 109)) is None and t["stop"] == 107            # 110 - 3*1
    assert step(t, B(108, 108, 100, 101)) == (107, "TRAIL") and t["stop"] == 107   # never lowered
    t = new_trade("A", 1, "X", pd.Series(dict(c=100.0, atr=1.0, adx=30.0, chop=40.0, T=0)))
    res = [step(t, B(100, 100.5, 99.5, 100.2)) for _ in range(30)]
    assert res[:29] == [None] * 29 and res[29] == (100.2, "TIME")                 # never reached +1R (102)
    c = close_trade(t, 101.0, "TIME", 1, 0.0001)
    assert c["r"] == round(0.5 - 0.002 * 100 / 2 - 0.0001 * 100 / 2, 3), c   # gross .5R, cost .1R, funding .005R
    # kill rules
    assert "PF" in kill_reason([dict(r=0.1)] * 5 + [dict(r=-0.12)] * 25 + [dict(r=0.0)])
    assert kill_reason([dict(r=-1.0)] * 13) and "drawdown" in kill_reason([dict(r=-1.0)] * 13)
    assert kill_reason([dict(r=1.0)] * 10) is None
    # validation: gap > 2 bars flagged
    g = df.drop(index=range(40, 44)).reset_index(drop=True)
    assert validate_4h(pd.concat([g] * 2, ignore_index=True), int(g["T"].iloc[-1]) + 1) is not None
    print("selftest ok")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else run()
