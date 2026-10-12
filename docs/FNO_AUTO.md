# Automated intraday F&O — v0.7.0

## Upgrade on Windows CMD

1. Stop PaperLab with Ctrl+C. Back up your existing `.env` and entire `data` folder. Do not delete your database or replace it with test data.
2. Extract the new ZIP. Copy the new `paperlab` code and compiled `frontend/dist` over your installation, keeping `.env`, `data` and your local `.venv`.
3. From your existing project folder:

```bat
.venv\Scripts\activate.bat
python -m pip install -e ".[test]"
python run.py
```

The included compiled frontend does not require Node to run. If rebuilding it, use `npm --prefix frontend ci` then `npm --prefix frontend run build`. Open the URL printed by run.py and hard-refresh the browser (Ctrl+F5). Health reports 0.7.0. Database schema upgrades additively to 7; do not run an older release against the upgraded database.

## Default NIFTY account

Open **F&O Auto** (the new home screen). Set virtual capital and risk once, leave **Automatic** selected, then **Create NIFTY account**. Press **Start automated F&O**. This is the NIFTY 50 index, not a scanner of its 50 constituent shares. Upstox data configuration in `.env` is still required; the app never sends orders to Upstox.

A single default NIFTY account persists across restarts. Its initial capital is immutable. Repeated setup selects that account rather than injecting money or resetting its loss history. Existing replay and cash-equity accounts are retained separately.

Start discovers the current contracts from the Upstox NSE instrument master and registers their keys, lot sizes, ticks, expiry and strike automatically. It selects the nearest non-expiry-day future and up to seven nearby strikes each for calls and puts. Actual entries are restricted to strikes within 2% of the current underlying. It refreshes the contract universe on the next normal trading day while running. Substantial same-day price movement can leave the selected strikes unsuitable; it waits rather than buying an arbitrary cheap far-OTM option. Pause/restart while flat refreshes the selection.

## Separate stock F&O accounts

Click **Add stock F&O account**, select an eligible stock from the current Upstox master, set its own capital, and create it. Start it from the same account selector. This account can only trade that stock's derivatives; its capital, ledger, risk controls and history are separate. Repeat for another stock if needed. NIFTY and managed stock accounts can run together within the stream subscription limit. Adding one refreshes the shared feed, cancels pending entries and requires fresh completed bars; open contracts remain subscribed.

Only stocks with current derivative contracts appear. The app does not invent futures/options for stocks outside that universe. Standalone/manual spot orders and legacy automation are blocked on managed F&O accounts.

## Signals and execution

The app downloads the previous seven calendar days of one-minute candles plus today's completed intraday candles. It excludes the current partial minute. At least 60 total completed bars and two completed live minute bars since connect or a detected feed gap are required. Normal start warm-up is about 2–3 minutes when provider history is available; it does not imply an order will be placed then.

Rules choose breakout or EMA trend direction in trending conditions, or a price reversal near a band extreme in ranging conditions. These deterministic rules are not ML, a historical profit ranking or a separately validated profitable model. The prior CSV daily-learning workflow remains separate; daily stock bhavcopy is not historical option-premium data.

Automatic mode tries eligible long calls for bullish signals and long puts for bearish signals, then a directional future if no eligible option fits. Futures-only and options-only can be chosen at account creation. It takes one full lot and at most one position per account. No naked option selling or live multi-leg strategies are added.

A queued order expires after 90 seconds. A fill needs a subsequent fresh stored Upstox V3 stream quote, an open normal market segment, correct tick and sufficient cash/risk budget. History, theoretical premiums, stale prices and model estimates cannot fill orders. Feed loss/reconnect cancels pending entries. Ledger evidence references the stored quote ID. This is an LTP-based simulation, not an exchange queue/depth/liquidity model; slippage is charged as a cost, not an adjusted displayed fill price.

## Risk and costs

Defaults: 1% risk per trade, 2% daily loss budget, six daily entries, one position per account. The account setup exposes risk up to 10%; increasing it increases possible loss. Long-option sizing counts the **entire premium plus round-trip costs** against the budget. Futures count the planned stop loss plus costs and reserve **25% notional collateral as a simulation assumption**, not broker SPAN margin. Stops can be crossed by price gaps and cannot guarantee a maximum loss.

With ₹5,000, the default risk budget is ₹50. Typical full option lots and futures collateral will not fit. The app reports BLOCKED rather than using fractional lots, adding virtual money, or quietly increasing risk. Choosing more initial capital is a user decision, not an automatic reset.

Product-specific research cost presets are automatic, immutable in this UI and not calibrated to a verified invoice:

| Assumption | Futures | Long options |
|---|---:|---:|
| Brokerage per order | ₹20 | ₹20 |
| Slippage | 1 bp | 5 bps |
| Exchange estimate | 0.2 bp | 4 bps |
| Sell-side levy estimate | 5 bps | 15 bps |
| Buy stamp estimate | 0.2 bp | 0.3 bp |
| SEBI estimate | 0.01 bp | 0.01 bp |
| GST on brokerage + exchange + SEBI | 18% | 18% |

These are explicitly assumed charges, not legal/tax advice or a certified current Upstox schedule. Displayed unrealised P&L is before prospective exit costs; completed trade net P&L deducts recorded entry/exit costs.

A closed loss pauses new entries for ten minutes, excludes the losing rule for 100 minutes and halves the risk budget, down to one-eighth of its initial scale. A 5% observed peak-equity drawdown permanently stops new entries for that account. Restart cannot reset these controls; Start may reconnect an existing position's protection with entries disabled.

## Intraday exits and monitoring

Normal weekday entries run from 09:15 until 15:00 IST, additionally gated by the provider's actual normal-session status. Stop and target checks run on fresh contract quotes. All positions receive an exit request from 15:15 IST, and any carried position is queued for exit at the next opportunity. The app does not support special weekend/holiday sessions automatically.

Keep the app, computer and feed running. If data disconnects or the market closes, an exit stays **CLOSING / EXIT PENDING** until an eligible fresh quote arrives; there is no fabricated closing price. Restart pauses entries and cancels pending entry orders. Press Start to reconnect monitoring/protection. If a contract expires while disconnected, settlement evidence is required; this automation does not manufacture delivery or settlement. Stock F&O is intended to close intraday and does not arrange real physical delivery.

The home screen polls every two seconds and shows selected-account equity, available cash, costs, open contract/quantity/entry/mark/P&L/stop/target, orders and completed trade results. Activity records starts, queued entries, fills, exits and rejections. **Pause new entries** retains live protection. **Close paper position & pause** queues an exit on the next valid observation.

## Validation and remaining limits

Synthetic tests cover instrument/account boundaries, current-expiry filtering, full-lot rejection, option entry/exit, futures short/margin, quote evidence, stale/fabricated observation rejection, loss controls, intraday exit pending, restart and index feed bars without volume. Browser tests cover the default setup, mobile layout and visible simulated positions/orders. Existing regression tests are retained.

No authenticated Upstox live acceptance test was possible without the user's credentials. Quote availability, entitlements, provider response differences and actual exchange-session behavior must be checked with the user's data connection. No profitability claim follows from passing software tests.

Provider references: https://upstox.com/developer/api-documentation/instruments/ ; https://upstox.com/developer/api-documentation/v3/get-market-data-feed/ ; https://upstox.com/developer/api-documentation/v3/get-intra-day-candle-data/ .
