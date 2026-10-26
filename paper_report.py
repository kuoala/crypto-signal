"""Paper-account report over every signal source (weekly + monthly). Prints markdown; posts nothing.

    python paper_report.py > PAPER_REPORT.md        # run paper_mirror.py first to refresh the mirror rows

Sources: signal_bot (trades.jsonl), alt_breakout rules A/B/C (breakout_state.json), mirror = every copied channel
replayed on candles (paper_mirror.jsonl, scale-out model). Every trade is scored in R; RISK% is the account
risk per trade used for the equity column. Costs are already inside each source's R.
"""
import json
from collections import defaultdict
from datetime import datetime, timezone

RISK = 1.0   # % of account risked per trade


def load():
    rows = []                                                   # (source, closed_ms, r)
    for l in open("trades.jsonl"):
        t = json.loads(l)
        if t["outcome"] != "UNTRACKABLE":
            rows.append(("signal_bot" + (" (paper)" if t.get("paper") else ""),
                         datetime.strptime(t["closed_at"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc).timestamp() * 1000, t["net_r"]))
    for rule, v in json.load(open("breakout_state.json")).items():
        if isinstance(v, dict):
            rows += [(f"alt_breakout {rule}" + ("" if rule == "A" else " (shadow)"), c["exit_T"], c["r"]) for c in v["closed"]]
    try:
        rows += [("mirror " + r.get("ch", "erfann_finance"), r.get("t", r["ms"]), r["scale_r"]) for r in map(json.loads, open("paper_mirror.jsonl")) if r["status"] == "closed"]
    except OSError:
        pass
    return rows


def stats(rs):
    n, w = len(rs), sum(r > 0 for r in rs)
    eq = peak = dd = 0.0
    for r in rs: eq += r; peak = max(peak, eq); dd = max(dd, peak - eq)
    gp, gl = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    return f"| {n} | {w / n:.0%} | {sum(rs) / n:+.2f} | {sum(rs):+.1f} | {sum(rs) * RISK:+.1f}% | {gp / gl if gl else float('inf'):.2f} | {dd:.1f} |"


def table(rows, keyf, title):
    print(f"\n## {title}\n")
    for src in sorted({r[0] for r in rows}):
        print(f"**{src}**\n\n| period | trades | win | avg R | total R | acct @{RISK:g}% | PF | maxDD R |\n|---|---|---|---|---|---|---|---|")
        by = defaultdict(list)
        for s, ms, r in sorted(rows, key=lambda x: x[1]):
            if s == src: by[keyf(ms)].append(r)
        for k in sorted(by): print(f"| {k} " + stats(by[k]))
        print("| **all** " + stats([r for s, ms, r in rows if s == src]) + "\n")


if __name__ == "__main__":
    rows = load()
    d = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc)
    print(f"# Paper report — generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\n\nR = profit / initial risk. Not posted to the channel.")
    table(rows, lambda ms: "%d-W%02d" % d(ms).isocalendar()[:2], "Weekly")
    table(rows, lambda ms: d(ms).strftime("%Y-%m"), "Monthly")
