"""Vet a Telegram signal channel: replay its past signals on real MEXC candles.
Usage: python vet.py CHANNEL [pages]   (each page ~20 posts)
Grades each signal by whichever of TP1 / SL trades first on 1h candles (same-candle = SL, conservative)."""
import sys
from sources import fetch_page, parse_signal, mexc_symbol, exchange

def vet(ch, pages=25):
    sigs, before = [], None
    for _ in range(pages):
        page = sorted(fetch_page(ch, before))
        if not page: break
        sigs += [(pid, ms, parse_signal(t)) for pid, ms, t in page]
        before = page[0][0]
    sigs = [(p, ms, s) for p, ms, s in sigs if s]
    ex, wins, losses, rs, skipped = exchange(), 0, 0, [], 0
    for pid, ms, s in sigs:
        sym = mexc_symbol(s["symbol"])
        if not sym: skipped += 1; continue
        e = sum(s["entries"][:2]) / len(s["entries"][:2]); long = s["side"] == "LONG"
        risk, tp1 = abs(e - s["stop"]), s["targets"][0]
        res = None
        for _, o, h, l, c, _v in ex.fetch_ohlcv(sym, "1h", since=ms, limit=500):
            hit_sl = l <= s["stop"] if long else h >= s["stop"]
            hit_tp = h >= tp1 if long else l <= tp1
            if hit_sl: res = -1.0; break
            if hit_tp: res = abs(tp1 - e) / risk; break
        if res is None: skipped += 1; continue
        wins += res > 0; losses += res < 0; rs.append(res)
    n = len(rs)
    print(f"@{ch}: {len(sigs)} signals parsed, {n} gradable ({skipped} not on MEXC / still open)")
    if n: print(f"  TP1-before-SL win rate {wins/n:.0%}   avg R at TP1-or-SL {sum(rs)/n:+.2f}")
    return n, (sum(rs) / n if n else 0)

if __name__ == "__main__":
    vet(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 25)
