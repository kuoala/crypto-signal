"""Shared bits for mirroring signal channels: scrape, parse (EN/FA), live-price gate."""
import html, re
from datetime import datetime
import requests

def fetch_posts(ch, before=None):
    """Yield dicts(id, ms, text, reply_to) for one t.me/s page (~20 posts); reply_to = source id replied to; fwd = forwarded from another channel."""
    url = f"https://t.me/s/{ch}" + (f"?before={before}" if before else "")
    for b in requests.get(url, timeout=20).text.split("tgme_widget_message_wrap")[1:]:
        pid = int(re.search(rf'data-post="{ch}/(\d+)"', b, re.I).group(1))
        m = re.search(r"js-message_text[^>]*>(.*?)</div>", b, re.S)
        t = re.search(r'datetime="([^"]+)"', b)
        r = re.search(rf'message_reply[^>]*href="https://t\.me/{ch}/(\d+)', b, re.I)
        if m and t:
            txt = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br/?>", "\n", m.group(1))))
            yield dict(id=pid, ms=int(datetime.fromisoformat(t.group(1)).timestamp() * 1000),
                       text=txt, reply_to=int(r.group(1)) if r else None,
                       fwd="tgme_widget_message_forwarded_from" in b)

def fetch_page(ch, before=None):
    for p in fetch_posts(ch, before):
        yield p["id"], p["ms"], p["text"]

NUM = r"\d+(?:\.\d+)?"
def _nums(s): return [float(x) for x in re.findall(NUM, s.replace(",", "")) ]

SIDE = {"LONG": "LONG", "BUY": "LONG", "SHORT": "SHORT", "SELL": "SHORT"}

def parse_signal(text):
    """-> dict(symbol, side, entries, stop, targets, lev) or None. Needs side+entry+stop+>=1 TP."""
    t = text.replace("‏", "")
    sym = re.search(r"#?([A-Z0-9]{1,15})\s*/\s*USDT", t, re.I) or re.search(r"(?:نام ارز|coin|pair)\s*:?\s*#?([A-Z0-9]{1,15})", t, re.I)
    side = re.search(r"\b(LONG|SHORT|BUY|SELL)\b", t, re.I)
    if not (sym and side):
        return None
    def after(*keys):
        out = []   # every line carrying the label, so "TP1: x / TP2: y" lines all count
        L = t.split("\n")
        for i, ln in enumerate(L):
            for k in keys:
                m = re.search(k + r"[^\n\d]*(.*)", ln, re.I)
                if m:
                    out += _nums(re.sub(r"^\d\s*[:=)-]", "", m.group(1)) if re.match(r"\d\s*[:=)-]", m.group(1)) else m.group(1))
                    if not out:      # label alone on its line: numbers follow as a list ("1) 611.22" or "0.1794")
                        for nxt in L[i + 1:]:
                            if nxt.strip() and not re.fullmatch(r"\s*\$?\s*(\d+\s*(?:\)|[.:-]\s)\s*)?" + NUM + r"(\s*[-–&,|]\s*\$?\s*" + NUM + r")*\s*", nxt): break
                            out += _nums(re.sub(r"^\s*\d+\s*(?:\)|[.:-]\s)\s*", "", nxt))
                    break
        return out
    ent = after(r"entry", r"نقطه ورود", r"buy zone")
    stop = after(r"stop[\s-]*loss", r"\bsl\b", r"حد ضرر", r"\bstop\b")
    tps = after(r"targets?", r"take[\s-]*profit", r"\btps?\d*\b", r"تارگت ها")
    if not (ent and stop and tps):
        return None
    lev = re.search(r"(?:leverage|لورج)\D*([^\n]*\d[^\n]*)", t, re.I)
    return dict(symbol=sym.group(1).upper(), side=SIDE[side.group(1).upper()], entries=ent, targets_raw=tps,
                stop=stop[0], targets=sorted(set(tps), reverse=SIDE[side.group(1).upper()] == "SHORT"),
                lev=lev.group(1).strip() if lev else None)

