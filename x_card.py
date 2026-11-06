"""Render a 1200x675 chart card for an X post: 7-day price line + headline.

Usage: python3 x_card.py <coingecko_id> "<headline>" [out.png]
       python3 x_card.py --brand <in.png|jpg> <out.png>   # add the Kuoala footer to any image (AI art, report card)
Example: python3 x_card.py bitcoin "Fear & Greed 50. Two days ago: 26." x_card.png
Pillow only (no matplotlib on this Mac). Free CoinGecko endpoint, no key.
"""
import json
import os
import sys
import urllib.request

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 675
BG, FG, MUTED, UP, DOWN = "#0f1419", "#e7e9ea", "#71767b", "#00ba7c", "#f4212e"
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "kuoala_logo.jpg")
CTA = "Free signals on Telegram  ·  link in bio"


def brand(img):
    """Return img with a footer strip: round Kuoala logo, @handle, Telegram CTA. Scales with image width."""
    img = img.convert("RGB")
    w, h = img.size
    fh = max(64, w // 14)
    out = Image.new("RGB", (w, h + fh), BG)
    out.paste(img, (0, 0))
    d = ImageDraw.Draw(out)
    d.line([(0, h), (w, h)], fill="#2f3336", width=2)
    ls = int(fh * 0.72)
    logo = Image.open(LOGO).convert("RGB").resize((ls, ls))
    mask = Image.new("L", (ls, ls), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, ls, ls), fill=255)
    pad = fh // 3
    out.paste(logo, (pad, h + (fh - ls) // 2), mask)
    f_h, f_c = ImageFont.truetype(FONT, int(fh * 0.36)), ImageFont.truetype(FONT, int(fh * 0.30))
    d.text((pad * 2 + ls, h + fh // 2), "@KuoalaOfficial", font=f_h, fill=FG, anchor="lm")
    d.text((w - pad, h + fh // 2), CTA, font=f_c, fill=UP, anchor="rm")
    return out


def prices(coin):
    url = f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart?vs_currency=usd&days=7"
    req = urllib.request.Request(url, headers={"User-Agent": "x-card/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return [p for _, p in json.load(r)["prices"]]


def fmt(p):
    return f"${p:,.0f}" if p >= 100 else f"${p:,.2f}" if p >= 1 else f"${p:.4f}"


def render(coin, headline, ps, out):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f_big, f_mid, f_small = (ImageFont.truetype(FONT, s) for s in (64, 40, 26))
    chg = (ps[-1] / ps[0] - 1) * 100
    color = UP if chg >= 0 else DOWN

    d.text((60, 40), headline[:60], font=f_mid, fill=FG)
    d.text((60, 105), fmt(ps[-1]), font=f_big, fill=FG)
    d.text((60 + d.textlength(fmt(ps[-1]), font=f_big) + 30, 125),
           f"{chg:+.1f}% 7d", font=f_mid, fill=color)

    x0, x1, y0, y1 = 60, W - 60, 220, H - 80
    lo, hi = min(ps), max(ps)
    span = (hi - lo) or 1
    pts = [(x0 + i * (x1 - x0) / (len(ps) - 1), y1 - (p - lo) / span * (y1 - y0))
           for i, p in enumerate(ps)]
    d.line(pts, fill=color, width=5, joint="curve")
    d.text((x1 - 260, y0 - 35), f"7d high {fmt(hi)}", font=f_small, fill=MUTED)
    d.text((x1 - 260, y1 + 10), f"7d low {fmt(lo)}", font=f_small, fill=MUTED)
    d.text((60, H - 50), f"{coin.upper()} / USD", font=f_small, fill=MUTED)
    brand(img).save(out)


if __name__ == "__main__":
    if sys.argv[1] == "--brand":
        brand(Image.open(sys.argv[2])).save(sys.argv[3])
        print(sys.argv[3])
        sys.exit()
    coin, headline = sys.argv[1], sys.argv[2]
    out = sys.argv[3] if len(sys.argv) > 3 else "x_card.png"
    ps = prices(coin)
    assert len(ps) > 10, "too few price points"
    render(coin, headline, ps, out)
    print(out)
