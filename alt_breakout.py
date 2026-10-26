"""
Alt-Breakout Signal Pipeline (Long-Only, 4h) — Rule A v1.0 (published) + Rule B v1.0 (shadow).
Spec: see "Alt-Breakout" section of README.md. Run every 4h just after bar close.

  python alt_breakout.py              # live: post Rule A to Telegram, log everything
  python alt_breakout.py --dry-run    # compute + print, no Telegram, no state/log writes
  python alt_breakout.py --selftest   # offline check of indicators / exits / kill rules

Files: breakout_state.json (open+closed trades, kill switch), breakout_log.jsonl (append-only
signal/exit log, never edited).
"""
import scoreboard
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

DRY = "--dry-run" in sys.argv
STATE_FILE, LOG_FILE = "breakout_state.json", "breakout_log.jsonl"
HL = "https://api.hyperliquid.xyz/info"
H4 = 4 * 3600 * 1000

# Universe frozen 2026-10-17 (refresh quarterly by 24h volume; log changes in UNIVERSE_LOG).
UNIVERSE = ("ZEC NEAR XRP WLD TIA ZRO kPEPE INJ XMR SUI ARB UNI CRV AAVE DOT LINK DOGE FIL PYTH APT").split()
# Universe for Rule B (shadow): top-60 by volume on 2026-10-19 (core 20 + 40 extended). Extended coins backtested PF 0.87
# (core 20: PF 1.31) -> their signals are tagged. Rule C = shadow on the validated core 20 only, for comparison.
UNIVERSE_WIDE = UNIVERSE + """HYPE PUMP ENA VVV LIT ONDO STRK FARTCOIN XPL TAO BNB MON LTC SAND GRASS ADA AVAX HBAR SPX BCH ASTER FET TRUMP ETHFI MORPHO ICP JUP TRX LDO NIL XLM PENGU kBONK SYRUP SEI STX S W MET""".split()
UNIVERSE_LOG = {"2026-10-17": "initial universe", "2026-10-19": "added top-60 universe", "2026-10-21": "Rule A/B live on top-60; Rule C = core-20 shadow", "2026-10-21b": "Rule A restricted to core-20 (wide set PF 0.87); Rule B = top-60 shadow"}

P = dict(DONCH=16, ADX_MIN=25, CHOP_MAX=45, SL_ATR=2.0, TRAIL_ATR=3.0, TIME_BARS=30,
         MAX_OPEN=4, RISK_PCT=1.0, COST=0.002, VERSION="v1.0")
KILL = dict(PF=0.85, AVG_R=-0.15, DD=12.0, WINDOW=30)
SIGNAL_MAX_DELAY_MS = 3.9 * 3600 * 1000  # Actions cron lags 1-3h in practice; spec says 2 min. Stays < 1 bar so only the latest bar is ever evaluated
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
    for i in range(4):
        try:
            r = requests.post(HL, json=body, timeout=20)
            r.raise_for_status()
            time.sleep(0.25)   # ~120 calls/run for 61 coins: stay under HL's rate limit
            return r.json()
        except Exception as e:
            if i == 3:
                raise
            time.sleep(3 * (i + 1))


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


def pr(p):
    if p >= 1000: return f"{p:,.2f}"
    if p >= 1: return f"{p:.4f}"
    if p >= 0.01: return f"{p:.5f}"
    return f"{p:.8f}"


def lev_of(t):
    """Suggested leverage: same rule as the other channel bot (stop ≈ 10% of margin), 2x..10x."""
    return lev_pct(t["risk"] / t["entry"] * 100)


def lev_pct(risk_pct):
    return max(2, min(10, round(10 / max(risk_pct, 1))))


def targets(t):
    return t["entry"] + t["risk"], t["entry"] + 2 * t["risk"]     # +1R / +2R milestones; exit is the trail


