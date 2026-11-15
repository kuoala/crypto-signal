"""Find Telegram channels for an equal shout-out swap (SFS). READ-ONLY, uses your own Telegram user session.

    pip install telethon==1.45.0
    export TG_API_ID=... TG_API_HASH=...            # from https://my.telegram.org > API development tools
    python3 sfs_prospects.py --selftest              # offline check of the scoring logic
    python3 sfs_prospects.py --dry-run seed1 seed2   # seeds only, no expansion, prints what it would score
    python3 sfs_prospects.py seed1 seed2 ...         # seeds -> recommendations -> one more level -> sfs_prospects.csv

First run asks for your phone number and login code in the terminal (Telethon's own prompt) and writes
kuoala_user.session. That file IS your login: it is gitignored, never commit or share it.

Read-only by design: it never joins, posts, reacts, marks anything read or messages anyone. It only calls
channels.getChannelRecommendations, channels.getFullChannel and messages.getHistory on public channels.
Per Telegram's API terms the collected data is not used to train any model. FloodWait: Telethon sleeps
automatically up to 5 min; longer waits make us sleep and continue. Plus a fixed pause between channels.
"""
import argparse
import asyncio
import csv
import math
import os
import re
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

OURS = "KuoalaOfficial"
PAUSE = 2.5            # seconds between channels; ponytail: fixed pause, adaptive pacing if FloodWaits pile up
MAX_CHANNELS = 150     # cap per run to stay gentle

SIGNAL_RX = re.compile(r"\b(tp ?\d|take ?profit|stop ?loss|\bsl\b|entry|targets?|leverage|\d+x\b|long|short|vip)", re.I)
NEWS_RX = re.compile(r"\b(news|breaking|report(s|ed)?|announc|etf|sec|update|according to|headline)", re.I)
EDU_RX = re.compile(r"\b(learn|lesson|guide|explained|how to|academy|education|tutorial|beginner|thread|why)", re.I)
EN_RX = re.compile(r"\b(the|and|is|to|of|in|for|this|with)\b", re.I)


def language(texts):
    s = " ".join(texts)
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return "unknown"
    share = lambda lo, hi: sum(lo <= c <= hi for c in letters) / len(letters)
    if share("Ѐ", "ӿ") > 0.3: return "ru"
    if share("؀", "ۿ") > 0.3: return "ar/fa"
    if share("一", "鿿") > 0.2: return "zh"
    if share("ऀ", "ॿ") > 0.3: return "hi"
    words = max(len(s.split()), 1)
    return "en" if len(EN_RX.findall(s)) / words > 0.05 else "latin-other"


def category(title, about, texts):
    sig_posts = sum(len(SIGNAL_RX.findall(t)) >= 3 for t in texts)      # a signal post has entry/TP/SL-style terms
    if sig_posts >= 3 or re.search(r"signal", title + " " + about, re.I):
        return "signals"
    blob = " ".join([title, about] + texts)
    news, edu = len(NEWS_RX.findall(blob)), len(EDU_RX.findall(blob))
    if max(news, edu) < 3:
        return "other"
    return "news" if news >= edu else "education"


def metrics(subs, posts, now):
    """posts: [(datetime, views or None)] newest first. Returns dict of the numbers we rank on."""
    views = [v for _, v in posts if v is not None]
    avg = statistics.mean(views) if views else 0.0
    dates = [d for d, _ in posts]
    span_days = max((now - min(dates)).total_seconds() / 86400, 1 / 24) if dates else 0
    cv = statistics.pstdev(views) / avg if len(views) >= 5 and avg else None
    return dict(subs=subs, avg_views=round(avg), view_rate=round(avg / subs, 3) if subs else 0.0,
                posts_per_day=round(len(dates) / span_days, 2) if dates else 0.0,
                last_post_days=round((now - max(dates)).total_seconds() / 86400, 1) if dates else None,
                views_cv=round(cv, 3) if cv is not None else None)


def suspicious(m):
    """Obvious bought-subscriber / view-bot patterns. ponytail: heuristics only, eyeball the channel before you write."""
    why = []
    if m["subs"] and m["view_rate"] < 0.03:
        why.append("views <3% of subs (dead or bought subs)")
    if m["view_rate"] > 1.5:
        why.append("views >150% of subs (view bots or heavy forwarding)")
    if m["views_cv"] is not None and m["views_cv"] < 0.05:
        why.append("near-identical view counts (view bots)")
    return why


def verdict(m, cat, our_subs):
    fails = []
    if cat not in ("news", "education"):
        fails.append(f"category={cat}")
    if m["view_rate"] < 0.10:
        fails.append("view rate <10%")
    if not 0.5 * our_subs <= m["subs"] <= 3 * our_subs:
        fails.append("size outside 0.5x-3x ours")
    if m["last_post_days"] is None or m["last_post_days"] > 7:
        fails.append("no post in 7 days")
    fails += suspicious(m)
    return fails