def check(sig, price):
    """Sanity + freshness vs live price. Returns None if OK else the reason."""
    long = sig["side"] == "LONG"
    e = sum(sig["entries"][:2]) / len(sig["entries"][:2])
    tp1 = sig["targets"][0]
    if long != (tp1 > e) or long != (sig["stop"] < e):
        return "stop/targets on wrong side"
    risk = abs(e - sig["stop"]) / e
    if not 0.005 <= risk <= 0.08:
        return f"stop distance {risk:.1%} outside 0.5-8%"
    if abs(price - e) / e > 0.01 * 1.5:
        return f"stale: price {price} vs entry {e:.6g}"
    if (price >= tp1) if long else (price <= tp1):
        return "TP1 already hit"
    if (price <= sig["stop"]) if long else (price >= sig["stop"]):
        return "stop already hit"
    if abs(tp1 - e) / abs(e - sig["stop"]) < 0.5:
        return "TP1 reward < 0.5R"
    return None

_ex = None
def exchange():
    global _ex
    if _ex is None:
        import ccxt
        _ex = ccxt.mexc({"options": {"defaultType": "swap"}}); _ex.load_markets()
    return _ex

def mexc_symbol(base):
    for b in (base, "1000" + base):
        s = f"{b}/USDT:USDT"
        if s in exchange().markets: return s
    return None

def self_test():
    s = parse_signal("نام ارز #NEAR/USDT\n⭕️ حالت معامله = SHORT 🔴\n⚠️لورج :- 5X 10X 20X\n⚡️ نقطه ورود = [ 4.672 & 4.684 ]\n❌ حد ضرر :- 4.850\n✅ تارگت ها = [ 4.634 , 4.580 , 4.535 ]")
    assert s and s["side"] == "SHORT" and s["stop"] == 4.85 and s["targets"][0] == 4.634 and s["entries"] == [4.672, 4.684], s
    s = parse_signal("#BTC/USDT LONG\nEntry: 64,250 - 64,000\nSL: 62,800\nTP1: 65,700\nTP2: 67,100")
    assert s and s["entries"] == [64250, 64000] and s["stop"] == 62800 and s["targets"][0] == 65700, s
    assert check(s, 64200) is None and "stale" in check(s, 70000)
    assert parse_signal("نام ارز #Q/USDT\n⭕️ حالت معامله = LONG\nنقطه ورود = [ 0.1210 & 0.1260 ]\nحد ضرر :- 0.1160\nتارگت ها = [ 0.1310 , 0.1380 ]")["symbol"] == "Q"  # 1-letter ticker
    s = parse_signal("💥 Futures\n\n✅ Long\n\n#BAT/USDT\n\nEntry zone : 0.173193 - 0.178550\n\nTake Profits :\n\n0.179433\n0.184763\n0.190093\n\nStop loss :0.1660515\n\nLeverage: 10x")
    assert s and s["entries"] == [0.173193, 0.17855] and s["targets_raw"] == [0.179433, 0.184763, 0.190093] and s["stop"] == 0.1660515 and s["lev"] == "10x", s
    s = parse_signal("#Free\n\n🟢 Long\n\n#XMR/USDT\n\nEntry :\n\n1) 611.22\n2) 592.88\n\nTargets :\n\n1) 614.73\n2) 627.49\n\n🛑 Stop : 572.71\n\nLeverage : 10x (isolated)")
    assert s and s["entries"] == [611.22, 592.88] and s["targets_raw"] == [614.73, 627.49] and s["stop"] == 572.71, s
    s = parse_signal("HYPE/USDT 🔴SHORT\nENTRY ZONE AREA \n$ 78.6 - $ 79.6\n\nSL : $ 80.961\n\n\nTP1: $ 77.7\nTP2: $ 76.6\n\nLEVERAGE 1x - 5x - 10x - 20×")
    assert s and s["entries"] == [78.6, 79.6] and s["stop"] == 80.961 and s["targets_raw"] == [77.7, 76.6] and s["lev"].startswith("1x"), s
    assert parse_signal("join vip LONG now") is None
    print("ok")

if __name__ == "__main__":
    self_test()

def clean(text):
    """Drop every line that names a channel/handle/link, then any stray @handle."""
    keep = [l for l in text.split("\n") if not re.search(r"@\w|t\.me/|telegram\.me/|https?://", l)]
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"@\w+", "", "\n".join(keep))).strip()