def fmt_signal(t, n_open=None, bar_close_ms=None, now_ms=None):
    t1, t2 = targets(t)
    return (f"📊 SIGNAL #{t['id']:04d} | 4H | {utc(t['entry_T'] + 1)}\n"
            f"─────────────\n"
            f"#{t['coin']}/USDT\n"
            f"Direction: 🟢 LONG\n"
            f"Entry: {pr(t['entry'])}\n"
            f"Stop Loss: {pr(t['stop'])}\n"
            f"Target 1: {pr(t1)}\n"
            f"Target 2: {pr(t2)}\n"
            f"Leverage: x{lev_of(t)}")


def fmt_target(t, n):
    pct = n * t["risk"] / t["entry"] * 100
    return (f"🎯 TARGET {n} HIT — #{t['coin']}/USDT LONG\n"
            f"+{pct:.1f}% (x{lev_of(t)} ≈ +{pct * lev_of(t):.1f}%) 🚀"
            + ("\nStop trails up — the rest keeps running." if n == 1 else ""))


def net_pct(c):
    return c["r"] * c["risk"] / c["entry"] * 100


def fmt_exit(c, s=None):
    pct, lev = net_pct(c), lev_of(c)
    if c["reason"] == "TIME":
        head, line = ("✅" if pct > 0 else "❌"), "Closed at market ⏱️ (time exit)"
    elif pct > 0:
        head, line = "✅", "Trailing stop hit — profit locked 🎯"
    else:
        head, line = "❌", "Stop loss hit 🛑"
    return (f"{head} #{c['coin']}/USDT LONG — SIGNAL #{c['id']:04d}\n"
            f"{line}\n"
            f"{'Profit' if pct > 0 else 'Result'}: {pct:+.1f}% (x{lev} ≈ {pct * lev:+.1f}%)")


def channel_trades(st):
    """Every closed signal that was posted to the channel, from all sources -> (closed, n_open).
    Item: dict(src, tag, coin, pct, lev, r, T). Mirrored signals are scored by replay on real candles
    (paper_mirror.py), never by the source's own profit claims; signal_bot paper trades were never posted."""
    out = [dict(src="Breakout", tag=f"#{c['id']:04d}", coin=c["coin"], pct=net_pct(c), lev=lev_of(c), r=c["r"], T=c["exit_T"] + 1)
           for c in st["A"]["closed"]]
    n_open = len(st["A"]["open"])
    try:
        for t in map(json.loads, open("trades.jsonl")):
            if not t.get("paper") and t["outcome"] != "UNTRACKABLE":
                rp = abs(t["entry"] - t["stop"]) / t["entry"] * 100
                out.append(dict(src="Futures", tag="Futures", coin=t["symbol"].split("/")[0], pct=t["net_r"] * rp, lev=t["leverage"], r=t["net_r"],
                                T=int(datetime.strptime(t["closed_at"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc).timestamp() * 1000)))
        n_open += sum(not o.get("paper") for o in json.load(open("state.json")).get("open", []))
    except OSError:
        pass
    try:
        for m in map(json.loads, open("paper_mirror.jsonl")):
            if m.get("posted") and m["status"] == "closed":
                out.append(dict(src="Mirror", tag="Mirror", coin=m["symbol"], pct=m["scale_r"] * m["risk_pct"], lev=lev_pct(m["risk_pct"]),
                                r=m["scale_r"], T=m.get("t", m["ms"])))
            n_open += bool(m.get("posted")) and m["status"] == "open"
    except OSError:
        pass
    return sorted(out, key=lambda c: c["T"]), n_open


def fmt_report(kind, label, cs, all_closed, n_open):
    s = stats(cs)
    wins = [c for c in cs if c["r"] > 0]
    pf = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    best, worst = max(cs, key=lambda c: c["pct"]), min(cs, key=lambda c: c["pct"])
    by = {}
    for c in cs:
        by.setdefault(c["src"], []).append(c["r"])
    srcs = "   ·   ".join(f"{k} {len(v)} ({sum(v):+.1f}R)" for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])))
    rows = "\n".join(f"{'✅' if c['r'] > 0 else '❌'} {c['tag']} {c['coin']}/USDT  {c['pct']:+.1f}%  (x{c['lev']}: {c['pct'] * c['lev']:+.0f}%)"
                     for c in cs[:30])
    more = f"\n… +{len(cs) - 30} more" if len(cs) > 30 else ""
    bar = "━━━━━━━━━━━━━━━━━━━━"
    return (f"{'🏆' if kind == 'MONTHLY' else '📈'} {kind} PERFORMANCE REPORT\n📅 {label}\n{bar}\n\n"
            f"📊 Signals closed: {s['n']}\n"
            f"📡 {srcs}\n"
            f"✅ Winners: {len(wins)}   ❌ Losers: {s['n'] - len(wins)}\n"
            f"🎯 Win rate: {len(wins) / s['n'] * 100:.0f}%\n\n"
            f"💰 Net result: {s['total_r']:+.1f}R  ≈ {s['total_r'] * P['RISK_PCT']:+.1f}% of account at {P['RISK_PCT']:g}% risk/trade\n"
            f"📐 Profit factor: {pf}   ·   Avg {s['avg_r']:+.2f}R per trade\n\n"
            f"🥇 Best: {best['coin']}/USDT {best['pct']:+.1f}%\n"
            f"🥉 Worst: {worst['coin']}/USDT {worst['pct']:+.1f}%\n\n"
            f"{bar}\n{rows}{more}\n{bar}\n"
            f"📂 Still open: {n_open}   ·   All-time: {len(all_closed)} signals, {stats(all_closed)['total_r']:+.1f}R\n"
            f"🔎 Every signal is timestamped and never edited — losses included. Mirrored signals are scored on real candles, not on the source's claims.\n"
            f"⚠️ Not financial advice.")