def selftest():
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    posts = [(now - timedelta(hours=6 * i), 400 + 37 * i) for i in range(20)]
    m = metrics(2000, posts, now)
    assert m["avg_views"] == 752 and m["view_rate"] == 0.376 and m["posts_per_day"] == 4.21, m
    assert verdict(m, "news", 1500) == [], verdict(m, "news", 1500)
    assert "category=signals" in verdict(m, "signals", 1500)
    assert "size outside 0.5x-3x ours" in verdict(m, "news", 500)
    flat = metrics(10000, [(now, 100)] * 20, now)
    assert any("bots" in w or "<3%" in w for w in suspicious(flat)), suspicious(flat)
    sig = ["BTC LONG entry 60000 TP1 61000 TP2 62000 SL 59000"] * 4
    assert category("Crypto Daily", "", sig) == "signals"
    assert category("Chain Brief", "daily crypto news", ["Breaking: ETF update according to SEC report"] * 3) == "news"
    assert category("Learn DeFi", "", ["How to read a funding rate, explained for beginner traders. Lesson 3"] * 3) == "education"
    assert language(["The market is up and this is why"]) == "en" and language(["Биткоин растёт сегодня"]) == "ru"
    print("selftest ok")


async def run(seeds, dry, out):
    from telethon import TelegramClient, errors
    from telethon.tl.functions.channels import GetChannelRecommendationsRequest, GetFullChannelRequest

    api_id, api_hash = os.environ.get("TG_API_ID"), os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        sys.exit("set TG_API_ID and TG_API_HASH (my.telegram.org)")
    client = TelegramClient("kuoala_user", int(api_id), api_hash, flood_sleep_threshold=300)
    await client.start()

    async def safe(coro_fn, *a):
        for _ in range(3):
            try:
                return await coro_fn(*a)
            except errors.FloodWaitError as e:
                print(f"  FloodWait {e.seconds}s, sleeping")
                await asyncio.sleep(e.seconds + 5)
            except (errors.ChannelPrivateError, errors.UsernameInvalidError, errors.UsernameNotOccupiedError, ValueError) as e:
                print(f"  skip: {e.__class__.__name__}")
                return None
        return None

    async def recs(ch):
        r = await safe(lambda: client(GetChannelRecommendationsRequest(channel=ch)))
        return [c for c in (r.chats if r else []) if getattr(c, "broadcast", False)]

    ours = (await client(GetFullChannelRequest(OURS))).full_chat.participants_count
    print(f"our subscribers: {ours}")
    seen, queue = {}, []
    for s in seeds:
        ent = await safe(client.get_entity, s)
        if ent is None:
            continue
        seen[ent.id] = ent
        if not dry:
            for c in await recs(ent):                       # level 1
                queue.append(c)
            await asyncio.sleep(PAUSE)
    lvl1 = [c for c in queue if c.id not in seen]
    for c in lvl1:
        seen.setdefault(c.id, c)
    if not dry:
        for c in lvl1[:40]:                                  # level 2 (one more), capped
            for c2 in await recs(c):
                seen.setdefault(c2.id, c2)
            await asyncio.sleep(PAUSE)

    rows, now = [], datetime.now(timezone.utc)
    for ent in list(seen.values())[:MAX_CHANNELS]:
        if getattr(ent, "username", None) in (None, OURS):
            continue                                         # private channels can't be swapped publicly
        full = await safe(lambda: client(GetFullChannelRequest(ent)))
        msgs = await safe(lambda: client.get_messages(ent, limit=20))   # getHistory: does not mark anything read
        if not full or msgs is None:
            continue
        texts = [m.message or "" for m in msgs]
        m = metrics(full.full_chat.participants_count or 0, [(x.date, x.views) for x in msgs], now)
        about = full.full_chat.about or ""
        cat = category(ent.title, about, texts)
        fails = verdict(m, cat, ours)
        rows.append(dict(title=ent.title, username=ent.username, link=f"https://t.me/{ent.username}", **m,
                         language=language(texts + [about]), category=cat, passes=not fails, why_not="; ".join(fails)))
        print(f"  {'PASS' if not fails else '----'} @{ent.username} subs={m['subs']} vr={m['view_rate']} {cat}")
        await asyncio.sleep(PAUSE)
    await client.disconnect()

    rank = lambda r: (not r["passes"], r["language"] != "en", -(r["view_rate"] * math.log10(max(r["subs"], 10))))
    rows.sort(key=rank)
    if dry:
        print(f"dry run: scored {len(rows)} seed channels, nothing written")
        return
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["title"])
        w.writeheader()
        w.writerows(rows)
    print(f"{sum(r['passes'] for r in rows)} pass / {len(rows)} scored -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("seeds", nargs="*", help="public channel usernames, without @")
    ap.add_argument("--dry-run", action="store_true", help="score the seeds only; no expansion, no CSV")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--out", default="sfs_prospects.csv")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    elif not a.seeds:
        sys.exit("give 5-10 seed channel usernames")
    else:
        t = time.time()
        asyncio.run(run(a.seeds, a.dry_run, a.out))
        print(f"done in {time.time() - t:.0f}s")
