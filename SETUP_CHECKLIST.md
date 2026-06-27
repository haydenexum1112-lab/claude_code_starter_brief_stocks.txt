# F3 Strategy — Setup Checklist (plain English)

This is the step-by-step to take the system from "practice mode on test data" to
"live and trading on its own." Do it in order. **Don't rush to the last step** —
stay in practice mode until you trust what you see in the emails.

There are 4 things to connect:
1. A **broker** (the account that actually holds money and places trades)
2. A **webhook** (the messenger that carries the trade from the bot to the broker)
3. A **price feed** (so the bot sees the real market, not test data)
4. **Emails + Claude** (the morning/night reports and the CEO sign-off)

---

## Before you start — one decision

**What do you want it to trade?**

- **Stocks / ETFs** (easier, cheaper to start) — e.g. QQQ instead of NQ, GLD
  instead of gold. Works with a normal brokerage. This repo already supports
  Alpaca for stock data.
- **Futures (NQ & GC)** — what the playbook calls for. More powerful but needs a
  **futures broker** and futures data, and carries more risk.

Tell me which and I'll point the bot at the right markets. The steps below work
for either; futures just need a futures-capable broker.

---

## Step 1 — Open a broker account

- [ ] **Stocks/ETFs:** open an [Alpaca](https://alpaca.markets) account
      (has a free **paper/practice** account — start there).
- [ ] **Futures:** open an account with a futures broker that TradersPost
      supports (e.g. Tradovate, TradeStation). Use their **paper/sim** account first.

> Start with the **paper (fake-money) account** at every broker. You can switch
> to real money later by changing one setting.

---

## Step 2 — Set up the webhook (TradersPost)

TradersPost is the bridge: the bot sends a trade to TradersPost, and TradersPost
places it at your broker.

- [ ] Create an account at [traderspost.io](https://traderspost.io)
- [ ] Connect your broker from Step 1 inside TradersPost
- [ ] Create a **strategy** in TradersPost and copy its **webhook URL**
      (looks like `https://traderspost.io/trading/webhook/....`)
- [ ] Keep that URL handy for Step 5

---

## Step 3 — Get a price feed (so it sees the real market)

This is the one part that may need my help to finish, depending on your data source.

- [ ] **Simplest for now:** export recent price history (candles) as CSV files
      named `NQ.csv` and `GC.csv` (columns: `time,open,high,low,close,volume`),
      put them in a folder, and set `F3_DATA_DIR` to that folder.
- [ ] **For a true always-on live feed:** the bot needs to pull fresh prices
      automatically each time it runs. That's a small code change to one function
      (`get_candles` in `f3/runner.py`) to call your broker/data provider's API.
      **Ask me and I'll wire this up for your chosen broker** — it's the trickiest
      piece and easy to get wrong.

> Until this is done, the bot runs on built-in **test data**, so its trades are
> practice only even if everything else is connected.

---

## Step 4 — Emails + Claude (optional but nice)

- [ ] **Emails:** create a Gmail [App Password](https://myaccount.google.com/apppasswords)
      (a special 16-character password just for apps — not your normal one).
- [ ] **Claude (the CEO):** get an API key from
      [console.anthropic.com](https://console.anthropic.com). Optional — without
      it, the built-in consensus signs off instead.

---

## Step 5 — Put the keys into GitHub (so it runs in the cloud)

In your repo on GitHub: **Settings → Secrets and variables → Actions**.

Add these as **Secrets** (the private ones):

| Name | What it is |
|------|-----------|
| `F3_WEBHOOK_URL` | the TradersPost webhook URL from Step 2 |
| `GMAIL_ADDRESS` | your Gmail address |
| `GMAIL_APP_PASSWORD` | the App Password from Step 4 |
| `MY_EMAIL` | where you want the reports sent |
| `ANTHROPIC_API_KEY` | your Claude key (optional) |

Add these as **Variables** (the non-secret settings):

| Name | What to put |
|------|-------------|
| `F3_EXECUTE` | **`0`** for now (practice mode — logs trades, places nothing) |
| `F3_ACCOUNT_BALANCE` | your account size, e.g. `50000` (used for position sizing) |
| `F3_NEWS_TODAY` | leave empty (set to e.g. `CPI` on big news days) |
| `F3_DATA_DIR` | your data folder from Step 3, or leave empty |

---

## Step 6 — Watch it in practice mode 🟢

- [ ] Leave `F3_EXECUTE = 0`.
- [ ] The bot now runs every 15 minutes in the cloud (your computer can be off).
- [ ] Read the **morning and night emails** for a week or two.
- [ ] Check that the trades it *says* it would make look sensible to you.

---

## Step 7 — Go live (only when you're ready) 🔴

- [ ] Switch your broker/TradersPost from paper to your **real** account.
- [ ] Change the GitHub variable `F3_EXECUTE` from `0` to **`1`**.
- [ ] Start small. The risk rules cap each trade at 0.5% of the account and stop
      for the day after the loss cap — but real markets are real risk.

---

## Where it stands today

| Piece | Status |
|-------|--------|
| The trading brain (7 agents + Claude) | ✅ built & tested |
| Code to send trades to a webhook | ✅ built |
| Morning/night emails | ✅ built |
| Runs in the cloud, computer off | ✅ built |
| A webhook URL entered | ⬜ you do this (Step 2 & 5) |
| A broker connected | ⬜ you do this (Step 1 & 2) |
| A live price feed | ⬜ Step 3 — ask me to wire it up |
| Live execution turned on | ⬜ stays OFF until Step 7 |

Nothing trades real money until **Step 7**. You're in the safe zone until then.

**Stuck on any step? Tell me which number and I'll walk you through it.**
