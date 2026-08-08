#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Crypto Futures Signal Bot  (Binance USDT-Perpetual)
---------------------------------------------------
- 100% real, free data from Binance (no API key required)
- Scans the most liquid USDT futures pairs (MAX_SCAN_SYMBOLS)
- Combined strategy: EMA cross + RSI + MACD (direction), ATR (stop loss), R/R (target)
- Quality filters (raise win rate): ADX trend-strength gate, BTC-regime + 1d higher-TF
  alignment, fresh-momentum gate (no chasing), RSI exhaustion guard, and stablecoin/
  pegged-asset + micro-move + blow-off-volatility exclusion
- Trust features: sequential signal ID, UTC timestamp, risk/reward, one-line reason,
  TradingView chart link
- Outcome tracker: watches open signals and auto-posts TP/SL results + monthly win rate
- Auto-posts to a Telegram channel
- Default timeframe: 4h

Filter values are not guesses — see research/ for the backtest harness that chose them,
and research/verify_parity.py, which checks that harness against analyze() itself.

State is persisted in state.json (next ID, open signals, monthly stats); every closed
trade is appended to trades.jsonl.

Install:  pip install ccxt pandas numpy requests
Run:      python signal_bot.py
"""

import os
import re
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
    "EXCHANGES": ["binanceusdm", "bybit", "okx", "mexc", "gateio", "htx", "phemex"],

    # --- Market / scan ---
    "TIMEFRAME": "4h",          # analysis timeframe
    "CANDLES": 200,             # number of candles used for indicators
    "QUOTE": "USDT",            # USDT pairs only
    "MIN_24H_VOLUME_USDT": 5_000_000,    # liquidity filter (drop low-volume coins)
    "MAX_SCAN_SYMBOLS": 40,     # only scan the N most liquid pairs (see note below)
    "MAX_SIGNALS_PER_RUN": 5,   # max NEW signals per run (strongest ones)

    # --- State / tracking ---
    "STATE_FILE": "state.json",
    "TRADE_LOG": "trades.jsonl",  # one line per closed trade, for backtest/audit
    "RESULT_CHECK_TF": "1h",    # finer timeframe used to detect TP/SL hits between runs
    "MAX_HOLD_DAYS": 10,        # close anything still open after this, at market

    # --- Risk management ---
    "ATR_PERIOD": 14,
    "ATR_SL_MULT": 1.5,         # stop loss = ATR × this multiplier
    "RR_RATIO": 2.0,            # target based on risk/reward ratio
    "TP1_FRACTION": 0.3,        # portion of the position booked at Target 1 (+1R)
    "MAX_LEVERAGE": 10,         # max suggested leverage

    # --- Indicators ---
    "EMA_FAST": 20,
    "EMA_SLOW": 50,
    "RSI_PERIOD": 14,
    "RSI_LONG_MIN": 45,         # for LONG, RSI must be above this
    "RSI_SHORT_MAX": 55,        # for SHORT, RSI must be below this

    # --- Signal quality filters (raise win rate: fewer, higher-quality signals) ---
    "REGIME_FILTER": True,      # only trade in the direction of the BTC market trend
    "HTF_CONFIRM": True,        # require the higher timeframe (1d) trend to agree
    "HTF_TIMEFRAME": "1d",
    "FRESH_BARS": 3,            # momentum must have turned within the last N bars (no chasing)
    "RSI_LONG_MAX": 72,         # don't buy into overbought exhaustion
    "RSI_SHORT_MIN": 28,        # don't sell into oversold exhaustion
    "MIN_SL_PCT": 1.0,          # skip signals whose stop is < this % away (peg / micro-move noise)
    "ADX_PERIOD": 14,
    "ADX_MIN": 22,              # require a real trend, not a chop-driven EMA/MACD cross
    "MAX_ATR_PCT": 9.0,         # skip blow-off volatility (stop would be absurdly wide)

    # --- Memecoin signals ---
    # Off by default: over Sep 2025-Aug 2026 memecoin signals produced -0.15R per
    # trade out of sample and no filter combination rescued them (see research/).
    "MEME_ENABLED": False,
    "MEME_MIN_24H_VOLUME_USDT": 1_000_000,  # lower floor — memecoins have thinner books
    "MEME_MAX_SIGNALS_PER_RUN": 2,          # max NEW memecoin signals per run
    "MEME_MAX_LEVERAGE": 5,                 # cap leverage — memecoins are very volatile
    "MEME_ATR_SL_MULT": 2.0,                # wider stop to survive memecoin wicks

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

def true_range(df):
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    return pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

def atr(df, period=14):
    return true_range(df).rolling(period).mean()

def adx(df, period=14):
    """Average Directional Index — trend strength, direction-agnostic.

    Used as a chop filter: EMA/MACD crosses inside a range are the dominant
    source of losing signals, and those bars have a low ADX.
    """
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    smooth_tr = true_range(df).ewm(alpha=1 / period, adjust=False).mean()
    pdi = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / smooth_tr
    mdi = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / smooth_tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean()

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
    "mexc": "MEXC", "htx": "HTX", "phemex": "PHEMEX",
}

# Per-exchange ccxt constructor options
EXCHANGE_OPTIONS = {
    "binanceusdm": {"defaultType": "future"},
    "bybit":       {"defaultType": "swap"},
    "okx":         {},          # OKX load_markets has a TypeError bug in this ccxt version
    "mexc":        {"defaultType": "swap"},
    "gateio":      {"defaultType": "swap"},
    "htx":         {"defaultType": "swap"},
    "phemex":      {"defaultType": "swap"},
}

EXCHANGE_NAME = "binanceusdm"   # set at runtime to whichever exchange connects

def build_exchange():
    """Try exchanges in order; return the first one whose markets load."""
    global EXCHANGE_NAME
    last_err = None
    for name in CONFIG["EXCHANGES"]:
        try:
            klass = getattr(ccxt, name)
            opts = {"enableRateLimit": True}
            ex_opts = EXCHANGE_OPTIONS.get(name, {})
            if ex_opts:
                opts["options"] = ex_opts
            ex = klass(opts)
            ex.load_markets()
            EXCHANGE_NAME = name
            print(f"[+] Connected to: {name}")
            return ex
        except Exception as e:
            last_err = e
            print(f"[!] {name} unreachable ({type(e).__name__}); trying next...")
    raise RuntimeError(f"No exchange reachable. Last error: {last_err}")

def fetch_closed_ohlcv(ex, symbol, timeframe, limit=None, since=None):
    """Fetch candles and drop the one that is still forming.

    ccxt returns the in-progress candle as the last element, so reading it as
    "the latest bar" evaluates the whole strategy on a partial candle — at the
    cron cadence that is about one minute of price action.
    """
    kwargs = {"timeframe": timeframe}
    if limit is not None:
        kwargs["limit"] = limit
    if since is not None:
        kwargs["since"] = since
    ohlcv = ex.fetch_ohlcv(symbol, **kwargs)
    if not ohlcv:
        return ohlcv
    tf_ms = ex.parse_timeframe(timeframe) * 1000
    now = ex.milliseconds()
    while ohlcv and ohlcv[-1][0] + tf_ms > now:
        ohlcv = ohlcv[:-1]
    return ohlcv

def fetch_markets(ex):
    """List active USDT-Perpetual pairs with high volume."""
    markets = ex.load_markets()
    tickers = ex.fetch_tickers()

    # Diagnostic counters
    n_inactive = n_wrong_quote = n_not_deriv = n_inverse = n_no_ticker = n_low_vol = n_pegged = 0

    out = []
    for sym, m in markets.items():
        if not m.get("active"):
            n_inactive += 1; continue
        if m.get("quote") != CONFIG["QUOTE"]:
            n_wrong_quote += 1; continue
        if not is_tradable_base(m.get("base", "")):
            n_pegged += 1; continue
        mtype = m.get("type", "")
        is_deriv = m.get("swap") or m.get("future") or mtype in ("swap", "future")
        if not is_deriv:
            n_not_deriv += 1; continue
        # Accept linear=True or linear=None (some exchanges don't set it); reject linear=False (inverse)
        # Also reject if settle/margined currency is not USDT (inverse contracts)
        if m.get("linear") is False:
            n_inverse += 1; continue
        settle = m.get("settle") or m.get("settleId") or ""
        if settle and settle.upper() not in ("USDT", ""):
            n_inverse += 1; continue
        t = tickers.get(sym)
        if not t:
            n_no_ticker += 1; continue
        qvol = t.get("quoteVolume") or 0
        if qvol < CONFIG["MIN_24H_VOLUME_USDT"]:
            n_low_vol += 1; continue
        out.append((sym, qvol))

    print(f"    [dbg] Market filter stats: inactive={n_inactive}, wrong_quote={n_wrong_quote}, "
          f"not_deriv={n_not_deriv}, inverse={n_inverse}, pegged={n_pegged}, no_ticker={n_no_ticker}, "
          f"low_vol={n_low_vol}, passed={len(out)}")

    out.sort(key=lambda x: x[1], reverse=True)

    # Only scan the most liquid names. Expectancy falls off steadily as the
    # universe widens into thin pairs, and on exchanges that list tokenized
    # equities the long tail is mostly untradeable noise.
    cap = CONFIG["MAX_SCAN_SYMBOLS"]
    if cap and len(out) > cap:
        print(f"    [dbg] Capping scan universe to the {cap} most liquid of {len(out)} pairs")
        out = out[:cap]
    return [s for s, _ in out]

# ===================== MEMECOINS =====================
# Curated memecoin bases (exchange bases like 1000PEPE are normalized before matching)
MEME_BASES = {
    "DOGE", "SHIB", "PEPE", "WIF", "BONK", "FLOKI", "MEME", "BOME", "POPCAT",
    "MEW", "BRETT", "NEIRO", "DOGS", "TURBO", "PNUT", "GOAT", "MOODENG",
    "PONKE", "HIPPO", "CHILLGUY", "ACT", "FARTCOIN", "TRUMP", "MELANIA",
    "SPX", "GIGA", "WOJAK", "LADYS", "BABYDOGE", "CAT", "MOG", "TOSHI",
    "PENGU", "AIDOGE", "SNEK", "COQ", "MYRO", "SLERF", "DEGEN", "PEOPLE",
    "BAN", "PIPPIN", "VINE", "ANIME",
}

def normalize_base(base):
    """Strip multiplier prefixes exchanges add to tiny-price coins (1000PEPE -> PEPE)."""
    return re.sub(r"^(1000000|100000|10000|1000|1M)(?=[A-Z])", "", base.upper())

# Bases that are stablecoins or hard-pegged (gold, fiat) — micro-move noise, not real trends.
STABLE_PEGGED_BASES = {
    "USDT", "USDC", "DAI", "TUSD", "FDUSD", "USDG", "USDP", "USDD", "PYUSD",
    "USDE", "GUSD", "FRAX", "LUSD", "BUSD", "USD1", "SUSD", "CRVUSD",
    "EURT", "EURC", "EURS", "EURI", "AEUR",
    "XAU", "XAUT", "PAXG", "XAUT0",
}

def is_tradable_base(base):
    """False for stablecoins / hard-pegged assets (peg wobble is noise, not a tradable trend)."""
    return normalize_base(base) not in STABLE_PEGGED_BASES

def fetch_coingecko_meme_bases():
    """Fetch top meme-category coins from CoinGecko to catch new memecoins dynamically."""
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={"vs_currency": "usd", "category": "meme-token",
                    "order": "market_cap_desc", "per_page": 250, "page": 1},
            timeout=15, headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        return {c["symbol"].upper() for c in r.json() if c.get("symbol")}
    except Exception as e:
        print(f"[!] CoinGecko meme category fetch failed ({e}); using curated list only.")
        return set()

def fetch_meme_markets(ex):
    """List active USDT-Perpetual memecoin pairs, sorted by volume."""
    meme_bases = MEME_BASES | fetch_coingecko_meme_bases()
    markets = ex.load_markets()
    tickers = ex.fetch_tickers()
    out = []
    for sym, m in markets.items():
        if not m.get("active") or m.get("quote") != CONFIG["QUOTE"]:
            continue
        mtype = m.get("type", "")
        if not (m.get("swap") or m.get("future") or mtype in ("swap", "future")):
            continue
        if m.get("linear") is False:
            continue
        settle = m.get("settle") or m.get("settleId") or ""
        if settle and settle.upper() not in ("USDT", ""):
            continue
        if normalize_base(m.get("base", "")) not in meme_bases:
            continue
        t = tickers.get(sym)
        if not t:
            continue
        qvol = t.get("quoteVolume") or 0
        if qvol < CONFIG["MEME_MIN_24H_VOLUME_USDT"]:
            continue
        out.append((sym, qvol))
    out.sort(key=lambda x: x[1], reverse=True)
    print(f"    [dbg] Memecoin pairs passing filters: {len(out)}")
    return [s for s, _ in out]

# ===================== REGIME / HIGHER-TF / FRESHNESS =====================
MARKET_BIAS = "NEUTRAL"     # set per run from the BTC trend: "LONG", "SHORT", or "NEUTRAL"
_HTF_CACHE = {}             # symbol -> higher-timeframe trend, cached for the run

def _ema_trend_from_ohlcv(ohlcv):
    """Return 'LONG'/'SHORT'/'NEUTRAL' from EMA20 vs EMA50 (+ price) on a candle list."""
    if not ohlcv or len(ohlcv) < CONFIG["EMA_SLOW"] + 2:
        return "NEUTRAL"
    close = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])["close"]
    ef = ema(close, CONFIG["EMA_FAST"]).iloc[-1]
    es = ema(close, CONFIG["EMA_SLOW"]).iloc[-1]
    c = close.iloc[-1]
    if any(isinstance(x, float) and math.isnan(x) for x in (ef, es, c)):
        return "NEUTRAL"
    if ef > es and c >= es:
        return "LONG"
    if ef < es and c <= es:
        return "SHORT"
    return "NEUTRAL"

def compute_market_bias(ex):
    """BTC higher-TF trend defines the tradable direction for the whole run."""
    if not CONFIG["REGIME_FILTER"]:
        return "NEUTRAL"
    for sym in ("BTC/USDT:USDT", "BTC/USDT"):
        try:
            ohlcv = fetch_closed_ohlcv(ex, sym, CONFIG["HTF_TIMEFRAME"], limit=CONFIG["CANDLES"])
        except Exception:
            continue
        if ohlcv:
            bias = _ema_trend_from_ohlcv(ohlcv)
            print(f"[+] Market regime (BTC {CONFIG['HTF_TIMEFRAME']}): {bias}")
            return bias
    print("[!] Could not determine BTC regime; defaulting to NEUTRAL (both directions allowed).")
    return "NEUTRAL"

def htf_trend(ex, symbol):
    """Higher-timeframe (1d) trend for one symbol, cached for the run."""
    if symbol in _HTF_CACHE:
        return _HTF_CACHE[symbol]
    try:
        ohlcv = fetch_closed_ohlcv(ex, symbol, CONFIG["HTF_TIMEFRAME"], limit=CONFIG["CANDLES"])
    except Exception:
        ohlcv = None
    trend = _ema_trend_from_ohlcv(ohlcv)
    _HTF_CACHE[symbol] = trend
    return trend

def _is_fresh(hist, ema_f, ema_s, direction, bars):
    """True if MACD histogram flipped sign OR the EMAs crossed within the last `bars` bars.

    Keeps us near the start of a momentum move instead of chasing a mature, extended trend.
    """
    if len(hist) < bars + 2:
        return False
    rng = range(1, bars + 1)
    if direction == "LONG":
        macd_flip = any(hist.iloc[-k] > 0 and hist.iloc[-k - 1] <= 0 for k in rng)
        ema_cross = any(ema_f.iloc[-k] > ema_s.iloc[-k] and ema_f.iloc[-k - 1] <= ema_s.iloc[-k - 1] for k in rng)
    else:
        macd_flip = any(hist.iloc[-k] < 0 and hist.iloc[-k - 1] >= 0 for k in rng)
        ema_cross = any(ema_f.iloc[-k] < ema_s.iloc[-k] and ema_f.iloc[-k - 1] >= ema_s.iloc[-k - 1] for k in rng)
    return macd_flip or ema_cross

def analyze(ex, symbol, sl_mult=None, max_lev=None, check_htf=True):
    """Analyze one pair; return a signal dict if a signal exists, else None."""
    try:
        ohlcv = fetch_closed_ohlcv(ex, symbol, CONFIG["TIMEFRAME"], limit=CONFIG["CANDLES"])
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
    adx_series = adx(df, CONFIG["ADX_PERIOD"])

    # latest values (last CLOSED candle — fetch_closed_ohlcv drops the forming one)
    c = close.iloc[-1]
    ef, es = ema_f.iloc[-1], ema_s.iloc[-1]
    ef_prev, es_prev = ema_f.iloc[-2], ema_s.iloc[-2]
    rv = r.iloc[-1]
    h, h_prev = hist.iloc[-1], hist.iloc[-2]
    av = a.iloc[-1]
    adx_v = adx_series.iloc[-1]

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

    # --- Regime filter: trade only with the BTC market trend ---
    if CONFIG["REGIME_FILTER"] and MARKET_BIAS in ("LONG", "SHORT") and direction != MARKET_BIAS:
        return None

    # --- Trend-strength gate: an EMA/MACD cross inside a range is the single
    #     biggest source of losing signals; a low ADX is what those look like ---
    if CONFIG["ADX_MIN"] and not (adx_v >= CONFIG["ADX_MIN"]):
        return None

    # --- Exhaustion guard: don't chase overbought longs / oversold shorts ---
    if direction == "LONG" and rv > CONFIG["RSI_LONG_MAX"]:
        return None
    if direction == "SHORT" and rv < CONFIG["RSI_SHORT_MIN"]:
        return None

    # --- Freshness: momentum must have turned within the last N bars (no chasing) ---
    if not _is_fresh(hist, ema_f, ema_s, direction, CONFIG["FRESH_BARS"]):
        return None

    entry = c
    sl_dist = av * (sl_mult if sl_mult is not None else CONFIG["ATR_SL_MULT"])
    if sl_dist <= 0:
        return None

    if direction == "LONG":
        stop = entry - sl_dist
        target1 = entry + sl_dist                       # 1R — first (partial) target
        target = entry + sl_dist * CONFIG["RR_RATIO"]   # final target
    else:
        stop = entry + sl_dist
        target1 = entry - sl_dist
        target = entry - sl_dist * CONFIG["RR_RATIO"]

    sl_pct = abs(entry - stop) / entry * 100
    if sl_pct < CONFIG["MIN_SL_PCT"]:
        return None
    if CONFIG["MAX_ATR_PCT"] and (av / entry * 100) > CONFIG["MAX_ATR_PCT"]:
        return None   # blow-off volatility: the stop would be absurdly wide

    # --- Higher-timeframe confirmation (expensive fetch — do it last) ---
    if check_htf and CONFIG["HTF_CONFIRM"]:
        ht = htf_trend(ex, symbol)
        if ht in ("LONG", "SHORT") and ht != direction:
            return None

    lev_cap = max_lev if max_lev is not None else CONFIG["MAX_LEVERAGE"]
    suggested_lev = max(2, min(lev_cap, round(10 / max(sl_pct, 1))))

    # One-line reason
    macd_txt = "crossed up" if direction == "LONG" else "crossed down"
    bias = "bullish" if direction == "LONG" else "bearish"
    cmp = ">" if direction == "LONG" else "<"
    reason = (f"EMA{CONFIG['EMA_FAST']} {cmp} EMA{CONFIG['EMA_SLOW']}, "
              f"RSI {rv:.0f} ({bias}), MACD {macd_txt}, ADX {adx_v:.0f} — "
              f"{CONFIG['TIMEFRAME']} trend confirmed.")

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target1": target1,
        "target": target,
        "leverage": suggested_lev,
        "score": score,
        "rsi": rv,
        "adx": float(adx_v),
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
    head = "🐸 MEME SIGNAL" if s.get("is_meme") else "📊 SIGNAL"
    meme_warn = ("\n⚠️ Memecoin — extreme volatility, use small size.\n"
                 if s.get("is_meme") else "")
    f1 = CONFIG["TP1_FRACTION"]
    p1, p2 = f"{f1:.0%}", f"{1 - f1:.0%}"
    t1 = s.get("target1")
    t1_line = f"Target 1 ({p1}, +1R): {fmt_price(t1)}\n" if t1 is not None else ""
    return (
        f"{head} #{sid} | {CONFIG['TIMEFRAME'].upper()} | {s['opened_at']}\n"
        f"─────────────\n"
        f"#{base}/USDT\n"
        f"Direction: {arrow}\n"
        f"Entry: {fmt_price(s['entry'])}\n"
        f"Stop Loss: {fmt_price(s['stop'])}\n"
        f"{t1_line}"
        f"Target 2 ({p2}, +{CONFIG['RR_RATIO']:g}R): {fmt_price(s['target'])}\n"
        f"Leverage: x{s['leverage']}\n"
        f"Risk/Reward: {rr}\n"
        f"{meme_warn}\n"
        f"💡 Plan: take {p1} off at Target 1, move stop to breakeven, let the rest run.\n"
        f"📈 Why: {s['reason']}\n"
        f"📉 Chart: {tradingview_link(s['symbol'])}"
    )

def signal_levels(sig):
    """Return (target1, target2) for a signal, deriving target1 for legacy records."""
    entry = sig["entry"]
    t2 = sig["target"]
    t1 = sig.get("target1")
    if t1 is None:
        risk = abs(entry - sig["stop"])
        t1 = entry + risk if sig["direction"] == "LONG" else entry - risk
    return t1, t2

def format_tp1(sig):
    """First-target milestone message (partial booked, stop moved to breakeven — not a close)."""
    base = sig["symbol"].split("/")[0]
    sid = f"{sig['id']:04d}"
    tag = "🐸 MEME " if sig.get("is_meme") else ""
    t1, _ = signal_levels(sig)
    pct = abs(t1 - sig["entry"]) / sig["entry"] * 100
    lev = sig["leverage"]
    return (
        f"🎯 TP1 — {tag}SIGNAL #{sid} (#{base}/USDT {sig['direction']})\n"
        f"First target hit +{pct:.1f}% (x{lev} ≈ +{pct*lev:.1f}%) on "
        f"{CONFIG['TP1_FRACTION']:.0%} of the position.\n"
        f"Stop moved to breakeven — risk-free now, letting the rest run to Target 2 🚀\n"
        f"{now_utc_str()}"
    )

def outcome_r(outcome, close_r=0.0):
    """Net realized R for a closing outcome.

    TP1_FRACTION of the position is booked at +1R; the remainder resolves at
    the final target, at breakeven, or — for a time exit — wherever it is.
    """
    f = CONFIG["TP1_FRACTION"]
    if outcome == "SL":
        return -1.0
    if outcome == "BE":
        return f * 1.0
    if outcome == "TP2":
        return f * 1.0 + (1 - f) * CONFIG["RR_RATIO"]
    if outcome == "TIME_TP1":       # timed out after TP1
        return f * 1.0 + (1 - f) * close_r
    if outcome == "TIME":           # timed out before TP1
        return close_r
    raise ValueError(f"unknown outcome {outcome}")

CLOSING_OUTCOMES = ("SL", "BE", "TP2", "TIME", "TIME_TP1")

def format_result(sig, outcome, net_r, stats_line):
    base = sig["symbol"].split("/")[0]
    sid = f"{sig['id']:04d}"
    tag = "🐸 MEME " if sig.get("is_meme") else ""
    lev = sig["leverage"]
    one_r_pct = abs(sig["entry"] - sig["stop"]) / sig["entry"] * 100   # 1R in %
    net_pct = net_r * one_r_pct
    icon = {"TP2": "✅", "BE": "⚖️", "SL": "❌"}.get(
        outcome, "✅" if net_r > 0 else ("❌" if net_r < 0 else "⚖️"))
    head = f"{icon} RESULT — {tag}SIGNAL #{sid} (#{base}/USDT {sig['direction']})"
    amounts = f"net {net_r:+.2f}R ({net_pct:+.1f}%, x{lev} ≈ {net_pct*lev:+.1f}%)"
    if outcome == "TP2":
        line = f"Final target hit 🎯 {amounts}"
    elif outcome == "BE":
        line = f"Closed at breakeven after TP1 🛡️ {amounts}"
    elif outcome in ("TIME", "TIME_TP1"):
        line = (f"Closed at market after {CONFIG['MAX_HOLD_DAYS']} days ⏱️ "
                f"— trade went nowhere. {amounts}")
    else:  # SL
        line = f"Stop loss hit 🛑 {amounts}"
    return (
        f"{head}\n"
        f"{line}\n"
        f"Closed {now_utc_str()}\n"
        f"─────────────\n"
        f"{stats_line}"
    )

def stats_line():
    mk = month_key()
    st = STATE["stats"].get(mk, {})
    tp = st.get("tp", 0); sl = st.get("sl", 0); be = st.get("be", 0)
    total = tp + sl + be
    wins = tp + be                      # BE closes at +0.5R — a small win, not a loss
    wr = (wins / total * 100) if total else 0
    r_count = st.get("r_count", 0)
    avg_r = (st.get("r_sum", 0.0) / r_count) if r_count else 0.0
    month_name = datetime.now(timezone.utc).strftime("%B")
    return (f"📊 {month_name}: {total} closed · {tp} TP ✅ · {be} BE ⚖️ · {sl} SL ❌ · "
            f"Win {wr:.0f}% · Avg {avg_r:+.2f}R")

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
def fetch_since(ex, symbol, timeframe, since_ms):
    """All candles from `since_ms` to now, paginated.

    A plain limit=200 fetch only reaches ~8 days back on 1h candles, so any
    trade older than that was being replayed from the middle — losing the fact
    that TP1 had already happened and grading breakeven exits as stop-outs.
    """
    tf_ms = ex.parse_timeframe(timeframe) * 1000
    out = []
    cursor = since_ms
    for _ in range(20):     # bounded: 20 * 1000 candles is far beyond MAX_HOLD_DAYS
        try:
            batch = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=1000)
        except Exception:
            break
        if not batch:
            break
        out += batch
        nxt = batch[-1][0] + tf_ms
        if nxt <= cursor or nxt > ex.milliseconds():
            break
        cursor = nxt
    now = ex.milliseconds()
    return [c for c in out if c[0] + tf_ms <= now]     # closed candles only

def check_outcome(ex, sig):
    """Replay candles since the signal opened and resolve the staged exit.

    Returns (outcome, net_R) where outcome is one of:
      "SL"        original stop hit before TP1            -> close
      "BE"        after TP1, price returned to breakeven  -> close
      "TP2"       after TP1, final target reached         -> close
      "TIME"/"TIME_TP1"  held past MAX_HOLD_DAYS          -> close at market
      "TP1_OPEN"  first target reached, still running     -> stays open (milestone)
      None        nothing yet                             -> stays open

    The stop is never mutated in state; the breakeven stop is reconstructed each
    run by replaying chronologically from the original stop, so an early dip
    below entry (before TP1) can't be mistaken for a breakeven exit. `tp1_hit`
    is persisted as a backstop in case the replay window ever misses it.
    """
    opened_ms = sig.get("opened_ms", 0)
    ohlcv = fetch_since(ex, sig["symbol"], CONFIG["RESULT_CHECK_TF"], opened_ms)
    if not ohlcv:
        # Delisted, renamed, or unavailable on the current exchange. Without
        # candles the outcome is unknowable, so drop it rather than leave it in
        # the book forever — and never invent a number for the track record.
        age_days = (datetime.now(timezone.utc).timestamp() * 1000 - opened_ms) / 86_400_000
        if age_days > 2 * CONFIG["MAX_HOLD_DAYS"]:
            return "UNTRACKABLE", 0.0
        return None, 0.0

    entry = sig["entry"]
    risk = abs(entry - sig["stop"])
    t1, t2 = signal_levels(sig)
    long = sig["direction"] == "LONG"
    tp1_seen = bool(sig.get("tp1_hit"))
    active_stop = entry if tp1_seen else sig["stop"]
    deadline = opened_ms + CONFIG["MAX_HOLD_DAYS"] * 86_400_000

    for ts, o, h, l, c, v in ohlcv:
        if ts < opened_ms:
            continue
        # Stop is checked before the target: within a single candle we cannot
        # know the order, so assume the worse of the two.
        if (long and l <= active_stop) or (not long and h >= active_stop):
            out = "BE" if tp1_seen else "SL"
            return out, outcome_r(out)
        if not tp1_seen and ((long and h >= t1) or (not long and l <= t1)):
            tp1_seen = True
            active_stop = entry
        if tp1_seen and ((long and h >= t2) or (not long and l <= t2)):
            return "TP2", outcome_r("TP2")
        if ts >= deadline:
            close_r = ((c - entry) if long else (entry - c)) / risk
            out = "TIME_TP1" if tp1_seen else "TIME"
            return out, outcome_r(out, close_r)

    if tp1_seen:
        sig["tp1_hit"] = True
        return "TP1_OPEN", 0.0
    return None, 0.0

def _bump_stats(mk, **deltas):
    """Increment monthly stat counters, tolerating legacy dicts missing keys."""
    d = STATE["stats"].setdefault(
        mk, {"tp": 0, "sl": 0, "tp1": 0, "tp2": 0, "be": 0, "r_sum": 0.0, "r_count": 0})
    for k, v in deltas.items():
        d[k] = d.get(k, 0) + v

def open_record(s):
    """Compact tracking record for an opened signal.

    Carries the entry conditions (rsi/adx/sl_pct/exchange) as well as the price
    levels, so the trade log can later answer which setups actually pay.
    """
    return {
        "id": s["id"], "symbol": s["symbol"], "direction": s["direction"],
        "entry": s["entry"], "stop": s["stop"],
        "target1": s.get("target1"), "target": s["target"],
        "leverage": s["leverage"], "opened_at": s["opened_at"], "opened_ms": s["opened_ms"],
        "rsi": round(s.get("rsi", 0), 1), "adx": round(s.get("adx", 0), 1),
        "sl_pct": round(s.get("sl_pct", 0), 2), "exchange": EXCHANGE_NAME,
        "is_meme": bool(s.get("is_meme")),
    }

def log_trade(sig, outcome, net_r):
    """Append a closed trade to the trade log (one JSON object per line).

    The monthly counters alone can't answer "which kind of signal loses money";
    this log is what makes the strategy auditable and re-backtestable.
    """
    if CONFIG["DRY_RUN"]:
        return
    rec = dict(sig)
    rec.update({"outcome": outcome, "net_r": round(net_r, 4), "closed_at": now_utc_str()})
    try:
        with open(CONFIG["TRADE_LOG"], "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:
        print(f"[!] Could not write trade log: {e}")

def process_open_signals(ex):
    """Check open signals, post TP1 milestones and closing results, update stats."""
    still_open = []
    mk = month_key()
    for sig in STATE["open"]:
        outcome, net_r = check_outcome(ex, sig)
        if outcome in CLOSING_OUTCOMES:
            if outcome == "TP2":
                _bump_stats(mk, tp=1, tp2=1, r_sum=net_r, r_count=1)
            elif outcome == "BE":
                _bump_stats(mk, be=1, r_sum=net_r, r_count=1)
            elif outcome == "SL":
                _bump_stats(mk, sl=1, r_sum=net_r, r_count=1)
            else:   # timed out — bucket by whether it actually made money
                key = "tp" if net_r > 0 else ("sl" if net_r < 0 else "be")
                _bump_stats(mk, r_sum=net_r, r_count=1, **{key: 1})
            post(format_result(sig, outcome, net_r, stats_line()))
            log_trade(sig, outcome, net_r)
        elif outcome == "UNTRACKABLE":
            print(f"[!] #{sig['id']} {sig['symbol']}: no candles available and long "
                  f"past max hold — dropping from the book, not counted in stats.")
            log_trade(sig, outcome, 0.0)
        elif outcome == "TP1_OPEN":
            if not sig.get("tp1_announced"):
                sig["tp1_announced"] = True
                _bump_stats(mk, tp1=1)
                post(format_tp1(sig))
            still_open.append(sig)
        else:
            still_open.append(sig)
    STATE["open"] = still_open

# ===================== FALLBACK SOURCES =====================

def fallback_trending_coins():
    """Post CoinGecko trending coins (no API key needed)."""
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/search/trending",
            timeout=15, headers={"Accept": "application/json"}
        )
        r.raise_for_status()
        coins = r.json().get("coins", [])[:7]
        if not coins:
            return False
        lines = []
        for item in coins:
            c = item["item"]
            price = c.get("data", {}).get("price") or ""
            change = c.get("data", {}).get("price_change_percentage_24h", {})
            chg_usd = change.get("usd", None) if isinstance(change, dict) else None
            chg_str = f"  {chg_usd:+.1f}%" if chg_usd is not None else ""
            price_str = f"  ${price}" if price else ""
            lines.append(f"#{c['symbol'].upper()}{price_str}{chg_str}")
        text = (
            f"🔥 Trending Coins on CoinGecko  |  {now_utc_str()}\n"
            "─────────────\n" +
            "\n".join(lines) +
            "\n\n⚠️ No futures signals this scan — market may be ranging."
        )
        print("[+] Fallback: posting CoinGecko trending coins")
        post(text)
        return True
    except Exception as e:
        print(f"[!] fallback_trending_coins failed: {e}")
        return False


def fallback_top_movers():
    """Post top 5 gainers and losers from CoinGecko (free tier)."""
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 100,
                "page": 1,
                "price_change_percentage": "24h",
            },
            timeout=15, headers={"Accept": "application/json"}
        )
        r.raise_for_status()
        coins = r.json()
        sorted_coins = sorted(
            [c for c in coins if c.get("price_change_percentage_24h") is not None],
            key=lambda c: c["price_change_percentage_24h"]
        )
        losers = sorted_coins[:5]
        gainers = sorted_coins[-5:][::-1]

        def fmt_coin(c):
            chg = c["price_change_percentage_24h"]
            return f"#{c['symbol'].upper()}  ${c['current_price']:,.4g}  {chg:+.1f}%"

        text = (
            f"📈 Top Movers (24h)  |  {now_utc_str()}\n"
            "─────────────\n"
            "🚀 Gainers:\n" + "\n".join(fmt_coin(c) for c in gainers) + "\n\n"
            "📉 Losers:\n" + "\n".join(fmt_coin(c) for c in losers) +
            "\n\n⚠️ No futures signals this scan."
        )
        print("[+] Fallback: posting top movers")
        post(text)
        return True
    except Exception as e:
        print(f"[!] fallback_top_movers failed: {e}")
        return False


def fallback_fear_greed():
    """Post Crypto Fear & Greed Index (alternative.me)."""
    try:
        r = requests.get(
            "https://api.alternative.me/fng/?limit=3",
            timeout=15, headers={"Accept": "application/json"}
        )
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return False
        now_d = data[0]
        value = int(now_d["value"])
        label = now_d["value_classification"]
        emoji_map = {
            "Extreme Fear": "😱", "Fear": "😨", "Neutral": "😐",
            "Greed": "😄", "Extreme Greed": "🤑",
        }
        em = emoji_map.get(label, "📊")
        bar_filled = round(value / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        history = ""
        if len(data) >= 3:
            history = (
                f"\nYesterday: {data[1]['value']} ({data[1]['value_classification']})"
                f"\nLast week: {data[2]['value']} ({data[2]['value_classification']})"
            )
        text = (
            f"{em} Crypto Fear & Greed Index  |  {now_utc_str()}\n"
            "─────────────\n"
            f"Today: {value}/100  —  {label}\n"
            f"[{bar}]{history}\n\n"
            "⚠️ No futures signals found this scan — market may be ranging."
        )
        print("[+] Fallback: posting Fear & Greed index")
        post(text)
        return True
    except Exception as e:
        print(f"[!] fallback_fear_greed failed: {e}")
        return False


def fallback_market_summary():
    """Post BTC/ETH prices + BTC dominance from CoinGecko."""
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": "bitcoin,ethereum,solana,bnb,xrp",
                "order": "market_cap_desc",
                "per_page": 5,
                "page": 1,
                "price_change_percentage": "24h",
            },
            timeout=15, headers={"Accept": "application/json"}
        )
        r.raise_for_status()
        coins = r.json()

        global_r = requests.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=15, headers={"Accept": "application/json"}
        )
        global_r.raise_for_status()
        gdata = global_r.json().get("data", {})
        btc_dom = gdata.get("market_cap_percentage", {}).get("btc", 0)
        total_mcap = gdata.get("total_market_cap", {}).get("usd", 0)

        lines = []
        for c in coins:
            chg = c.get("price_change_percentage_24h") or 0
            chg_str = f"{chg:+.2f}%"
            lines.append(f"#{c['symbol'].upper():<5}  ${c['current_price']:>12,.2f}   {chg_str}")

        mcap_t = f"${total_mcap/1e12:.2f}T" if total_mcap > 1e12 else f"${total_mcap/1e9:.0f}B"
        text = (
            f"🌍 Crypto Market Summary  |  {now_utc_str()}\n"
            "─────────────\n" +
            "\n".join(lines) +
            f"\n\nTotal Market Cap: {mcap_t}\n"
            f"BTC Dominance: {btc_dom:.1f}%\n\n"
            "⚠️ No futures signals found this scan."
        )
        print("[+] Fallback: posting market summary")
        post(text)
        return True
    except Exception as e:
        print(f"[!] fallback_market_summary failed: {e}")
        return False


def try_fallbacks(ex):
    """Run fallback sources in order until one posts successfully.

    Deliberately informational only. The daily-timeframe and spot fallbacks used
    to open real tracked trades whenever the 4h scan found nothing — forcing a
    position precisely when the strategy says there is no setup. They also
    skipped the dedup and liquidity checks, which is how the same symbol could
    be signalled twice and how illiquid tokenized-equity pairs got in.
    """
    fallbacks = [
        ("Trending coins",     fallback_trending_coins),
        ("Top movers",         fallback_top_movers),
        ("Fear & Greed",       fallback_fear_greed),
        ("Market summary",     fallback_market_summary),
    ]
    for name, fn in fallbacks:
        print(f"    [fallback] Trying: {name}...")
        try:
            if fn():
                print(f"    [fallback] Posted via: {name}")
                return True
        except Exception as e:
            print(f"    [fallback] {name} raised: {e}")
    return False


# ===================== RUN =====================
STATE = {}

def run_once():
    global STATE, MARKET_BIAS
    STATE = load_state()
    check_telegram()
    ex = build_exchange()

    MARKET_BIAS = compute_market_bias(ex)

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
        STATE["open"].append(open_record(s))

    # ---- Memecoin scan ----
    meme_top = []
    if CONFIG["MEME_ENABLED"]:
        print("[+] Scanning memecoin pairs...")
        posted_symbols = open_symbols | {s["symbol"] for s in top}
        try:
            meme_symbols = [s for s in fetch_meme_markets(ex) if s not in posted_symbols]
        except Exception as e:
            print(f"[!] Memecoin market load failed: {e}")
            meme_symbols = []
        meme_signals = []
        for sym in meme_symbols:
            s = analyze(ex, sym,
                        sl_mult=CONFIG["MEME_ATR_SL_MULT"],
                        max_lev=CONFIG["MEME_MAX_LEVERAGE"])
            if s:
                s["is_meme"] = True
                meme_signals.append(s)
        meme_signals.sort(key=lambda x: (x["score"], abs(x["rsi"] - 50)), reverse=True)
        meme_top = meme_signals[:CONFIG["MEME_MAX_SIGNALS_PER_RUN"]]
        print(f"[+] {len(meme_signals)} memecoin signals found. Posting top {len(meme_top)}.")
        for s in meme_top:
            s["id"] = STATE["next_id"]
            STATE["next_id"] += 1
            s["opened_at"] = now_utc_str()
            s["opened_ms"] = int(datetime.now(timezone.utc).timestamp() * 1000)
            post(format_signal(s))
            STATE["open"].append(open_record(s))

    if not top and not meme_top:
        print("[i] No new signals this run. Trying fallback sources...")
        posted = try_fallbacks(ex)
        if not posted:
            print("[!] All fallback sources exhausted — nothing posted this run.")

    save_state(STATE)
    print("[+] Done. State saved.")

if __name__ == "__main__":
    run_once()
