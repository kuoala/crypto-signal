# Crypto Signal Bot (Free, Real Data)

`signal_bot.py` scans the most liquid Binance USDT futures pairs on the **4-hour**
timeframe, detects direction with a combined **EMA + RSI + MACD** strategy, sets the stop
loss using **ATR**, then auto-posts to your Telegram channel.

To keep quality high, several **filters** run before any signal is posted: it requires a
real trend (**ADX ≥ 22**, the single most effective filter — see [Backtesting](#backtesting)),
trades only **with the BTC market regime** and the **1-day trend**, requires a **fresh**
momentum turn (no chasing extended moves), skips **overbought/oversold** exhaustion, and
excludes **stablecoins / pegged assets**, micro-move (< 1% stop) and blow-off-volatility
(ATR > 9%) trades. Only the **40 most liquid** pairs are scanned; expectancy degrades
steadily as the universe widens into thin pairs.

Each signal ships **two targets** and a staged, risk-managed exit: **Target 1** at +1R
(take 30% off, move the stop to **breakeven**) and **Target 2** at +2R (let the rest run).
Once Target 1 is reached the trade can no longer become a loss — the worst case is a
small +0.3R at breakeven. Anything still open after **10 days** is closed at market.

Signal post format:

```
📊 SIGNAL #0142 | 4H | 2026-06-27 12:00 UTC
─────────────
#BTC/USDT
Direction: 🟢 LONG
Entry: 64,250.50
Stop Loss: 62,800.00
Target 1 (30%, +1R): 65,700.00
Target 2 (70%, +2R): 67,100.00
Leverage: x4
Risk/Reward: 1:2

💡 Plan: take 30% off at Target 1, move stop to breakeven, let the rest run.
📈 Why: EMA20 > EMA50, RSI 58 (bullish), MACD crossed up, ADX 27 — 4h trend confirmed.
📉 Chart: https://www.tradingview.com/chart/?symbol=BINANCE:BTCUSDT.P
```

As a trade progresses the bot posts staged updates and, on close, a result with a running
monthly win rate and **average R** (expectancy) — this track record is what builds trust:

```
🎯 TP1 — SIGNAL #0142 (#BTC/USDT LONG)
First target hit +2.3% (x4 ≈ +9.0%) on 30% of the position.
Stop moved to breakeven — risk-free now, letting the rest run to Target 2 🚀

✅ RESULT — SIGNAL #0142 (#BTC/USDT LONG)
Final target hit 🎯 net +1.70R (+3.9%, x4 ≈ +15.3%)
Closed 2026-06-28 04:00 UTC
─────────────
📊 June: 18 closed · 10 TP ✅ · 4 BE ⚖️ · 4 SL ❌ · Win 78% · Avg +0.61R
```

Closing outcomes: **SL** −1R (stop before TP1), **BE** +0.3R (returned to entry after TP1),
**TP2** +1.7R (final target after TP1), **time exit** — closed at market after 10 days,
scored at whatever it was actually worth.

### How tracking works

Open signals, the next ID, and monthly stats are stored in **`state.json`**, and every
closed trade is appended to **`trades.jsonl`** with its entry conditions (RSI, ADX, stop
distance) so the strategy stays auditable and re-backtestable. Each run the bot first
checks open signals (using 1h candles fetched from the signal's open time, so trades older
than a few days still resolve correctly), posts any results, then scans for new signals.
The same symbol won't get a new signal while one is still open. On GitHub Actions, the
workflow commits `state.json` back to the repo so the track record persists across runs —
do **not** add `state.json` to `.gitignore`.

### Backtesting

`research/` holds the harness used to choose the filter values above. It replays the
strategy bar-by-bar over real cached OHLCV, reproducing the live cadence, filter stack,
per-run signal selection and staged exits.

```bash
python research/fetch_data.py      # cache ~11 months of 4h/1h/1d candles
python research/backtest.py        # single run of the current config
python research/sweep.py           # filter ablation + candidate variants
python research/verify_parity.py --prod   # confirm the harness matches analyze()
```

Parameters were chosen on data up to 2026-02 and validated on 2026-03 onward.
Out of sample, the shipped config scores **+0.20R per signal at 61% win rate**, against
**−0.00R at 50%** for the previous configuration, with max drawdown down from 82R to 16R.
`verify_parity.py` cross-checks the harness against the real `analyze()` so the two cannot
silently drift apart.

> Two findings worth keeping in mind before loosening anything: memecoin signals were
> **−0.15R out of sample and no filter combination rescued them** (hence `MEME_ENABLED`
> defaults to `False`), and the old "post a daily/spot signal when the 4h scan finds
> nothing" fallbacks opened real tracked trades precisely when the strategy said there
> was no setup — they have been removed, and the remaining fallbacks are informational only.

---

> **Note on data source / GitHub Actions:** Binance blocks US IP addresses (HTTP 451),
> and GitHub-hosted runners are in the US — so on GitHub the bot automatically falls back
> to the first reachable exchange in `CONFIG["EXCHANGES"]` (Binance → Bybit → OKX → Gate).
> All are free and need no API key. Run logs print which exchange was used. When you run
> locally (outside the US), Binance is used.

## 1) Requirements

```bash
pip install ccxt pandas numpy requests
```

> Binance market data is free and **requires no API key**.

---

## 2) Create a Telegram bot (one time)

1. Message **@BotFather** in Telegram → `/newbot` → choose a name and username.
2. Copy the bot token (looks like `123456:ABC-DEF...`).
3. Add the bot to your channel as an **admin** (with Post Messages permission).
4. Channel ID:
   - Public channel: `@your_channel_username`
   - Private channel: a negative number like `-1001234567890`
     (post a message in the channel, then check `https://api.telegram.org/bot<TOKEN>/getUpdates` to find the ID).

---

## 3) Configuration

Two options:

**a) Environment variables (safer):**
```bash
export TELEGRAM_BOT_TOKEN="123456:ABC..."
export TELEGRAM_CHAT_ID="@your_channel"
```

**b) Directly in the file:** edit the `CONFIG` block at the top of `signal_bot.py`.

