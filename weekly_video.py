"""Weekly ~30 s vertical (1080x1920) Instagram Reel: last week's market move + our results, in plain words.

    python3 weekly_video.py --plan-only        # data + on-screen script + caption, no render (fast check)
    python3 weekly_video.py                    # also renders weekly_video/kuoala_weekly.mp4 with OpenMontage
    python3 weekly_video.py --selftest

Audience: general Instagram users, not traders. So: big short lines, rough round numbers, one simple lesson,
one clear call to action (free Telegram channel). Honesty stays: wins AND losses, where the signals came from
(relayed third-party signals are never presented as ours), "Not financial advice" on screen and in the caption.
Data: the same closed-trade set as `alt_breakout.py --export-report WEEKLY` (last completed Mon-Sun UTC week)
plus BTC's real move over that week (CoinGecko, no key).
Render: OpenMontage's Remotion "Explainer" composition (github.com/calesthio/OpenMontage, AGPL-3.0, used
unmodified as an external tool). OM_DIR points at a checkout with `npm ci` done in remotion-composer/.
Outputs (gitignored): weekly_video/kuoala_weekly.mp4, props.json, meta.json (Instagram caption).
Env (optional): INVITE_REELS_IG = the named invite link from make_invite_link.py / the Telegram app.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

OUT = "weekly_video"
OM_DIR = os.path.expanduser(os.environ.get("OM_DIR", "~/OpenMontage"))
CHANNEL = "t.me/KuoalaOfficial"
NFA = "Not financial advice"
GREEN, RED, BG = "#00ba7c", "#f4212e", "#0f1419"


def week():
    """(label, lo_ms, hi_ms, closed trades in the window, risk_pct). Read-only, same window as the report."""
    import alt_breakout as ab
    _, _, lo, hi, _, label = next(w for w in ab.report_windows(int(time.time() * 1000)) if w[4] == "WEEKLY")
    closed, _ = ab.channel_trades(ab.load_state())
    return label, lo, hi, [c for c in closed if lo <= c["T"] < hi], ab.P["RISK_PCT"]


def btc_week(lo, hi):
    url = (f"https://api.coingecko.com/api/v3/coins/bitcoin/market_chart/range?vs_currency=usd"
           f"&from={lo // 1000}&to={hi // 1000}")
    req = urllib.request.Request(url, headers={"User-Agent": "kuoala-weekly/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        ps = [p for _, p in json.load(r)["prices"]]
    assert len(ps) > 10, "too few BTC points"
    return ps


def rough(x):
    """0.57 -> 'more than half'. Plain words for a general audience; never rounds a loss away."""
    for lim, w in ((0.9, "almost all"), (0.7, "most"), (0.5, "more than half"), (0.4, "almost half"),
                   (0.3, "about a third"), (0.2, "about a quarter")):
        if x >= lim:
            return w
    return "a small part"


def summarise(cs):
    rs = [c["r"] for c in cs]
    wins, losses = [r for r in rs if r > 0], [r for r in rs if r <= 0]
    return dict(n=len(rs), wins=len(wins), losses=len(losses), total_r=sum(rs),
                gross_win=sum(wins), gross_loss=-sum(losses),
                best=max(cs, key=lambda c: c["pct"]), worst=min(cs, key=lambda c: c["pct"]),
                relayed=sum(c["src"] == "Main" for c in cs))


def plan(label, cs, risk, ps):
    s = summarise(cs)
    chg = (ps[-1] / ps[0] - 1) * 100
    net = s["total_r"] * risk
    hook = (f"Bitcoin went nowhere last week. So how did our trades do?" if abs(chg) < 1.5 else
            f"Bitcoin {'dropped' if chg < 0 else 'jumped'} about {round(abs(chg))}% last week. So how did our trades do?")
    if s["total_r"] < 0:
        lesson = "A red week. It happens. Small, equal risk on every trade keeps it small."
    elif s["losses"] and s["gross_loss"] >= 0.3 * s["gross_win"]:
        lesson = f"{s['losses']} losses took back {rough(s['gross_loss'] / s['gross_win'])} of the gains. Winning often isn't enough."
    else:
        lesson = f"Best trade {s['best']['pct']:+.0f}%. Worst {s['worst']['pct']:+.0f}%. Same risk every time."
    if s["relayed"] == s["n"]:
        source = "All picked from other channels. Every result shown, losses too."
    elif s["relayed"]:
        source = "Some ours, some picked from other channels. Every result shown."
    else:
        source = "All from our own rules. Every result shown, losses too."

    T = [(0, 3.5), (3.5, 7.5), (7.5, 11.5), (11.5, 16.5), (16.5, 21), (21, 26)]
    tc = lambda i, text, fs=92, **kw: dict(id=f"s{i}", type="text_card", text=text, fontSize=fs,
                                           in_seconds=T[i][0], out_seconds=T[i][1], **kw)
    logo = dict(backgroundImage="kuoala/logo.jpg", backgroundOverlay=0.62)
    cuts = [
        tc(0, hook, 100, **logo),
        tc(1, f"We shared {s['n']} trades. {s['wins']} won. {s['losses']} lost."),
        tc(2, f"{'Up' if net >= 0 else 'Down'} {abs(net):.1f}% for the week, risking {risk:g}% per trade.", 84),
        tc(3, lesson, 80),
        tc(4, source, 80),
        tc(5, f"Free every day on Telegram. {CHANNEL}", 88, **logo),
    ]
    props = dict(cuts=cuts, overlays=[dict(type="section_title", text=NFA, position="top-left", in_seconds=0, out_seconds=T[-1][1])],
                 captions=[], audio={},
                 themeConfig=dict(backgroundColor=BG, surfaceColor="#16202a", textColor="#e7e9ea", mutedTextColor="#71767b",
                                  primaryColor=GREEN, accentColor="#1d9bf0", chartColors=[GREEN, RED, "#1d9bf0", "#f5c470"]))
    caption = (f"{hook} ({label})\n\n"
               f"{s['wins']} won, {s['losses']} lost. {source}\n\n"
               f"Free signals and market updates on Telegram: link in bio.\n{NFA}. Crypto is risky.\n\n"
               f"#crypto #bitcoin #cryptotrading #trading #btc")
    meta = dict(week=label, instagram=dict(caption=caption), invite_link=os.environ.get("INVITE_REELS_IG") or f"https://{CHANNEL}")
    check_honesty(props, meta, s)
    return props, meta


def check_honesty(props, meta, s):
    blob = (json.dumps(props) + json.dumps(meta)).lower()
    banned = [b for b in ("guarantee", "risk-free", "100x", "buy now", "vip", "can't lose", "sure profit") if b in blob]
    assert not banned, f"banned phrase in copy: {banned}"
    assert NFA.lower() in json.dumps(meta["instagram"]).lower() and NFA.lower() in json.dumps(props["overlays"]).lower()
    assert f"{s['losses']} lost" in json.dumps(props["cuts"]), "losses must be on screen"
    if s["relayed"]:
        assert "other channels" in json.dumps(props["cuts"]), "relayed signals must be disclosed"


def render(props, out_mp4):
    rc = os.path.join(OM_DIR, "remotion-composer")
    if not os.path.isdir(os.path.join(rc, "node_modules")):
        sys.exit(f"OpenMontage Remotion composer not installed at {rc} (git clone + `npm ci` there)")
    os.makedirs(os.path.join(rc, "public", "kuoala"), exist_ok=True)
    shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "kuoala_logo.jpg"),
                os.path.join(rc, "public", "kuoala", "logo.jpg"))
    pj = os.path.abspath(os.path.join(OUT, "props.json"))
    subprocess.run(["npx", "remotion", "render", "src/index.tsx", "Explainer", os.path.abspath(out_mp4),
                    f"--props={pj}", "--width=1080", "--height=1920", "--codec=h264", "--crf=20"], cwd=rc, check=True)
    # silent AAC track: Instagram's Reels pipeline expects H.264 + AAC
    tmp = out_mp4 + ".tmp.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", out_mp4, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-c:v", "copy", "-c:a", "aac", "-shortest", "-movflags", "+faststart", tmp], check=True)
    os.replace(tmp, out_mp4)


def selftest():
    cs = [dict(src="Main", coin="AAA", pct=1.0, r=0.2), dict(src="Main", coin="BBB", pct=0.5, r=0.1),
          dict(src="Breakout", coin="CCC", pct=-3.0, r=-1.0), dict(src="Main", coin="DDD", pct=6.0, r=1.5)]
    props, meta = plan("02 Nov – 08 Nov 2026", cs, 1.0, [100_000 - 100 * i for i in range(50)])
    text = json.dumps(props["cuts"])
    assert "Bitcoin dropped about 5% last week." in text and "1 lost" in text and "Some ours" in text, text
    assert 25 <= props["cuts"][-1]["out_seconds"] <= 45
    assert rough(0.57) == "more than half" and rough(0.05) == "a small part"
    red = plan("x – y", [dict(src="Breakout", coin="Z", pct=-2, r=-1.0)], 1.0, [1, 1, 1] * 5)[0]
    assert "red week" in json.dumps(red["cuts"]) and "Down 1.0%" in json.dumps(red["cuts"])
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    os.makedirs(OUT, exist_ok=True)
    label, lo, hi, cs, risk = week()
    if not cs:
        print(f"NO_TRADES {label}: no video this week")
        sys.exit()
    props, meta = plan(label, cs, risk, btc_week(lo, hi))
    json.dump(props, open(os.path.join(OUT, "props.json"), "w"), indent=1)
    json.dump(meta, open(os.path.join(OUT, "meta.json"), "w"), indent=1, ensure_ascii=False)
    print("\n".join(c.get("text", c.get("title", "")) for c in props["cuts"]))
    print("---\n" + meta["instagram"]["caption"])
    if "--plan-only" not in sys.argv:
        render(props, os.path.join(OUT, "kuoala_weekly.mp4"))
        print(os.path.join(OUT, "kuoala_weekly.mp4"))
