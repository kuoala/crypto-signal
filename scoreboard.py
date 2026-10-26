"""Scoreboard image for the weekly/monthly report. Real closed-trade numbers only; no exchange branding."""
import io
from PIL import Image, ImageDraw, ImageFont

BG1, BG2, CARD, LINE = (11, 16, 24), (22, 30, 44), (24, 32, 45), (40, 52, 70)
TXT, DIM, GRN, RED = (238, 242, 247), (125, 138, 154), (46, 204, 113), (255, 92, 92)
W, ROWS, S = 1080, 8, 2          # S = supersample factor (drawn 2x, downscaled for smooth edges)


def _f(n):
    return ImageFont.load_default(size=n * S)


def render(kind, label, rows, s, n_open, risk_pct):
    """rows: [(won, coin, tag, pct, lev_pct, r)] in close order ; s: stats() dict. Returns PNG bytes."""
    nwin = sum(1 for r in rows if r[0])
    best = sorted(rows, key=lambda r: -r[3])[:ROWS]
    H = 1000 + 74 * len(best) - 74 * 0 - 150
    im = Image.new("RGB", (W * S, H * S), BG1)
    d = ImageDraw.Draw(im, "RGBA")
    for y in range(H * S):                                   # vertical gradient
        t = y / (H * S)
        d.line((0, y, W * S, y), fill=tuple(int(a + (b - a) * t) for a, b in zip(BG2, BG1)))
    X = lambda v: int(v * S)
    net = s["total_r"] * risk_pct
    up = net >= 0
    acc = GRN if up else RED

    # header
    d.rounded_rectangle((X(60), X(56), X(66), X(150)), X(3), fill=acc)
    d.text((X(86), X(52)), f"{kind} SCOREBOARD", font=_f(44), fill=TXT)
    d.text((X(86), X(110)), label.replace("–", "-"), font=_f(26), fill=DIM)
    # hero number
    d.text((X(60), X(190)), "NET RESULT", font=_f(22), fill=DIM)
    d.text((X(60), X(222)), f"{net:+.1f}%", font=_f(110), fill=acc)
    d.text((X(60), X(350)), f"of account at {risk_pct:g}% risk per trade", font=_f(22), fill=DIM)

    # equity curve (cumulative % per closed trade)
    cx0, cy0, cx1, cy1 = 560, 190, 1020, 380
    d.rounded_rectangle((X(cx0), X(cy0), X(cx1), X(cy1)), X(20), fill=CARD)
    eq = [0.0]
    for r in rows:
        eq.append(eq[-1] + r[5] * risk_pct)
    lo, hi = min(eq), max(eq)
    span = (hi - lo) or 1.0
    pad = 28
    pts = [(X(cx0 + pad + i * (cx1 - cx0 - 2 * pad) / max(len(eq) - 1, 1)),
            X(cy1 - pad - (v - lo) / span * (cy1 - cy0 - 2 * pad))) for i, v in enumerate(eq)]
    zero_y = X(cy1 - pad - (0 - lo) / span * (cy1 - cy0 - 2 * pad))
    d.line((X(cx0 + pad), zero_y, X(cx1 - pad), zero_y), fill=LINE, width=S)
    d.polygon(pts + [(pts[-1][0], zero_y), (pts[0][0], zero_y)], fill=acc + (40,))
    d.line(pts, fill=acc, width=3 * S, joint="curve")
    ex, ey = pts[-1]
    d.ellipse((ex - 7 * S, ey - 7 * S, ex + 7 * S, ey + 7 * S), fill=acc, outline=TXT, width=2 * S)
    d.text((X(cx0 + 22), X(cy0 + 14)), "EQUITY CURVE", font=_f(18), fill=DIM)

    # stat tiles
    pf = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    tiles = [("SIGNALS", str(s["n"]), TXT), ("WIN RATE", f"{nwin / s['n'] * 100:.0f}%", TXT),
             ("PROFIT FACTOR", pf, TXT), ("AVG / TRADE", f"{s['avg_r']:+.2f}R", acc)]
    tw = (W - 120 - 3 * 18) // 4
    for i, (k, v, c) in enumerate(tiles):
        x = 60 + i * (tw + 18)
        d.rounded_rectangle((X(x), X(415), X(x + tw), X(525)), X(18), fill=CARD)
        d.text((X(x + 20), X(432)), k, font=_f(18), fill=DIM)
        d.text((X(x + 20), X(462)), v, font=_f(40), fill=c)

    # trades
    d.text((X(60), X(560)), "TOP TRADES", font=_f(20), fill=DIM)
    d.text((X(W - 60), X(560)), "SPOT  ·  LEVERAGED", font=_f(20), fill=DIM, anchor="ra")
    y, mx = 600, max(abs(r[3]) for r in best) or 1.0
    for won, coin, tid, pct, lp, _ in best:
        c = GRN if won else RED
        d.rounded_rectangle((X(60), X(y), X(W - 60), X(y + 62)), X(14), fill=CARD)
        d.rounded_rectangle((X(60), X(y), X(70), X(y + 62)), X(5), fill=c)
        d.text((X(92), X(y + 15)), f"{coin}/USDT", font=_f(28), fill=TXT)
        d.text((X(92 + 215), X(y + 21)), tid, font=_f(20), fill=DIM)
        bw = abs(pct) / mx * 170                              # magnitude bar
        d.rounded_rectangle((X(500), X(y + 26), X(500 + bw), X(y + 36)), X(5), fill=c + (150,))
        d.text((X(W - 84), X(y + 15)), f"{pct:+.1f}%  ·  {lp:+.0f}%", font=_f(28), fill=c, anchor="ra")
        y += 74

    d.text((X(60), X(y + 14)), f"Losses included  ·  {n_open} still open  ·  Not financial advice", font=_f(20), fill=DIM)
    im = im.crop((0, 0, W * S, X(y + 64))).resize((W, y + 64), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()
