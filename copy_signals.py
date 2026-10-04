"""Mirror signal posts from t.me/s/erfann_finance into our channel, translated to English.
Posts are templated, so translation is regex-based (no API/key). Non-signal posts are skipped."""
import html, json, os, re
import requests
from telegram_poster import send_message

SRC = "erfann_finance"
STATE = "copy_state.json"

def parse(text):
    """Return English text for a signal/result post, else None."""
    t = text.replace("‏", "")
    m = re.search(r"نام ارز\s*:?\s*#(\w+)(?:/USDT)?", t)
    if m and "حالت معامله" in t:
        side = "LONG" if "LONG" in t else "SHORT"
        num = lambda key: re.search(key + r"[^\[\d]*\[?\s*([\d.,&\s]+?)\s*\]?\s*(?:\n|$)", t)
        lev, ent, stop, tps = (re.search(p, t) for p in
            (r"لورج[^\n]*?:-?\s*([^\n]+)", r"نقطه ورود\s*=\s*\[\s*([^\]]+?)\s*\]",
             r"حد ضرر\s*:-?\s*([\d.]+)", r"تارگت ها\s*=\s*\[\s*([^\]]+?)\s*\]"))
        if not (ent and stop and tps):
            return None
        return (f"#{m.group(1)}/USDT\nDirection: {'🟢' if side == 'LONG' else '🔴'} {side}\n"
                f"Leverage: {lev.group(1).strip() if lev else '-'}\nEntry: {ent.group(1).strip()}\n"
                f"Stop Loss: {stop.group(1)}\nTargets: {tps.group(1).strip()}")
    m = re.search(r"نام ارز\s*:?\s*#(\w+)\s*\n.*تارگت ها\s*:\s*([\d,\s]+)\n.*میزان سود\s*:\s*([\d.]+%)", t, re.S)
    if m:
        return f"#{m.group(1)}\n✅ Targets hit: {m.group(2).strip()}\n🚀 Profit: {m.group(3)}"
    if re.match(r"#\w+/USDT Take-Profit target \d", t):  # already English
        return t
    return None

def fetch(before=None):
    d = requests.get(f"https://t.me/s/{SRC}", timeout=20).text
    for b in d.split("tgme_widget_message_wrap")[1:]:
        pid = int(re.search(rf'data-post="{SRC}/(\d+)"', b).group(1))
        m = re.search(r"js-message_text[^>]*>(.*?)</div>", b, re.S)
        if m:
            yield pid, html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br/?>", "\n", m.group(1))))

def main():
    last = json.load(open(STATE))["last_id"] if os.path.exists(STATE) else None
    posts = sorted(fetch())
    if last is None:  # first run: start from now, never replay stale signals
        last = posts[-1][0]
    for pid, text in posts:
        if pid <= last:
            continue
        out = parse(text)
        if out and not send_message(out):
            break  # retry this one next run
        last = pid
    json.dump({"last_id": last}, open(STATE, "w"))

if __name__ == "__main__":
    # self-check against real posts
    assert parse("نام ارز #NEAR/USDT\n⭕️ حالت معامله = SHORT 🔴\n⚠️لورج :- 5X 10X 20X\n⚡️ نقطه ورود = [ 4.672 & 4.684 ]\n❌ حد ضرر :- 4.850\n✅ تارگت ها = [ 4.634 , 4.580 ]\n").startswith("#NEAR/USDT\nDirection: 🔴 SHORT\nLeverage: 5X 10X 20X\nEntry: 4.672 & 4.684\nStop Loss: 4.850\nTargets: 4.634 , 4.580")
    assert "Profit: 16.39%" in parse("نام ارز :#NEAR\n✅تارگت ها : 1\n🚀 میزان سود : 16.39%\n\nفقط")
    assert parse("hello") is None
    import sys
    main() if "--run" in sys.argv else print("ok")
