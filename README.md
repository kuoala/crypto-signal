# Crypto Signal Bot (Free, Real Data)

`signal_bot.py` scans the entire Binance USDT futures market on the **4-hour** timeframe,
detects direction with a combined **EMA + RSI + MACD** strategy, sets the stop loss using
**ATR** and the target using a **risk/reward** ratio, then auto-posts to your Telegram channel.

Signal post format:

```
📊 SIGNAL #0142 | 4H | 2026-06-27 12:00 UTC
─────────────
#BTC/USDT
Direction: 🟢 LONG
Entry: 64,250.50
Stop Loss: 62,800.00
Target: 67,100.00
Leverage: x4
Risk/Reward: 1:2

📈 Why: EMA20 > EMA50, RSI 58 (bullish), MACD crossed up — 4h trend confirmed.
📉 Chart: https://www.tradingview.com/chart/?symbol=BINANCE:BTCUSDT.P
```

When a signal later hits its target or stop, the bot auto-posts the result with a
running monthly win rate — this track record is what builds trust:

```
✅ RESULT — SIGNAL #0142 (#BTC/USDT LONG)
Target hit 🎯 +4.4% (x4 = +17.7%)
Closed 2026-06-28 04:00 UTC
─────────────
📊 June: 18 signals · 12 TP ✅ · 6 SL ❌ · Win rate 67%
```

### How tracking works

Open signals, the next ID, and monthly stats are stored in **`state.json`**. Each run the
bot first checks open signals (using 1h candles to catch TP/SL between runs), posts any
results, then scans for new signals. The same symbol won't get a new signal while one is
still open. On GitHub Actions, the workflow commits `state.json` back to the repo so the
track record persists across runs — do **not** add `state.json` to `.gitignore`.

---

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
| `MIN_24H_VOLUME_USDT` | drop low-volume coins | 30,000,000 |
| `MAX_SIGNALS_PER_RUN` | max signals per run | 5 |
| `POST_HEADER` | optional header on every post | `""` (off) |
| `ATR_SL_MULT` | stop-loss multiplier | 1.5 |
| `RR_RATIO` | target-to-risk ratio | 2.0 |
| `MAX_LEVERAGE` | max suggested leverage | 10 |
| `DRY_RUN` | if `True`, prints only, no sending | `False` |

To add a header/disclaimer above every post, set:
```python
"POST_HEADER": "📊 New Signal | Timeframe 4H\n⚠️ Informational only — not financial advice\n─────────────\n",
```

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
