"""Mirror every signal from the source channel(s) into ours, translated to English:
signals, and every update on them (targets hit / profit) posted as a reply to our copy of the
original. Channel names, @mentions and links are stripped. If a source post is edited, our copy is edited."""
import hashlib, json, os, re, sys
from sources import fetch_posts, parse_signal, clean, self_test
from telegram_poster import send_get_id, edit_message

STATE = "copy_state.json"

def sig_text(s):
    f = lambda xs: " , ".join(f"{x:g}" for x in xs)
    return (f"#{s['symbol']}/USDT\nDirection: {'🟢' if s['side'] == 'LONG' else '🔴'} {s['side']}\n"
            f"Leverage: {s['lev'] or '-'}\nEntry: {f(s['entries'])}\nStop Loss: {s['stop']:g}\n"
            f"Targets: {f(s['targets_raw'])}")

def update_text(t):
    """Profit/target update -> English, else None. Persian template or already-English."""
    t = t.replace("‏", "")
    m = re.search(r"نام ارز\s*:?\s*#(\w+)[^\n]*\n.*?تارگت ها\s*:\s*([\d,\s]+?)\s*\n.*?میزان سود\s*:\s*([\d.]+%)", t, re.S)
    if m:
        return m.group(1), f"#{m.group(1)}\n✅ Targets hit: {m.group(2).strip()}\n🚀 Profit: {m.group(3)}"
    m = re.match(r"#(\w+)(?:/USDT)?\s+(?:Take-Profit|Stop)", t, re.I)
    return (m.group(1), t) if m else (None, None)

def translate(text):
    """-> (kind, symbol, english) or None for posts we can't translate (promos / free text)."""
    s = parse_signal(text)
    if s:
        return "signal", s["symbol"], clean(sig_text(s))
    sym, u = update_text(text)
    return ("update", sym, clean(u)) if u else None

def find_parent(st, sym, reply_to):
    """Our message_id to reply to. A source reply must map exactly; only a link-less update falls back to the coin's latest signal."""
    src = str(reply_to) if reply_to else st["sym"].get(sym)
    return st["map"][src][0] if src in st["map"] else None

def main():
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    for ch in json.load(open("channels.json")):
        st = state.get(ch)
        if not isinstance(st, dict):            # migrate old {"ch": last_id}
            st = {"last": st, "map": {}, "sym": {}}
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
            if tr:
                kind, sym, text = tr
                reply = find_parent(st, sym, p["reply_to"]) if kind == "update" else None
                mid = send_get_id(text, reply)
                if mid is None: break           # retry next run
                st["map"][key] = [mid, h]
                if kind == "signal": st["sym"][sym] = key
                print(f"{ch}/{p['id']} {kind} {sym} -> {mid}{' (reply)' if reply else ''}")
            st["last"] = p["id"]
        st["map"] = dict(sorted(st["map"].items(), key=lambda kv: int(kv[0]))[-300:])  # bound state
        state[ch] = st
    json.dump(state, open(STATE, "w"))

if __name__ == "__main__":
    self_test()
    assert translate("نام ارز :#NEAR\n✅تارگت ها : 1, 2\n🚀 میزان سود : 16.39%\n\nفقط یک هفته با من باش👈 @erfann_finance")[2] == "#NEAR\n✅ Targets hit: 1, 2\n🚀 Profit: 16.39%"
    k, sym, t = translate("نام ارز #MAGIC/USDT\n⭕️ حالت معامله = LONG 🟢\n⚠️لورج :- 5X 10X 20X\n⚡️ نقطه ورود = [ 0.0635 & 0.0615 ]\n❌ حد ضرر :- 0.0595\n✅ تارگت ها = [ 0.0655 , 0.0680 , 0.0735 ]\n\nفقط یک هفته با من باش👈 @erfann_finance")
    assert k == "signal" and "@" not in t and "Leverage: 5X 10X 20X" in t and t.endswith("Targets: 0.0655 , 0.068 , 0.0735"), t
    assert translate("#AXS/USDT Take-Profit target 1 ✅\nProfit: 51.28% 📈")[0] == "update"
    assert translate("خدایا ممنونم") is None
    st = {"map": {"10": [500, "h"], "11": [501, "h"]}, "sym": {"AVAX": "11"}}
    assert find_parent(st, "AVAX", 10) == 500        # replies to its exact parent
    assert find_parent(st, "AVAX", 99) is None        # parent never copied -> no wrong reply
    assert find_parent(st, "AVAX", None) == 501       # no link -> latest signal of that coin
    main() if "--run" in sys.argv else print("ok (use --run to post)")