Key settings in `CONFIG`:

| Key | Purpose | Default |
|---|---|---|
| `TIMEFRAME` | analysis timeframe | `4h` |
| `MIN_24H_VOLUME_USDT` | drop low-volume coins | 5,000,000 |
| `MAX_SCAN_SYMBOLS` | scan only the N most liquid pairs | 40 |
| `MAX_SIGNALS_PER_RUN` | max signals per run | 5 |
| `ATR_SL_MULT` | stop-loss multiplier | 1.5 |
| `RR_RATIO` | Target 2 (final) reward-to-risk ratio | 2.0 |
| `TP1_FRACTION` | portion booked at Target 1 | 0.3 |
| `MAX_HOLD_DAYS` | close anything still open after N days | 10 |
| `MAX_LEVERAGE` | max suggested leverage | 10 |
| `REGIME_FILTER` | only trade with the BTC market trend | `True` |
| `HTF_CONFIRM` | require the 1d trend to agree | `True` |
| `FRESH_BARS` | momentum must have turned within N bars | 3 |
| `RSI_LONG_MAX` / `RSI_SHORT_MIN` | overbought/oversold exhaustion guard | 72 / 28 |
| `MIN_SL_PCT` | skip trades whose stop is < this % away | 1.0 |
| `ADX_MIN` | require a real trend, not a chop-driven cross | 22 |
| `MAX_ATR_PCT` | skip blow-off volatility | 9.0 |
| `MEME_ENABLED` | also scan memecoin perps each run | `False` |
| `MEME_MIN_24H_VOLUME_USDT` | memecoin volume floor | 1,000,000 |
| `MEME_MAX_SIGNALS_PER_RUN` | max memecoin signals per run | 2 |
| `MEME_MAX_LEVERAGE` | leverage cap for memecoins | 5 |
| `MEME_ATR_SL_MULT` | wider memecoin stop-loss multiplier | 2.0 |
| `DRY_RUN` | if `True`, prints only, no sending | `False` |

### Memecoin signals

**Disabled by default** (`MEME_ENABLED: False`). Over Sep 2025 – Aug 2026 memecoin signals
returned **−0.15R per trade out of sample**, and tightening them (ADX, volatility, volume
gates) made them *worse*, not better — the losses are not a filtering problem. Set
`MEME_ENABLED: True` to re-enable, knowing the measured cost.

When enabled, each run also scans memecoin USDT-perps (DOGE, PEPE, WIF, BONK, FLOKI, …) using the same
EMA + RSI + MACD strategy. The universe is a curated list merged with CoinGecko's
`meme-token` category (so new memecoins are picked up automatically); multiplier tickers
like `1000PEPE` are matched too. Memecoin signals use a lower volume floor, a wider ATR
stop (2.0×), capped leverage (x5), are posted with a `🐸 MEME SIGNAL` header plus a
volatility warning, and are tracked for TP/SL results like all other signals.

---

## 4) First test (no sending)

