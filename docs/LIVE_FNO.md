# PaperLab 0.8 — focused live F&O paper workspace

## Run / upgrade

Stop the old server. Back up `.env` and the entire `data` folder. Replace application code and compiled `frontend/dist` with this release, keeping `.env`, `data` and your virtual environment. Database migration is additive to schema 8; old account/ledger history is not deleted or converted into new capital.

Set the sole trading input in `.env`:

```dotenv
PAPER_INITIAL_CAPITAL=5000
```

Existing Upstox data authentication and permission settings remain necessary (`UPSTOX_ENABLED`, `UPSTOX_ACCESS_TOKEN`, `PAPERLAB_MODE`, `DATA_PERMISSION_REFERENCE`). A capital number cannot authenticate a market-data feed. No Qwen, OpenAI or other LLM service is required for this release.

Windows CMD:

```bat
.venv\Scripts\activate.bat
python -m pip install -e ".[test]"
python run.py
```

Open the printed URL and Ctrl+F5. Compiled frontend assets are included; Node is needed only to rebuild development sources. Run a single server process per database.

The app creates or resumes the default NIFTY 50 **index** F&O paper account and starts discovery, historical warm-up and the live feed automatically. There are no menu choices, CSV uploads, manual orders, strategy forms or capital fields. The only trading controls are emergency Pause, Resume and Close & pause. A deliberate pause survives restarts; Resume clears it. A permanent drawdown stop cannot be cleared by Resume.

Capital is used once. Editing `.env` cannot top up or reset an existing experiment. A different value causes a visible configuration block; restore that account's original initial-capital value to resume. Positions, expenses and loss history are retained. Existing managed stock-F&O accounts remain observable and their open positions retain protection when subscribed, but this release adds no stock selection/setup UI. The default trading universe remains NIFTY index derivatives as requested earlier. Prior cash/replay/research records are retained for migration, not active features. Public legacy APIs and menus are unavailable. Legacy engine modules remain as accounting/migration dependencies and for regression testing.

## What the agents actually do

These are cooperating **rule-based software agents**, not independent LLMs or an all-knowing trader. Communication is through persisted, time-stamped messages and decision context; all roles run under one account coordinator. Execution stays behind deterministic price, lot, capital and risk checks.

- Market: validates completed chronological OHLC data, including index data with zero volume.
- Trend: computes ADX and EMA alignment/slope.
- Pattern: checks prior-range breaks and stretched range reversals.
- Strategy: ranks currently signalled trend, breakout and range-reversal candidates using past completed-bar proxy outcomes. It can wait when no candidate qualifies. These are the implemented candidates, not every possible market strategy.
- Risk review: checks evidence from prior closed paper trades. At least five ledger-backed observations, at least 80% losses and negative aggregate net P&L are required to veto that same strategy/regime. Sparse data is marked insufficient.
- Risk: checks full lots, account budget, costs, collateral, expiry and stop placement. Rejection is visible.
- Execution: queues a paper ticket, fills only on a subsequent eligible fresh stored quote, records cash/fees and maintains stop/target/time exits.
- Post-trade review: records factual price extrema, sampled stop/target crossings, fees, result and saved decision features. It does not infer an unobserved causal explanation for a loss.

Proxy comparisons use the underlying index, not historical option returns. They are a conservative ranking aid, not a validated probability or proof of an optimal/profitable strategy. The system does not generate arbitrary new strategies, retrain a neural network or promote untested rules. The concrete adaptation is cooldown, reduced risk, temporary exclusion and evidence-based strategy/regime veto. No automatic relaxation of risk budgets is permitted.

## Live charts and strategy stops

The dashboard shows one-minute candles, forming live price and EMA20/EMA50. Choose the underlying or a discovered contract for viewing only; this does not choose a trade. On the held contract chart, entry, stop and target are overlaid. Underlying charts never display a derivative premium as an index level. Forming candles are marked provisional and never used for strategy decisions. All chart times are IST.

Futures stops use underlying ATR and the chosen strategy's stop factor. Options use observed contract ATR when enough completed contract bars exist; otherwise they use a declared strategy-specific premium fallback (trend 25%, breakout 20%, range reversal 15%). Targets use the chosen strategy's reward/risk multiple. The ticket and review freeze the actual stop/target and their basis. Stops are local paper instructions; they require the app and fresh feed to execute and can be crossed by gaps.

Entries remain disabled on expiry day and after 15:00 IST; intraday exit requests begin at 15:15. No eligible quote means the exit remains pending, not magically filled. Normal weekday sessions only; special trading sessions are not automatically handled.

## Balances

For a bought option, premium and entry charges reduce cash. For example, with ₹5,000 capital and ₹400 premium, cash becomes **₹4,600 minus entry charges**. The premium appears separately as money in open positions. Its changing market value affects equity and unrealised P&L; it is not spendable cash until the position closes.

For futures, the full notional is not deducted. Assumed collateral is reserved separately. Available cash = cash ledger balance − held futures margin − estimated pending-entry reserves. Pending reserves are released on cancellation/rejection and replaced by actual accounting on fill. The display distinguishes cash, available cash, open option premium, margin, pending reserves, expenses, equity and P&L.

The 0.7 risk/cost limits remain: whole lots, one position per account, 1% default maximum/planned risk, 2% daily loss budget, six entries/day, 5% peak-equity drawdown stop, closed-loss cooldown and risk reduction. Long-option risk conservatively includes the full premium plus costs. At ₹5,000, a ₹50 budget will often prevent any F&O trade. The app explains the block; it does not force an order. Futures use 25% notional simulated margin; this is not broker SPAN. Product cost presets are assumptions, not an audited current invoice.

## Recording and learning scope

The database stores received subscribed quotes (including their original feed payload), completed stream bars across sessions, downloaded underlying minute history, role messages, order/ledger evidence, decision snapshots and trade reviews. Restart does not erase them. It records **the subscribed NIFTY underlying and selected derivative contracts**, not every stock/index in the exchange. It cannot record while the app/feed is offline, and historical warm-up is distinguished from observed live data. No complete tick archive is claimed.

The recording grows over time. Back up `data/trading.db` with the server stopped. No silent retention purge is performed. Market data usage remains subject to the user's provider permissions.

## Validation boundary

Software tests use explicitly synthetic quotes and contracts. They check isolation, causal indicators, saved evidence, stop logic, ledger balances, restart safety, API scope and UI rendering. Passing tests does not establish trading profitability. Authenticated Upstox market-session acceptance still requires the user's live data connection.
