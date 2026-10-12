# Auto Paper — v0.6.1

## Windows upgrade

1. Stop the server with Ctrl+C. Back up the existing `data` folder.
2. Extract this build over the existing `paperlab` project folder. Keep your `data`, `.env` and `.venv` folders/files.
3. In CMD, run `.venv\Scripts\activate.bat`, then `python -m pip install -e ".[test]"`, then `python run.py`.
4. Refresh the browser with Ctrl+F5. Auto Paper is the new home page. The compiled frontend is included; no npm build is necessary for this archive.

`run.py` now loads `.env` from the project folder. Explicit CMD environment values take precedence. An existing Upstox token and the app's recent-data configuration are still needed for live data. Tokens never belong in an uploaded CSV or chat message.

## Simple flow

Choose an already imported NSE daily dataset or upload a combined/one-day bhavcopy CSV. Enter virtual capital (default ₹5,000). Press **Prepare automatic account**, then **Start automatic paper trading**. No manual stock keys, contract selection, strategy rule or cost JSON is required.

Matching uses ISIN against the current official Upstox NSE master, requiring a unique EQ match. Tick sizes and lot sizes come from that master. Historical instruments remain separate. Missing or ambiguous matches are listed, not guessed. All matched instruments are retained. Up to 2,000 receive the full feed at once; larger universes rotate automatically after 220 minutes of valid open-market feed updates. The dashboard reports active and queued counts. Held and pending instruments are pinned. Removed stocks lose their live bar continuity and re-enter warm-up if selected again. Retained stocks keep their bars. A saved next-batch cursor makes restarts move on to the next cohort; reconnects keep the current cohort and restart its warm-up timer. Complete coverage may require multiple trading days. Subscriptions are removed before new ones are added so the upstream count never exceeds 2,000. This is not support for every security ever listed on NSE.

One shared capital balance finances at most one position. Stocks are evaluated sequentially. All matched stocks are monitored; unaffordable/stale stocks cannot enter. This is not a globally ranked best-stock portfolio optimizer. The stock status table and recent orders show what the scanner is doing and why it waits.

## Strategies and evidence

The CSV supplies the universe. This automatic mode does **not** train a model from its daily prices. Its strategy ranking uses 200–300 completed, continuous live minute bars. A one-day CSV cannot provide intraday history. First-run warm-up is roughly 200 trading minutes, and missing minute bars or reconnects restart that stock's warm-up. Stopping/restarting the application requires restarting scanning. Pausing new entries while keeping the same feed connected allows warm-up to continue.

The existing separate CSV learning/replay tools remain available for historical strategy studies; their learned profiles are not automatically applied by Auto Paper. Daily and minute bars are not mixed.

All catalogue strategies are simulated on rolling completed bars with account costs. Candidates need at least three closed trades, positive closed and marked P&L, profit factor at least 1.2 (or no losses), and maximum drawdown at most 5%. Ranking is refreshed after twenty more minutes of completed evidence. Passing candidates still need a current entry signal. These are rolling historical screens, not independent holdout validation or a profit guarantee.

After a closed loss, new entries pause ten minutes, the losing strategy is excluded for 100 minutes, and the sizing risk budget halves down to a floor of one eighth of the original budget. Peak observed equity drawdown of 5% permanently stops scanner entries for that account. Risk state survives restarts. Protective exits continue when new entries are paused, provided the feed remains connected and fresh. Gap losses can exceed a stop estimate. Pending scanner entries expire after sixty seconds without a fill.

## Automatic costs and risk

Preset simulation assumptions: brokerage 5 basis points of order value capped at ₹20/order; exchange 0.3 bps; STT 10 bps each side; GST 18% on brokerage/exchange/SEBI; stamp duty 1.5 bps on buys; SEBI 0.01 bps; slippage 5 bps. These are approximate cash-equity simulation assumptions, not a verified current invoice for a user's Upstox plan. No delivery depository charge or market-depth model is included. Slippage is charged as a simulated cost by the existing engine.

Capital determines affordability and the 1% initial per-trade risk budget. Daily loss limit is 2%; maximum ten daily entries. Capital alone does not determine brokerage or slippage. For example, the brokerage portion on a ₹1,000 order is ₹0.50 before taxes and other costs. Accounts remain immutable; preparing again creates a separate experiment, not a deposit or reset.

## Validation and limits

Synthetic tests cover matching from one CSV day, account/feed binding, percentage brokerage and cap, unaffordable-stock waiting, strategy gating, one-order exposure, pause cancellation, paper entry/stop-loss fill, loss cooldown, restart persistence and permanent drawdown stop. Browser coverage verifies the new home page, inputs, waiting states and mobile overflow, alongside existing flows.

No user credentials were used in development. Actual full-universe Upstox load/throughput and session acceptance remain untested. A large universe can delay sequential research; stale signals are rejected and the UI reports each stock's state. Upstox is used only for read-only market data; no real broker order endpoint is implemented.

Official reference: https://upstox.com/developer/api-documentation/instruments/ and https://upstox.com/developer/api-documentation/v3/get-market-data-feed/ (checked 8 October 2026).