Set `DRY_RUN` to `True` in the file and run:
```bash
python signal_bot.py
```
Signals print to the terminal only. Once the output looks right, set `DRY_RUN` back to `False`.

---

## 5) Automated posting via GitHub Actions (recommended)

The workflow `.github/workflows/futures_signals.yml` runs `signal_bot.py` every 4 hours
on GitHub's servers and posts to your channel — no need to keep your own machine on.

**Setup:**

1. Push this repo to GitHub.
2. Go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `TELEGRAM_BOT_TOKEN` — your bot token from @BotFather
   - `TELEGRAM_CHANNEL_ID` — your channel (`@your_channel` or `-100...`)
3. Open the **Actions** tab and enable workflows if prompted.
4. Test it now: open **Actions → Futures Signals (signal_bot) → Run workflow** (manual trigger).

After that it runs automatically on the schedule below.

**Schedule** (in `futures_signals.yml`):
```yaml
on:
  schedule:
    - cron: "1 0,4,8,12,16,20 * * *"   # every 4h, 1 min after candle close (UTC)
  workflow_dispatch:                    # manual run button
```

> Notes:
> - GitHub Actions cron always runs in **UTC**.
> - Scheduled runs can be delayed a few minutes during peak load — that's normal.
> - Scheduled workflows are auto-disabled after ~60 days of no repo activity; just push a commit or re-enable to keep them running.
> - This repo also contains `signals.yml` (daily aggregated report via `main.py`) and `breaking_news.yml`. Disable any workflow you don't want from the Actions tab so the channel isn't over-posted.

**Alternative — local cron (Linux/macOS):**
```bash
crontab -e
1 */4 * * * cd "/path/to/Telegram Signal" && /usr/bin/python3 signal_bot.py >> bot.log 2>&1
```
**On Windows:** create a Basic Task in Task Scheduler with the same interval.

---

## Notes

- This is an **automated technical-analysis** tool, not a profit guarantee. Signals can be wrong.
- Make it clear on your channel that signals are for educational/informational purposes only and are not financial advice.
- You can change the strategy in the `analyze()` function (e.g. add a volume filter or multi-timeframe confirmation).
- To reduce weak signals, raise `MIN_24H_VOLUME_USDT` or tighten the `score` condition.

---

# Alt-Breakout pipeline (Hyperliquid, long-only, 4h) — `alt_breakout.py`

Implements the spec in the Alt-Breakout design doc: Rule A (published) and Rule B (BTC-regime
shadow, log-only) on a 20-alt Hyperliquid universe. Runs by `.github/workflows/alt_breakout.yml`
hourly (evaluates the latest closed 4h bar; GitHub drops scheduled runs). Uses the same `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHANNEL_ID`
secrets. **Separate from the Binance/MEXC bot above** — own state, own IDs.

```bash
python alt_breakout.py --selftest   # offline check: indicators, exits, kill rules
python alt_breakout.py --dry-run    # live data, prints only, writes nothing
```

| File | Purpose |
|---|---|
| `breakout_state.json` | open + closed trades per rule, next ID, kill-switch status |
| `breakout_log.jsonl` | append-only log of every signal, exit and kill event (never edited) |

Implemented: closed-bar-only signals, no-lookahead daily EMA200, ADX/CHOP/Donchian per §5,
2×ATR stop, 3×ATR ratcheting chandelier, 30-bar time stop, gap-aware stop fills, 4-position cap
with ADX→CHOP→alpha ranking, 0.2% cost + actual hourly funding in net R, data-gap exclusion,
funding-feed-stale pause, kill rules (rolling-30 PF/avg R, 12% DD → publishing suspended),
weekly/monthly reports, exit posts, signal IDs.

Deliberate deviations: (1) signals publish up to up to 3.9 h after bar close, not 2 min — Actions cron
lags; later than that the signal is skipped; (2) kill switch has **no auto-resume**: set
`"suspended": null` in `breakout_state.json` after review; (3) Rule B is only logged, never posted;
(4) the universe is a fixed list in the file — refresh quarterly and add a line to `UNIVERSE_LOG`.
Parameters are frozen until 100 live trades; changes ship as a new version with a new track record.

**Rule C (shadow, top-60 universe):** same rules as A on the 60 most liquid Hyperliquid perps
(`UNIVERSE_WIDE`). Logged and graded in `breakout_state.json` / `breakout_log.jsonl`, never posted.
Backtest (`research/breakout_bt.py`): top-60 PF 0.87 vs 1.31 for the 20-coin universe — widening the
universe lost money, so A stays on the proven 20 until live Rule C data says otherwise.
