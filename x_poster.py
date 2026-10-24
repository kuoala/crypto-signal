"""Post one tweet to X via the official API. Usage: python x_poster.py pulse|tip|question|record [--dry-run]
Env: X_API_KEY X_API_SECRET X_ACCESS_TOKEN X_ACCESS_SECRET, TELEGRAM_LINK (e.g. https://t.me/yourchannel)."""
import json, os, sys, time
import requests

STATE = "x_state.json"
LINK = os.environ.get("TELEGRAM_LINK", "")

TIPS = [
    "Most traders lose not because their entries are bad, but because one oversized loss erases ten small wins.\n\nRisk 1% per trade. Boring. Works.",
    "A stop-loss isn't a prediction that you're wrong. It's the price at which you admit the trade idea is invalid.\n\nSet it BEFORE you enter, never after.",
    "Funding rate deeply positive = longs are paying shorts to stay in. When everyone is on one side of the boat, watch the other rail.",
    "Rule I trust: if you can't explain why you're entering in one sentence, you don't have a setup, you have a feeling.",
    "Altcoins mostly follow BTC. Before you chase an alt breakout, check what BTC is doing on the same timeframe. Fighting both is expensive.",
    "Win rate alone means nothing.\n\n40% win rate with 3R winners beats 70% win rate with 0.5R winners. Always look at R, not just wins.",
    "Leverage doesn't change if you're right. It changes how much being wrong costs. Size the stop first, leverage falls out of it.",
    "Volume confirms breakouts. A breakout on thin volume is usually a trap with a nice chart.",
    "Moving your stop further away 'to give it room' is how small losses become account-ending ones.",
    "Memecoins can 10x. They can also go to zero in an hour. Treat them like lottery tickets: tiny size, no averaging down.",
    "Missing a move is free. Chasing one costs money. There's always another setup.",
    "Open interest rising + price rising = new money entering. Open interest rising + price falling = new shorts piling in. Same indicator, opposite stories.",
    "Take partial profit at the first target and move the stop to breakeven. You give up some upside and buy a lot of sleep.",
    "Your best trade journal entry isn't the winner. It's the loss where you followed the plan.",
    "Fear & Greed at extreme fear doesn't mean 'buy now'. It means the crowd is scared. Whether that's opportunity depends on your plan, not the number.",
    "Never risk money you need for rent on a trade. Scared money makes bad decisions, every time.",
    "One strategy tested on 20 trades proves nothing. Look for 100+ trades, out-of-sample, fees included, before trusting any backtest.",
    "ADX under 20 usually means chop. Breakout strategies bleed in chop. Know which regime you're in before you press buy.",
    "If a 'signal' group never posts its losses, it isn't a track record, it's an advert.",
    "Check liquidity before you check the chart. A perfect setup on a thin book gets eaten by slippage.",
    "Revenge trading has a 100% record of making a bad day worse. Close the app. Walk away.",
    "Don't marry a bias. Bull or bear, the market pays the one who adapts fastest, not the one who was loudest.",
]

QUESTIONS = [
    "Honest question: what's the single biggest mistake that cost you the most in crypto?\n\nMine would be moving a stop. Yours?",
    "BTC next 30 days:\n\nA) New highs\nB) Chop sideways\nC) Dump first, then recover\nD) No idea (honest answer)\n\nReply with a letter.",
    "How much of your account do you risk per trade?\n\n<1%\n1-2%\n3-5%\nWhatever feels right 😬",
    "Spot or futures? And why? Genuinely curious what this crowd prefers.",
    "What's one indicator you'd keep if you could only have one on your chart?",
    "Do you take profit in parts, or all-in/all-out? Which one has treated you better?",
    "What's your rule for when you're NOT allowed to trade? (Mine: after two losses in a row, I'm done for the day.)",
    "Alt season: real, or just a story we tell ourselves every cycle? Reply with your take.",
    "What was your first ever crypto buy? Bonus points if it's now worth zero 😅",
    "Which hurts more: getting stopped out then watching it hit your target, or holding a loser that never recovers?",
]


def load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"tip": 0, "q": 0, "recent": []}


def get(url, **kw):
    try:
        r = requests.get(url, timeout=15, **kw)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("fetch failed:", e)
        return None


def pulse():
    fg = get("https://api.alternative.me/fng/?limit=1")
    px = get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana"
             "&vs_currencies=usd&include_24hr_change=true")
    if not (fg and px):
        return None
    f = fg["data"][0]
    row = lambda sym, k: f"{sym}  ${px[k]['usd']:,.0f}  ({px[k]['usd_24h_change']:+.1f}%)"
    return (f"Crypto pulse\n\n{row('BTC', 'bitcoin')}\n{row('ETH', 'ethereum')}\n{row('SOL', 'solana')}\n\n"
            f"Fear & Greed: {f['value']} ({f['value_classification']})")


def record():
    """Real closed trades only, losses included. Mirrors what the channel shows."""
    cutoff = (time.time() - 30 * 86400) * 1000
    rows = []
    for line in open("trades.jsonl"):
        t = json.loads(line)
        if t.get("outcome") != "UNTRACKABLE" and t.get("net_r") is not None and t.get("opened_ms", 0) >= cutoff:
            rows.append(t["net_r"])
    if len(rows) < 10:
        return None
    wins = sum(r > 0 for r in rows)
    return (f"Last 30 days, every closed signal, losses included:\n\n"
            f"Trades: {len(rows)}\nWin rate: {wins / len(rows):.0%}\nNet: {sum(rows):+.1f}R\n\n"
            f"No deleted losses, no cherry-picking. Full log in the channel:\n{LINK}").strip()


def build(kind, st):
    if kind == "pulse":
        return pulse()
    if kind == "tip":
        i = st["tip"]; st["tip"] = i + 1
        return TIPS[i % len(TIPS)]
    if kind == "question":
        i = st["q"]; st["q"] = i + 1
        return QUESTIONS[i % len(QUESTIONS)]
    if kind == "record":
        return record()


def main():
    kind, dry = sys.argv[1], "--dry-run" in sys.argv
    st = load()
    text = build(kind, st)
    if not text or text in st["recent"]:   # never post empty or duplicate text
        print("nothing to post for", kind)
        return
    if dry or not os.environ.get("X_API_KEY"):
        print("--- DRY RUN ---\n" + text)
        return
    import tweepy
    c = tweepy.Client(consumer_key=os.environ["X_API_KEY"], consumer_secret=os.environ["X_API_SECRET"],
                      access_token=os.environ["X_ACCESS_TOKEN"], access_token_secret=os.environ["X_ACCESS_SECRET"])
    print("posted", c.create_tweet(text=text).data)
    st["recent"] = (st["recent"] + [text])[-60:]
    json.dump(st, open(STATE, "w"), indent=1)


if __name__ == "__main__":
    main()
