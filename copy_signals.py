"""Mirror every signal from the source channel(s) into ours, translated to English:
signals, and every update on them (targets hit / profit) posted as a reply to our copy of the
original. Channel names, @mentions and links are stripped. If a source post is edited, our copy is edited."""
import hashlib, json, os, re, subprocess, sys, time
from sources import fetch_posts, parse_signal, clean, self_test
from telegram_poster import send_get_id, edit_message

STATE = "copy_state.json"

def sig_text(s):
    f = lambda xs: " , ".join(f"{x:.10g}" for x in xs)
    return (f"#{s['symbol']}/USDT\nDirection: {'🟢' if s['side'] == 'LONG' else '🔴'} {s['side']}\n"
            f"Leverage: {s['lev'] or '-'}\nEntry: {f(s['entries'])}\nStop Loss: {s['stop']:.10g}\n"
            f"Targets: {f(s['targets_raw'])}")

def update_text(t):
    """Profit/target update -> English, else None. Persian template or already-English."""
    t = t.replace("‏", "")
    m = re.search(r"نام ارز\s*:?\s*#(\w+)[^\n]*\n.*?تارگت ها\s*:\s*([\d,\s]+?)\s*\n.*?میزان سود\s*:\s*([\d.]+%)", t, re.S)
    if m:
        return m.group(1), f"#{m.group(1)}\n✅ Targets hit: {m.group(2).strip()}\n🚀 Profit: {m.group(3)}"
    m = re.match(r"#(\w+)(?:/USDT)?\s+(?:Take-Profit|Stop|Cancel|Closed)", t, re.I)
    if m: return m.group(1), t
    pct = re.search(r"(\d+(?:\.\d+)?)%", t)       # "#X/USDT reached 0.07 ... 45.99% Profit", "#Free_signal 76% Profit"
    if pct and re.search(r"profit", t, re.I) and len(t) < 300 and not re.search(r"strategy|join|accuracy", t, re.I):
        sym = re.search(r"#?([A-Z0-9]{1,15})/USDT", t, re.I) or re.search(r"profit in #?([A-Z0-9]{1,15})\b", t, re.I)
        px = re.search(r"(?:reached|hit|touched|\bat)\s+\$?\s*([\d.]*\d)", t, re.I)
        tg = re.search(r"targets?\s*([\d, ]*\d)\s*(?:done|hit)", t, re.I)
        sym = sym.group(1).upper() if sym else None
        return sym, "\n".join(filter(None, [sym and f"#{sym}/USDT", tg and f"✅ Targets hit: {tg.group(1)}", px and f"🎯 Price: {px.group(1)}", f"🚀 Profit: {pct.group(1)}%"]))
    return None, None

def translate(text):
    """-> (kind, symbol, english) or None for posts we can't translate (promos / free text)."""
    s = parse_signal(text)
    if s:
        return "signal", s["symbol"], clean(sig_text(s))
    sym, u = update_text(text)
    if u:
        return "update", sym, clean(u)
    m = re.search(r"#?([A-Z0-9]{1,15})\s*/\s*USDT", text, re.I)
    if m and re.search(r"\b(LONG|SHORT)\b", text) and len(re.findall(r"\d", text)) >= 6:
        return "signal", m.group(1).upper(), clean(text)      # parser missed it: post cleaned original, never drop the main signal
    return None

def period_ms(text):
    """'Period: 17 hr 29 min' -> ms (None if absent)."""
    m = re.search(r"Period:\s*([^\n]*)", text)
    if not m: return None
    u = {"d": 86400, "h": 3600, "m": 60}
    v = [int(n) * u[k[0].lower()] for n, k in re.findall(r"(\d+)\s*(day|d|hr|h|min|m)\w*", m.group(1), re.I)]
    return sum(v) * 1000 if v else None

