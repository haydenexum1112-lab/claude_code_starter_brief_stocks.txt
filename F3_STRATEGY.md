# F3 Strategy — Claude Trader

The exact 3-step framework from the playbook — **Frame, Find, Fire** — built on
ICT structure & liquidity, run by **7 AI agents with Claude as the CEO**. Trade
it by hand, or let the AI run it for you. This package (`f3/`) is the automation:
it makes every trade for you, runs in the cloud so it works with your computer
off, and emails you a report morning and night.

> ⚠️ **For research / paper trading.** Futures (NQ, GC) carry real risk. It ships
> in **dry-run** (`F3_EXECUTE=0`) — it logs the orders it *would* place but sends
> nothing until you deliberately enable live execution and wire a broker webhook.
> Validate on paper first.

---

## The F3 framework

Most traders don't lose because their strategy is bad — they lose because they
break their own rules. F3 strips trading to 3 mechanical steps so there's nothing
left to "feel". **If a step is missing, there's no trade.**

| Step | What it checks |
|------|----------------|
| **FRAME** | Higher-timeframe bias from the last clear break of structure, *and* you're inside a killzone. |
| **FIND**  | A liquidity sweep of an obvious high/low, then an FVG / order block entry zone in the discount/premium area aligned with bias. |
| **FIRE**  | A lower-timeframe shift in structure, a stop beyond the sweep, and a minimum **2:1** reward:risk, sized to a fixed small risk %. |

**Markets:** NQ (Nasdaq) and GC (Gold) — cleanest for this style.

**Killzones (the only times we trade):**
- London open — ~02:00–05:00 ET
- New York AM — ~08:30–11:00 ET ← the sweet spot

Outside the killzones, and around high-impact news (NFP, CPI, FOMC), the system
does nothing. Patience is part of the edge.

### The risk rules (the part that keeps you funded)

| Rule | Default |
|------|---------|
| Risk per trade | fixed small % — **0.5%** to start |
| Reward : risk | **minimum 2:1**, every time |
| Stop placement | **beyond the sweep / invalidation** — never in noise |
| Daily loss cap | hit it → **done for the day**, no exceptions (2%) |
| Max trades / day | capped (3) — no over-trading, no revenge |
| Moving a stop | **never** against the trade |

### The daily checklist

```
☐ In a killzone?     ☐ Bias clear?
☐ Liquidity swept?   ☐ Price in an FVG/OB?
☐ LTF confirmation?  ☐ 2:1+ R:R?   ☐ Within daily loss cap?
```

All checked → **FIRE**. Any missing → **SKIP**.

---

## How the AI runs F3 — 7 agents + Claude

It's not one bot guessing. Seven agents each grade one thing, and Claude (the
CEO) weighs all seven and signs off. **Nothing fires unless the team agrees and
Claude confirms.**

| Agent | Checks | Step |
|-------|--------|------|
| **Atlas** | Macro & structure — the higher-timeframe bias | FRAME |
| **Lumen** | News & fundamentals — blackout around NFP/CPI/FOMC | — |
| **Hydra** | Liquidity — finds the sweep and where stops sit | FIND |
| **Hermes** | The setup — maps the FVG / order block entry | FIND |
| **Apollo** | Timing — confirms you're in the killzone window | — |
| **Hephaestus** | Risk — sizes the trade, locks daily loss & drawdown | FIRE |
| **Mnemosyne** | Memory — logs every trade and recalls similar setups | — |

Each agent grades the setup independently, then Claude weighs all seven and
confirms — like a senior trader signing off. **If the risk agent or the structure
says no, the trade dies.** No emotion, no override. The hard vetoes are enforced
in code, so Claude can never approve a setup an agent has failed.

**Flow:** `price data → 7 agents grade it → Claude confirms → risk check → alert / execute.`

> Claude (model `claude-opus-4-8`) provides the final judgement when
> `ANTHROPIC_API_KEY` is set. Without it, a deterministic consensus stands in —
> same hard veto rules, confidence from the agents' own scores — so the engine
> always runs.

---

## What you asked for