def periodic_reports(st, now_ms):
    """Weekly (Mon 00:00 UTC, covers prior 7 days) and monthly (1st, covers prior calendar month); once each.
    Covers every signal posted to the channel (see channel_trades)."""
    d = datetime.fromtimestamp(now_ms / 1000, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    ms = lambda x: int(x.timestamp() * 1000)
    mon, first = d - timedelta(days=d.weekday()), d.replace(day=1)
    prev_first = (first - timedelta(days=1)).replace(day=1)
    for key, cur, lo, hi, kind, label in (
            ("last_weekly", ms(mon), ms(mon - timedelta(days=7)), ms(mon), "WEEKLY",
             f"{(mon - timedelta(days=7)):%d %b} – {(mon - timedelta(days=1)):%d %b %Y}"),
            ("last_monthly", ms(first), ms(prev_first), ms(first), "MONTHLY", f"{prev_first:%B %Y}")):
        if key not in st:
            st[key] = cur            # first run: arm silently, report from the next boundary on
        elif st[key] != cur:
            st[key] = cur
            closed, n_open = channel_trades(st)
            cs = [c for c in closed if lo <= c["T"] < hi]
            if cs:
                publish_photo(scoreboard.render(kind, label, [(c["r"] > 0, c["coin"], c["tag"], c["pct"], c["pct"] * c["lev"], c["r"]) for c in cs],
                                                stats(cs), n_open, P["RISK_PCT"]), f"{kind} scoreboard · {label}")
                publish(fmt_report(kind, label, cs, closed, n_open))


def publish(text, reply_to=None):
    """Post to the channel (plain text). Returns the Telegram message_id, or None."""
    print(text, "\n")
    if DRY:
        return None
    import os
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHANNEL_ID", "")
    body = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
    if reply_to:
        body.update(reply_to_message_id=reply_to, allow_sending_without_reply=True)
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", json=body, timeout=20)
        r.raise_for_status()
        print("✅ Message sent")
        return r.json()["result"]["message_id"]
    except Exception as e:
        print("❌ Telegram send failed:", e)
        return None


def publish_photo(png, caption):
    if DRY:
        return
    import os
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHANNEL_ID", "")
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendPhoto", data={"chat_id": chat, "caption": caption},
                      files={"photo": ("scoreboard.png", png)}, timeout=30).raise_for_status()
    except Exception as e:
        print("❌ Telegram photo failed:", e)


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
        return {r: dict(next_id=1, open=[], closed=[], suspended=None) for r in "ABC"}