def pick(cands, upd_ms, per):
    """Which candidate signal (key, ms) does an update belong to? Latest before it, or -- when the update states its
    Period -- the one posted `per` before it (within tolerance). None if nothing fits."""
    cands = [c for c in cands if c[1] <= upd_ms]
    if not cands: return None
    if per is None: return max(cands, key=lambda c: c[1])[0]
    best = min(cands, key=lambda c: abs(upd_ms - c[1] - per))
    return best[0] if abs(upd_ms - best[1] - per) <= max(3600_000, per // 2) else None

def history(ch, pages=60):
    """Source posts newest-first, paging back through t.me/s."""
    before = None
    for _ in range(pages):
        pg = sorted(fetch_posts(ch, before), key=lambda p: -p["id"])
        if not pg: return
        yield from pg
        before = pg[-1]["id"]

def post_signal(st, p, sym, text):
    mid = send_get_id(text, None)
    if mid is not None:
        st["map"][str(p["id"])] = [mid, hashlib.md5(p["text"].encode()).hexdigest()]
        st["sig"][str(p["id"])] = [sym, p["ms"]]
        st["sym"][sym] = str(p["id"])
    return mid

def backfill(st, ch, sym, upd_ms, per, want_id):
    """The signal an update belongs to was never copied (older than we started, or missed): find it in the source history,
    post it now, return our message id."""
    found = []
    for p in history(ch):
        if want_id and p["id"] < want_id: break
        if not want_id and p["ms"] < upd_ms - 30 * 86400_000: break
        if p.get("fwd") or str(p["id"]) in st["map"] or (want_id and p["id"] != want_id): continue
        tr = translate(p["text"])
        if tr and tr[0] == "signal" and tr[1] == sym:
            found.append((p, tr[2]))
    k = pick([(i, f[0]["ms"]) for i, f in enumerate(found)], upd_ms, per) if not want_id else (0 if found else None)
    return post_signal(st, found[k][0], sym, found[k][1]) if k is not None else None

def resolve(st, ch, sym, reply_to, upd_ms, text):
    """Our message id to thread an update under, posting the original signal first if we never copied it. None = no such signal exists."""
    if reply_to:
        mid = find_parent(st, sym, reply_to)
        return mid or backfill(st, ch, sym, upd_ms, None, reply_to)
    per = period_ms(text)
    c = [(k, v[1]) for k, v in st["sig"].items() if v[0] == sym]
    k = pick(c, upd_ms, per)
    if k: return st["map"][k][0]
    if not c and st["sym"].get(sym) in st["map"]:        # signals copied before timestamps were stored
        return st["map"][st["sym"][sym]][0]
    return backfill(st, ch, sym, upd_ms, per, None)

def find_parent(st, sym, reply_to):
    """Our message_id to reply to. A source reply must map exactly; only a link-less update falls back to the coin's latest signal."""
    src = str(reply_to) if reply_to else st["sym"].get(sym)
    return st["map"][src][0] if src in st["map"] else None

def main():
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    for ch in json.load(open("channels.json")):
        st = state.get(ch)
        if not isinstance(st, dict):            # migrate old {"ch": last_id}
            st = {"last": st, "map": {}, "sym": {}, "sig": {}}
        if "sig" not in st:                     # legacy state: recover post times of already-copied signals
            st["sig"] = {}
            for q in history(ch, 15):
                tr = translate(q["text"]) if str(q["id"]) in st["map"] else None
                if tr and tr[0] == "signal": st["sig"][str(q["id"])] = [tr[1], q["ms"]]
        posts = sorted(fetch_posts(ch), key=lambda p: p["id"])
        if not posts: continue
        if st["last"] is None:                  # first run: start from now
            st["last"] = posts[-1]["id"]
        for p in posts:
            key, h = str(p["id"]), hashlib.md5(p["text"].encode()).hexdigest()
            if key in st["map"]:                # already copied: follow edits
                if st["map"][key][1] != h and (tr := translate(p["text"])) and edit_message(st["map"][key][0], tr[2]):
                    st["map"][key][1] = h
                continue
            if p["id"] <= st["last"]: continue
            tr = translate(p["text"])
            if not tr and p["reply_to"] in map(int, st["map"]) and not p.get("fwd") and 0 < len(clean(p["text"])) <= 160 and not re.search(r"https?://|t\.me", p["text"]):
                tr = ("update", st["sig"].get(str(p["reply_to"]), [None])[0], clean(p["text"]))   # "Nice Movement", "Cancelled": any short reply on our signal
            if tr and p.get("fwd"):   # forwarded from a VIP channel: cherry-picked signals/wins we can't verify
                print(f"skip forwarded {ch}/{p['id']} {tr[1]}")
                st["last"] = p["id"]
                continue
            if tr:
                kind, sym, text = tr
                sym = sym or st["sig"].get(str(p["reply_to"]), [None])[0]    # "76% Profit" replies carry no symbol: the parent's
                reply = resolve(st, ch, sym, p["reply_to"], p["ms"], p["text"]) if kind == "update" else None
                if kind == "update" and reply is None:   # the source never posted this trade's signal: nothing to thread under
                    print(f"ALERT orphan update {ch}/{p['id']} {sym}: original signal not found in source history - not posted")
                    st["last"] = p["id"]
                    continue
                mid = post_signal(st, p, sym, text) if kind == "signal" else send_get_id(text, reply)
                if mid is None: break           # retry next run
                if kind == "update": st["map"][key] = [mid, h]
                print(f"{ch}/{p['id']} {kind} {sym} -> {mid}{' (reply)' if reply else ''}")
            elif re.search(r"\b(LONG|SHORT)\b", p["text"]):
                print(f"WARNING: {ch}/{p['id']} looks like a signal but did not parse - skipped")
            st["last"] = p["id"]
        st["map"] = dict(sorted(st["map"].items(), key=lambda kv: int(kv[0]))[-300:])  # bound state
        st["sig"] = {k: v for k, v in st["sig"].items() if k in st["map"]}
        state[ch] = st
    before = open(STATE).read() if os.path.exists(STATE) else ""
    json.dump(state, open(STATE, "w"))
    return open(STATE).read() != before

def commit_state():
    """Persist state right away so a killed job never re-posts what it already sent."""
    run = lambda *a: subprocess.run(a, check=True, capture_output=True)
    run("git", "add", STATE)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode:
        run("git", "commit", "-m", "chore: update copy state [skip ci]")
        run("git", "pull", "--rebase", "--autostash")
        run("git", "push")

def loop(seconds, every=10):
    """Poll the source channel every `every` s for `seconds` s (GitHub cron alone lags by hours)."""
    end = time.time() + seconds
    while time.time() < end:
        try:
            if main(): commit_state()
        except Exception as e:
            print("poll error:", e)
        time.sleep(every)

if __name__ == "__main__":
    self_test()
    assert translate("نام ارز :#NEAR\n✅تارگت ها : 1, 2\n🚀 میزان سود : 16.39%\n\nفقط یک هفته با من باش👈 @erfann_finance")[2] == "#NEAR\n✅ Targets hit: 1, 2\n🚀 Profit: 16.39%"
    k, sym, t = translate("نام ارز #MAGIC/USDT\n⭕️ حالت معامله = LONG 🟢\n⚠️لورج :- 5X 10X 20X\n⚡️ نقطه ورود = [ 0.0635 & 0.0615 ]\n❌ حد ضرر :- 0.0595\n✅ تارگت ها = [ 0.0655 , 0.0680 , 0.0735 ]\n\nفقط یک هفته با من باش👈 @erfann_finance")
    assert k == "signal" and "@" not in t and "Leverage: 5X 10X 20X" in t and t.endswith("Targets: 0.0655 , 0.068 , 0.0735"), t
    assert translate("#AXS/USDT Take-Profit target 1 ✅\nProfit: 51.28% 📈")[0] == "update"
    assert translate("خدایا ممنونم") is None
    assert translate("#KAVA/USDT Reached 0.070600\n\nWe hope You enjoyed our today  signals 🔥🔥\n\n✅ 45.99% Profit") == ("update", "KAVA", "#KAVA/USDT\n🎯 Price: 0.070600\n🚀 Profit: 45.99%")
    assert translate("🎯 Target hit!\n\n #IOTA/USDT at 0.046820\n\nKeep enjoying  signals 🚀\n\n✅ 46.06% Profit")[2].endswith("0.046820\n🚀 Profit: 46.06%")
    assert translate("#Free_signal\n\n76% Profit\n\n👉 VIP : @david_promoter1") == ("update", None, "🚀 Profit: 76%")
    assert translate("THE/USDT 🔴SHORT\nPrices dropped reached $ 0.06075\n\n🏆 176% Profit 💰 Lev: 20x\n\n🎯 Target 1, 2, 3, 4, 5 done ✅\n\n🌟 Congratulation 🪩🎉\n\n🔲 @CCFIndonesianTrader")[:2] == ("update", "THE")
    assert translate("HYPE/USDT 🔴SHORT\nENTRY ZONE AREA\n$ 78.6 - $ 79.6\n\nSL : $ 80.961\n\nTP1: $ 77.7\nTP2: $ 76.6\n\nLEVERAGE 1x - 5x")[0] == "signal"
    assert translate("🎆⤵️ Profit from free ⤵️🎆\n\n   ❗Recover your losses \n   ✅Huge Profit Daily\n   ✅98% accuracy\n   ✅5-7 signals daily\n   ✅ Fast signals (in few minutes)\n\nhttps://t.me/+o-x") is None
    assert translate("📈#IOST/USDT\n\n💥297.39% profit\n\n✔️ Based on 🥇 Ember Strategy (Price Action)") is None
    st = {"map": {"10": [500, "h"], "11": [501, "h"]}, "sym": {"AVAX": "11"}}
    assert find_parent(st, "AVAX", 10) == 500        # replies to its exact parent
    assert find_parent(st, "AVAX", 99) is None        # parent never copied -> no wrong reply
    assert find_parent(st, "AVAX", None) == 501       # no link -> latest signal of that coin
    assert period_ms("Period: 17 hr 29 min ⏰") == (17 * 60 + 29) * 60000 and period_ms("Period: 2 days 3 hr") == 51 * 3600000 and period_ms("x") is None
    H = 3600000
    assert pick([("a", 0), ("b", 10 * H)], 12 * H, 2 * H) == "b" and pick([("a", 0), ("b", 10 * H)], 12 * H, 12 * H) == "a"   # Period picks the right trade
    assert pick([("a", 0)], 12 * H, 2 * H) is None and pick([("a", 0), ("b", 5 * H)], 12 * H, None) == "b" and pick([("a", 20 * H)], 12 * H, None) is None
    assert translate("نام ارز #XYZ/USDT\nLONG 1.234 حد 1.1 هدف 1.5")[0] == "signal"   # unparseable layout still posted, never dropped
    if "--loop" in sys.argv: loop(int(sys.argv[sys.argv.index("--loop") + 1]))
    elif "--run" in sys.argv: main()
    else: print("ok (use --run to post)")