| You wanted… | How it's done |
|-------------|---------------|
| "Make all the trades — I don't do any" | FIRE decisions are POSTed to the broker webhook automatically (`f3/alerts.py → execute_trade`). You never place a trade by hand. |
| "Run when my computer is off" | A GitHub Actions cron (`.github/workflows/f3-trader.yml`) runs the stateless tick in the cloud every 15 min. Your machine can be off. |
| "Emails morning and night" | Gmail reports at ~07:30 ET (the plan + rules) and ~16:30 ET (every trade fired & skipped). |

---

## Running it

```bash
pip install -r requirements.txt

python run_f3.py demo      # textbook setup → full agent board + Claude sign-off (offline)
python run_f3.py backtest             # backtest on synthetic data (mechanics check)
python run_f3.py backtest NQ.csv NQ   # backtest on YOUR real OHLC data
python run_f3.py scan      # force a scan now (synthetic feed unless F3_DATA_DIR set)
python run_f3.py morning   # send the morning report now
python run_f3.py night     # send the evening report now
python run_f3.py tick      # one scheduled tick (what the cloud cron calls)

python -m unittest f3.tests.test_f3 -v   # tests (offline)
```

### Prove it first — the backtester

Before risking a funded account, find out whether the strategy actually makes
money. `run_f3.py backtest` runs the **exact same engine** over historical
candles, simulates every trade to its stop or target, and reports:

```
Total return / Max drawdown / Trades / Win rate / Average R / Profit factor
```

With **no arguments it uses a synthetic history** — that only proves the
*mechanics and accounting* work, it is **not** a performance prediction. For a
real verdict, pass your own OHLC export:

```bash
python run_f3.py backtest NQ.csv NQ     # columns: time,open,high,low,close,volume
```

The recommended path to a funded account: **backtest on real data → paper trade
live → only then run the evaluation.**

### Wiring a live data feed

The engine takes `list[Candle]` (oldest→newest, timezone-aware ET). Drop your
broker/vendor OHLC exports as CSVs (`time,open,high,low,close,volume`) into a
folder and point `F3_DATA_DIR` at it (`NQ.csv`, `GC.csv`), or replace
`f3.runner.get_candles()` with a call to your feed. Without a feed it uses a
deterministic synthetic series so dry-runs are safe.

---

## Deploying to the cloud (computer off)

1. Push this repo to GitHub (scheduled workflows run from the **default branch**).
2. In **Settings → Secrets and variables → Actions**, add:
   - **Secrets:** `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `MY_EMAIL`,
     `ANTHROPIC_API_KEY` (optional), `F3_WEBHOOK_URL` (your broker webhook).
   - **Variables:** `F3_EXECUTE` (`0` to dry-run, `1` to place orders),
     `F3_ACCOUNT_BALANCE`, `F3_NEWS_TODAY` (e.g. `CPI` on news days), `F3_DATA_DIR`.
3. The workflow ticks every 15 min on weekdays and sends the emails. Start with
   `F3_EXECUTE=0`, read the dry-run logs + evening emails, and only flip to `1`
   once you trust it.

See `.env.example` for local configuration.

---

## Package layout

```
f3/
  config.py       markets, killzones, news blackout, risk rules
  models.py       Candle, AgentVerdict, ProposedTrade, F3Decision
  market_data.py  CSV loader, synthetic series, textbook setup
  ict.py          swings, break of structure, sweeps, FVG/OB, premium/discount, MSS
  agents.py       the 7 agents (Atlas … Mnemosyne)
  ceo.py          Claude (the CEO) + deterministic consensus fallback
  engine.py       orchestration: analyze → grade → confirm → size → checklist
  state.py        daily trade count / loss cap, persisted JSON
  alerts.py       auto-execute webhook + Gmail morning/night emails
  runner.py       single-tick: morning email / scan & fire / night email
run_f3.py         CLI entry point
.github/workflows/f3-trader.yml   cloud cron (runs with your computer off)
```

> Note: the existing `trading_bot/` package and `main.py` are a separate VWAP
> stocks bot and are left untouched. F3 is fully self-contained under `f3/`.

One good setup, run with perfect discipline, repeated 1000 times — that's the
whole game. Boring on purpose. Boring is what gets paid.
