#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Crypto Futures Signal Bot  (Binance USDT-Perpetual)
---------------------------------------------------
- 100% real, free data from Binance (no API key required)
- Scans the entire USDT futures market
- Combined strategy: EMA cross + RSI + MACD (direction), ATR (stop loss), R/R (target)
- Trust features: sequential signal ID, UTC timestamp, risk/reward, one-line reason,
  TradingView chart link
- Outcome tracker: watches open signals and auto-posts TP/SL results + monthly win rate
- Auto-posts to a Telegram channel
- Default timeframe: 4h

State is persisted in state.json (next ID, open signals, monthly stats).

Install:  pip install ccxt pandas numpy requests
Run:      python signal_bot.py
"""

import os
import json
import time
import math
from datetime import datetime, timezone

import requests
import numpy as np
import pandas as pd
import ccxt

# ===================== SETTINGS =====================
CONFIG = {
    # --- Telegram ---
    # TELEGRAM_CHANNEL_ID matches the existing repo secret; TELEGRAM_CHAT_ID is accepted as a fallback.
    "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE"),
    "TELEGRAM_CHAT_ID":   os.getenv("TELEGRAM_CHANNEL_ID") or os.getenv("TELEGRAM_CHAT_ID", "PUT_YOUR_CHANNEL_ID_HERE"),

    # --- Data source ---
    # Tried in order; the first reachable one is used. Binance is best but returns HTTP 451
    # from US IPs (e.g. GitHub Actions runners), so reachable fallbacks follow.
    "EXCHANGES": ["binanceusdm", "bybit", "okx", "gateio"],

    # --- Market / scan ---
    "TIMEFRAME": "4h",          # analysis timeframe
    "CANDLES": 200,             # number of candles used for indicators
    "QUOTE": "USDT",            # USDT pairs only
    "MIN_24H_VOLUME_USDT": 30_000_000,   # liquidity filter (drop low-volume coins)
    "MAX_SIGNALS_PER_RUN": 5,   # max NEW signals per run (strongest ones)

    # --- State / tracking ---
    "STATE_FILE": "state.json",
    "RESULT_CHECK_TF": "1h",    # finer timeframe used to detect TP/SL hits between runs

    # --- Risk management ---
    "ATR_PERIOD": 14,
    "ATR_SL_MULT": 1.5,         # stop loss = ATR × this multiplier
    "RR_RATIO": 2.0,            # target based on risk/reward ratio
    "MAX_LEVERAGE": 10,         # max suggested leverage

    # --- Indicators ---
    "EMA_FAST": 20,
    "EMA_SLOW": 50,
    "RSI_PERIOD": 14,
    "RSI_LONG_MIN": 45,         # for LONG, RSI must be above this
    "RSI_SHORT_MAX": 55,        # for SHORT, RSI must be below this

    # If True, only prints and does NOT send to Telegram or write state (for testing)
    "DRY_RUN": False,
}

# ===================== INDICATORS =====================
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def macd(series, fast=12, slow=26, signal=9):
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

def atr(df, period=14):
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

# ===================== STATE =====================
def load_state():
    path = CONFIG["STATE_FILE"]
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            st = {}
    else:
        st = {}
    st.setdefault("next_id", 1)
    st.setdefault("open", [])          # list of open signal dicts
    st.setdefault("stats", {})         # {"YYYY-MM": {"tp": n, "sl": n}}
    return st

def save_state(st):
    if CONFIG["DRY_RUN"]:
        return
    with open(CONFIG["STATE_FILE"], "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)

def now_utc_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def month_key():
    return datetime.now(timezone.utc).strftime("%Y-%m")

# ===================== SIGNAL CORE =====================
# TradingView symbol prefix per exchange (for the chart link)
TV_PREFIX = {
    "binanceusdm": "BINANCE", "binance": "BINANCE",
    "bybit": "BYBIT", "okx": "OKX", "gateio": "GATEIO",
}
EXCHANGE_NAME = "binanceusdm"   # set at runtime to whichever exchange connects

def build_exchange():
    """Try exchanges in order; return the first one whose markets load."""
    global EXCHANGE_NAME
    last_err = None
    for name in CONFIG["EXCHANGES"]:
        try:
            klass = getattr(ccxt, name)
            ex = klass({"enableRateLimit": True, "options": {"defaultType": "swap"}})
            ex.load_markets()
            EXCHANGE_NAME = name
            print(f"[+] Connected to: {name}")
            return ex
        except Exception as e:
            last_err = e
            print(f"[!] {name} unreachable ({type(e).__name__}); trying next...")
    raise RuntimeError(f"No exchange reachable. Last error: {last_err}")

def fetch_markets(ex):
    """List active USDT-Perpetual pairs with high volume."""
    markets = ex.load_markets()
    tickers = ex.fetch_tickers()

    # Diagnostic counters
    n_inactive = n_wrong_quote = n_not_deriv = n_inverse = n_no_ticker = n_low_vol = 0

    out = []
    for sym, m in markets.items():
        if not m.get("active"):
            n_inactive += 1; continue
        if m.get("quote") != CONFIG["QUOTE"]:
            n_wrong_quote += 1; continue
        if not (m.get("swap") or m.get("future")):
            n_not_deriv += 1; continue
        # Accept linear=True or linear=None (some exchanges don't set it); reject linear=False (inverse)
        if m.get("linear") is False:
            n_inverse += 1; continue
        t = tickers.get(sym)
        if not t:
            n_no_ticker += 1; continue
        qvol = t.get("quoteVolume") or 0
        if qvol < CONFIG["MIN_24H_VOLUME_USDT"]:
            n_low_vol += 1; continue
        out.append((sym, qvol))

    print(f"    [dbg] Market filter stats: inactive={n_inactive}, wrong_quote={n_wrong_quote}, "
          f"not_deriv={n_not_deriv}, inverse={n_inverse}, no_ticker={n_no_ticker}, "
          f"low_vol={n_low_vol}, passed={len(out)}")

    out.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in out]

def analyze(ex, symbol):
    """Analyze one pair; return a signal dict if a signal exists, else None."""
    try:
        ohlcv = ex.fetch_ohlcv(symbol, timeframe=CONFIG["TIMEFRAME"], limit=CONFIG["CANDLES"])
    except Exception:
        return None
    if not ohlcv or len(ohlcv) < 60:
        return None

    df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
    close = df["close"]

    ema_f = ema(close, CONFIG["EMA_FAST"])
    ema_s = ema(close, CONFIG["EMA_SLOW"])
    r = rsi(close, CONFIG["RSI_PERIOD"])
    _, _, hist = macd(close)
    a = atr(df, CONFIG["ATR_PERIOD"])

    # latest values
    c = close.iloc[-1]
    ef, es = ema_f.iloc[-1], ema_s.iloc[-1]
    ef_prev, es_prev = ema_f.iloc[-2], ema_s.iloc[-2]
    rv = r.iloc[-1]
    h, h_prev = hist.iloc[-1], hist.iloc[-2]
    av = a.iloc[-1]

    if any(map(lambda x: x is None or (isinstance(x, float) and math.isnan(x)),
               [c, ef, es, rv, h, av])):
        return None

    direction = None
    score = 0

    long_trend = ef > es
    long_cross = ef_prev <= es_prev and ef > es        # fresh cross
    long_rsi = rv >= CONFIG["RSI_LONG_MIN"]
    long_macd = h > 0 and h > h_prev

    short_trend = ef < es
    short_cross = ef_prev >= es_prev and ef < es
    short_rsi = rv <= CONFIG["RSI_SHORT_MAX"]
    short_macd = h < 0 and h < h_prev

    if long_trend and long_rsi and long_macd:
        direction = "LONG"
        score = sum([long_trend, long_cross, long_rsi, long_macd])
    elif short_trend and short_rsi and short_macd:
        direction = "SHORT"
        score = sum([short_trend, short_cross, short_rsi, short_macd])
    else:
        return None

    entry = c
    sl_dist = av * CONFIG["ATR_SL_MULT"]
    if sl_dist <= 0:
        return None

    if direction == "LONG":
        stop = entry - sl_dist
        target = entry + sl_dist * CONFIG["RR_RATIO"]
    else:
        stop = entry + sl_dist
        target = entry - sl_dist * CONFIG["RR_RATIO"]

    sl_pct = abs(entry - stop) / entry * 100
    if sl_pct <= 0:
        return None
    suggested_lev = max(2, min(CONFIG["MAX_LEVERAGE"], round(10 / max(sl_pct, 1))))

    # One-line reason
    macd_txt = "crossed up" if direction == "LONG" else "crossed down"
    bias = "bullish" if direction == "LONG" else "bearish"
    cmp = ">" if direction == "LONG" else "<"
    reason = (f"EMA{CONFIG['EMA_FAST']} {cmp} EMA{CONFIG['EMA_SLOW']}, "
              f"RSI {rv:.0f} ({bias}), MACD {macd_txt} — {CONFIG['TIMEFRAME']} trend confirmed.")

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
        "leverage": suggested_lev,
        "score": score,
        "rsi": rv,
        "sl_pct": sl_pct,
        "reason": reason,
    }

# ===================== FORMAT & SEND =====================
def fmt_price(p):
    if p >= 1000:   return f"{p:,.2f}"
    if p >= 1:      return f"{p:.4f}"
    if p >= 0.01:   return f"{p:.5f}"
    return f"{p:.8f}"

def tradingview_link(symbol):
    base = symbol.split("/")[0]
    prefix = TV_PREFIX.get(EXCHANGE_NAME, "BINANCE")
    return f"https://www.tradingview.com/chart/?symbol={prefix}:{base}USDT.P"

def format_signal(s):
    base = s["symbol"].split("/")[0]
    arrow = "🟢 LONG" if s["direction"] == "LONG" else "🔴 SHORT"
    rr = f"1:{CONFIG['RR_RATIO']:g}"
    sid = f"{s['id']:04d}"
    return (
        f"📊 SIGNAL #{sid} | {CONFIG['TIMEFRAME'].upper()} | {s['opened_at']}\n"
        f"─────────────\n"
        f"#{base}/USDT\n"
        f"Direction: {arrow}\n"
        f"Entry: {fmt_price(s['entry'])}\n"
        f"Stop Loss: {fmt_price(s['stop'])}\n"
        f"Target: {fmt_price(s['target'])}\n"
        f"Leverage: x{s['leverage']}\n"
        f"Risk/Reward: {rr}\n\n"
        f"📈 Why: {s['reason']}\n"
        f"📉 Chart: {tradingview_link(s['symbol'])}"
    )

def format_result(s, outcome, pct, stats_line):
    base = s["symbol"].split("/")[0]
    sid = f"{s['id']:04d}"
    if outcome == "TP":
        head = f"✅ RESULT — SIGNAL #{sid} (#{base}/USDT {s['direction']})"
        line = f"Target hit 🎯 +{pct:.1f}% (x{s['leverage']} = +{pct*s['leverage']:.1f}%)"
    else:
        head = f"❌ RESULT — SIGNAL #{sid} (#{base}/USDT {s['direction']})"
        line = f"Stop loss hit 🛑 -{pct:.1f}% (x{s['leverage']} = -{pct*s['leverage']:.1f}%)"
    return (
        f"{head}\n"
        f"{line}\n"
        f"Closed {now_utc_str()}\n"
        f"─────────────\n"
        f"{stats_line}"
    )

def stats_line():
    mk = month_key()
    st = STATE["stats"].get(mk, {"tp": 0, "sl": 0})
    tp, sl = st["tp"], st["sl"]
    total = tp + sl
    wr = (tp / total * 100) if total else 0
    month_name = datetime.now(timezone.utc).strftime("%B")
    return (f"📊 {month_name}: {total} signals · {tp} TP ✅ · {sl} SL ❌ · "
            f"Win rate {wr:.0f}%")

def send_telegram(text):
    token = CONFIG["TELEGRAM_BOT_TOKEN"]
    chat = CONFIG["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, data={
        "chat_id": chat,
        "text": text,
        "disable_web_page_preview": True,
    }, timeout=20)
    resp.raise_for_status()
    return resp.json()

def check_telegram():
    """Verify bot token and chat ID are set and working. Prints diagnostics."""
    token = CONFIG["TELEGRAM_BOT_TOKEN"]
    chat = CONFIG["TELEGRAM_CHAT_ID"]
    if "PUT_YOUR" in token or "PUT_YOUR" in chat:
        print("[!] Telegram credentials are placeholders — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID secrets.")
        return False
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        data = r.json()
        if not data.get("ok"):
            print(f"[!] Telegram getMe failed: {data}")
            return False
        print(f"[+] Telegram bot OK: @{data['result']['username']} | chat_id={chat}")
        return True
    except Exception as e:
        print(f"[!] Telegram connectivity error: {e}")
        return False

def post(text):
    print(text + "\n" + "-" * 30)
    if not CONFIG["DRY_RUN"]:
        try:
            send_telegram(text)
            time.sleep(1)  # respect Telegram rate limits
        except Exception as e:
            print(f"[!] Telegram send error: {e}")

# ===================== OUTCOME TRACKER =====================
def check_outcome(ex, sig):
    """Return 'TP', 'SL', or None by scanning candles since the signal opened."""
    try:
        ohlcv = ex.fetch_ohlcv(sig["symbol"], timeframe=CONFIG["RESULT_CHECK_TF"], limit=200)
    except Exception:
        return None
    opened_ms = sig.get("opened_ms", 0)
    for ts, o, h, l, c, v in ohlcv:
        if ts < opened_ms:
            continue
        if sig["direction"] == "LONG":
            if l <= sig["stop"]:
                return "SL"
            if h >= sig["target"]:
                return "TP"
        else:
            if h >= sig["stop"]:
                return "SL"
            if l <= sig["target"]:
                return "TP"
    return None

def process_open_signals(ex):
    """Check open signals, post results, update stats. Returns symbols still open."""
    still_open = []
    for sig in STATE["open"]:
        outcome = check_outcome(ex, sig)
        if outcome is None:
            still_open.append(sig)
            continue
        # update monthly stats
        mk = month_key()
        STATE["stats"].setdefault(mk, {"tp": 0, "sl": 0})
        STATE["stats"][mk]["tp" if outcome == "TP" else "sl"] += 1
        # compute pct move (entry -> target/stop)
        ref = sig["target"] if outcome == "TP" else sig["stop"]
        pct = abs(ref - sig["entry"]) / sig["entry"] * 100
        post(format_result(sig, outcome, pct, stats_line()))
    STATE["open"] = still_open

# ===================== RUN =====================
STATE = {}

def run_once():
    global STATE
    STATE = load_state()
    check_telegram()
    ex = build_exchange()

    print("[+] Checking open signals for TP/SL...")
    process_open_signals(ex)

    print("[+] Loading markets...")
    symbols = fetch_markets(ex)
    open_symbols = {s["symbol"] for s in STATE["open"]}
    print(f"[+] Found {len(symbols)} USDT pairs. Scanning ({CONFIG['TIMEFRAME']})...")

    new_signals = []
    for i, sym in enumerate(symbols):
        if sym in open_symbols:
            continue  # don't open a duplicate while one is still running
        s = analyze(ex, sym)
        if s:
            new_signals.append(s)
        if (i + 1) % 25 == 0:
            print(f"    ...{i+1}/{len(symbols)} checked, {len(new_signals)} signals so far")

    new_signals.sort(key=lambda x: (x["score"], abs(x["rsi"] - 50)), reverse=True)
    top = new_signals[:CONFIG["MAX_SIGNALS_PER_RUN"]]

    print(f"\n[+] {len(new_signals)} new signals found. Posting top {len(top)}:\n")
    for s in top:
        s["id"] = STATE["next_id"]
        STATE["next_id"] += 1
        s["opened_at"] = now_utc_str()
        s["opened_ms"] = int(datetime.now(timezone.utc).timestamp() * 1000)
        post(format_signal(s))
        # store a compact record for tracking
        STATE["open"].append({
            "id": s["id"], "symbol": s["symbol"], "direction": s["direction"],
            "entry": s["entry"], "stop": s["stop"], "target": s["target"],
            "leverage": s["leverage"], "opened_at": s["opened_at"], "opened_ms": s["opened_ms"],
        })

    if not top:
        print("[i] No new signals this run.")

    save_state(STATE)
    print("[+] Done. State saved.")

if __name__ == "__main__":
    run_once()
