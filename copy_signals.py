"""Mirror vetted signal channels into our channel, translated to English.
Only posts signals that parse fully and pass a live-price gate (fresh, sane stop/targets,
tradable on MEXC futures). Profit-claim posts, promos and chatter are never copied."""
import json, os, sys
from sources import fetch_page, parse_signal, check, mexc_symbol, exchange, self_test
from telegram_poster import send_message

STATE = "copy_state.json"

def fmt(ch, s):
    n = lambda xs: " , ".join(f"{x:g}" for x in xs)
    return (f"#{s['symbol']}/USDT  ({'🟢' if s['side'] == 'LONG' else '🔴'} {s['side']})\n"
            f"Leverage: {s['lev'] + 'x (max)' if s['lev'] else 'low (≤5x)'}\nEntry: {n(s['entries'])}\n"
            f"Stop Loss: {s['stop']:g}\nTargets: {n(s['targets'])}\nSource: @{ch}")

def main():
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    if "last_id" in state:  # migrate old single-channel state
        state = {"erfann_finance": state["last_id"]}
    for ch in json.load(open("channels.json")):
        posts = sorted(fetch_page(ch))
        if not posts: continue
        last = state.get(ch, posts[-1][0])  # first run: start from now, never replay stale signals
        for pid, _, text in posts:
            if pid <= last: continue
            s = parse_signal(text)
            if s:
                sym = mexc_symbol(s["symbol"])
                why = "not on MEXC futures" if not sym else check(s, exchange().fetch_ticker(sym)["last"])
                print(f"{ch}/{pid} {s['symbol']}: {'POST' if not why else 'skip - ' + why}")
                if not why and not send_message(fmt(ch, s)):
                    break  # retry next run
            last = pid
        state[ch] = last
    json.dump(state, open(STATE, "w"))

if __name__ == "__main__":
    self_test()
    main() if "--run" in sys.argv else print("ok (use --run to post)")