# ---------- one run ----------
def run(now_ms=None):
    now_ms = now_ms or int(time.time() * 1000)
    bar_T = (now_ms // H4) * H4 - 1
    st = load_state()
    st.setdefault("C", dict(next_id=1, open=[], closed=[], suspended=None))   # state files from before Rule C

    d1 = {}
    f4 = {}
    excluded = {}
    for coin in dict.fromkeys(["BTC", *UNIVERSE_WIDE]):
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
    for rule in "ABC":
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
                for n, lvl in enumerate(targets(tr), 1):     # milestone replies (+1R / +2R); exit is still the trail
                    if b.h >= lvl and not tr.get(f"t{n}"):
                        tr[f"t{n}"] = True
                        if rule == "A":
                            publish(fmt_target(tr, n), tr.get("msg_id"))
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
                publish(fmt_exit(c), tr.get("msg_id"))
        s["open"] = still

        # 2) kill rules (A only; B and C are log-only shadows)
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
        for coin in (UNIVERSE if rule in ("A", "C") else UNIVERSE_WIDE):
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
            if rule == "A":
                t["msg_id"] = publish(fmt_signal(t))
            log({"ev": "signal", **t, "bar_T": bar_T})

    periodic_reports(st, now_ms)
    if not DRY:
        with open(STATE_FILE, "w") as f:
            json.dump(st, f, indent=1)
    print(f"done. A open={len(st['A']['open'])} closed={len(st['A']['closed'])} | B open={len(st['B']['open'])} closed={len(st['B']['closed'])} | C open={len(st['C']['open'])} closed={len(st['C']['closed'])}")


def test_post():
    """Send one clearly-labelled sample through the real formatter + Telegram path. No state, no log."""
    now = int(time.time() * 1000)
    t = new_trade("A", 0, "TEST", pd.Series(dict(c=1.0, atr=0.03, adx=30.0, chop=40.0, T=(now // H4) * H4 - 1)))
    mid = publish("🧪 PIPELINE TEST — not a trade signal.\n\n" + fmt_signal(t))
    publish(fmt_target(t, 1), mid)
    publish(fmt_exit(close_trade(t, 1.07, "TRAIL", now, 0.0)), mid)


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
    # messages: clean format, no clutter; report renders
    m = fmt_signal(t)
    assert all(k in m for k in ("Direction:", "Leverage:", "Entry:", "Stop Loss:", "Target 1:", "Target 2:")) and "Rule" not in m and "ATR" not in m
    assert lev_of(dict(entry=100.0, risk=6.0)) == 2 and lev_of(dict(entry=100.0, risk=1.0)) == 10
    cw = close_trade(t, 106.0, "TRAIL", 1, 0.0); cl = close_trade(t, 98.0, "STOP", 1, 0.0)
    assert fmt_exit(cw).startswith("✅") and "Profit" in fmt_exit(cw) and fmt_exit(cl).startswith("❌")
    import scoreboard
    png = scoreboard.render("MONTHLY", "October 2026", [(True, "ABC", "#0001", 6.0, 30.0, 1.5), (False, "XYZ", "Mirror", -2.0, -10.0, -1.0), (True, "DEF", "Futures", 11.0, 44.0, 2.4), (True, "GHI", "#0004", 3.0, 15.0, 0.9)], stats([cw, cl]), 1, 1.0)
    assert png[:4] == b"\x89PNG"
    open("/tmp/scoreboard_sample.png", "wb").write(png) if "--png" in sys.argv else None
    it = [dict(src="Breakout", tag="#0001", coin="X", pct=net_pct(cw), lev=lev_of(cw), r=cw["r"], T=1),
          dict(src="Mirror", tag="Mirror", coin="Y", pct=-2.0, lev=5, r=cl["r"], T=2)]
    rep = fmt_report("MONTHLY", "October 2026", it, it, 1)
    assert "Win rate: 50%" in rep and "Breakout 1" in rep and "Mirror 1" in rep and "Best:" in rep and len(rep) < 4000
    st0 = {"A": {"closed": [], "open": []}}
    periodic_reports(st0, 1792540799999); assert "last_weekly" in st0 and "last_monthly" in st0   # arms silently
    print("selftest ok")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else test_post() if "--test-post" in sys.argv else run()
