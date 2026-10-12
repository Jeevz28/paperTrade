# PaperLab 0.4 — activity, streaming, automation and delivery

Open **Activity & Automation** in the sidebar. It shows the stream state, fresh/stale
quotes, strategy state and decisions, paper orders and baskets, delivery outcomes,
and background backtest progress. It refreshes every two seconds. Its account filter
includes global stream/backtest events. Audit details use integer paise where named.

## Automatic paper strategies

For automatic selection and loss adaptation, choose **Adaptive** and follow
[the adaptive guide](ADAPTIVE.md). It needs 200 completed bars.

For a fixed strategy, select an account, choose one of the eleven existing long-entry strategies, choose
a spot/long-option instrument, and set the maximum units and ATR stop multiplier.
Enable the strategy. The engine chooses a lot-rounded quantity within that cap and
the account's risk/cost constraints. Rejections are visible, not silently converted
into fills. Only one rule per account is supported; an open position or pending order
prevents another entry. Stops/targets provide exits. This is not an automated
short-options or multi-leg strategy-selection system.

For a **REPLAY** account, click **Start background replay**. It advances one candle
per second, evaluates the newly visible completed data, and queues entries for a
subsequent observed open. **Pause background replay** stops the clock. This worker
continues when you change screens. Manual stepping is blocked while it owns the
selected account. The older Historical Replay autoplay remains a separate,
page-scoped convenience. Use only one driver at a time.

**Stop strategy** cancels that rule's pending entries. Existing positions retain
their protective orders; stopping is not a forced liquidation. Stopping a stream or
replay clock also stops processing protective exits until valid observations resume.
Restarting the server disables automation and cancels its pending entries; it does
not silently restart workers. One server process per database is required.

## Upstox live WebSocket streaming

Configure valid credentials and independently established permission in the backend
environment. For Windows PowerShell:

```powershell
$env:PAPERLAB_MODE = "licensed_private"
$env:DATA_PERMISSION_REFERENCE = "Your actual permission reference"
$env:UPSTOX_ENABLED = "true"
$env:UPSTOX_ACCESS_TOKEN = "Your token"
python run.py
```

Do not commit credentials. `.env` files are not loaded automatically. The default
education mode refuses streaming. The new authorization endpoint is the read-only
`GET /v3/feed/market-data-feed/authorize`; all existing broker-order endpoints remain
absent. The authorized `wss` URL is validated, used in memory and never logged.

Register instruments with exact provider keys. Select up to 50 instruments, and
start the stream. For automatic paper fills, first select a **LIVE** paper account
and check **Process queued paper orders…**. Subscribe to all held and automated
instruments. Then enable its strategy. Starting without an account is data-only.

The app subscribes in `full` mode using binary subscription messages and decodes
the official V3 Protobuf schema. It requires market status `NORMAL_OPEN`, rejects
active closing-auction updates, validates tick size and positive prices, rejects
feed/trade timestamps older than ten seconds or in the future, and deduplicates
observations. Each accepted quote is stored as immutable execution evidence.
Reconnect uses a new authorization URL, resubscribes and clears cached prices and
partial bars, with a retry delay capped at 30 seconds. A disconnect or stream stop
cancels unfilled automatic entries so an old
signal cannot fill immediately on reconnect. Manual pending orders are retained.
Credential failures remain
visible; refresh tokens outside the app and restart the app with the new environment.

Live automation builds one-minute **observed stream bars**, not certified exchange
historical candles. It drops the first partial minute. Volume is the cumulative-volume
change between observed samples within a bucket; unobserved boundary trades cannot
be reconstructed. A missing minute, volume reset or reconnect resets indicator
warm-up. Strategies need at least 51 consecutive stored bars. Index feeds without
cumulative volume do not produce executable strategy bars. A signal is evaluated
after the bar closes; it can fill only on a later valid market observation, never
at a fabricated candle price. No spread, queue-depth or market-impact fill model is
claimed. The account cost model still applies.

Source specifications reviewed 6 October 2026:

- [Upstox Market Data Feed V3](https://upstox.com/developer/api-documentation/v3/get-market-data-feed/)
- [Upstox V3 authorization](https://upstox.com/developer/api-documentation/get-market-data-feed-authorize-v3/)
- [Official schema vendored with this build](https://assets.upstox.com/feed/market-data-feed/v3/MarketDataFeed.proto)

`MarketDataFeed_pb2.py` is generated from the bundled schema using protoc 6.33.5;
runtime protobuf >=6.33.5,<7 is required. No compiler is required to run the app.
The bundled schema SHA-256 is
`3e1c939dce2c83a3fef91405c5ca268dfa089aae973319f56685f12a5afcf612`.
Live entitlement, credentials and real-market connectivity have **not** been
acceptance-tested in this environment; protocol and reconnection tests use mocks.

## Physical stock delivery simulation

This feature applies to **basket positions in replay**, using imported publication
and delivery evidence. It does not instruct a broker/depository to transfer shares.
Register the derivative as `PHYSICAL`, its `underlying` equal to the registered
SPOT symbol, and use the actual historical expiry/strike/lot metadata.

In **Options / F&O → Import point-in-time evidence**, select `delivery` and provide:

```text
instrument_id,underlying_id,effective_at,available_at,delivery_at,reference_price,fees
```

`reference_price` is the official underlying/final settlement reference in INR.
`fees` is the total delivery charge **per registered lot**, in INR, supplied from a
verified schedule. Times require explicit timezones. `effective_at` must be on or
after expiry; publication and delivery cannot precede effectiveness. All rows are
immutable and retain a source reference and file hash. No exchange schedule or
delivery fee is fabricated by the app.

Advance replay until both publication and delivery timestamps are visible, then
click **Apply due physical deliveries** in Activity & Automation. Every derivative
leg in the basket requires matching underlying/reference evidence. Obligations are
netted only within this single basket:

| Position at expiry | Share obligation | Consideration |
|---|---|---|
| Long / short stock future | Receive / deliver units | Imported final reference |
| ITM long / short call | Receive / deliver units | Contract strike |
| ITM long / short put | Deliver / receive units | Contract strike |
| ATM or OTM option | No shares | Expires without exercise |

The model follows the direction of stock-derivative obligations described by
[NSE's individual-security derivatives specification](https://www.nseindia.com/static/products-services/equity-derivatives-individual-securities)
and the [clearing settlement framework](https://www.nseclearing.in/clearing-settlement/equity-derivatives/settlement-mechanism).
Actual delivery timing, charges and settlement references come from your evidence.
It does not implement auction/default penalties, depository authorization,
corporate-action adjustments or cross-account clearing netting.

Long futures receive their final variation before delivery consideration is applied.
Options do not receive a cash intrinsic-value credit: cash moves at strike, while
received stock is booked at the reference value and the option's P&L is recognized
in the ledger. Existing basket shares can meet outgoing obligations. Insufficient
shares or net cash produce **BLOCKED** with the ledger unchanged. Naked short stock,
unlimited debit balances and virtual top-ups are never created. Received shares
remain in the basket and can be closed at later imported stock opens. Reconciliation
recomputes the transformation from immutable delivery evidence.

## Multi-leg backtests

Use the scheduled-basket builder in Activity & Automation. Add the strategy,
contracts, signed quantities, timezone-aware `enter after` and `exit after` times,
and a futures stop when applicable. Multiple scheduled tickets may be included;
the engine still allows only one open basket per experiment. Overlapping schedules
can therefore be rejected. An entry must execute strictly before its exit horizon;
an unfilled entry is cancelled when that horizon becomes visible.

Choose the dataset and capital/risk settings, then run. Costs copy the selected
account's settings, or default to zero if no account is selected. Runs use a temporary
SQLite snapshot and the same broker, collateral, cash settlement, delivery and
reconciliation code as replay. Your accounts and ledger are unchanged. A single
background worker processes at most three queued/running requests; each dataset is
limited to 2,000 replay timestamps. A cancellation control is provided. Interrupted
runs are explicitly marked after a server restart, not silently resumed.

The **Backtests** tab shows progress/status and a downloadable full result with
data provenance, parameters, trades, equity samples, remaining positions and ledger.
Unsynchronized legs remain pending. Missing marks yield unavailable equity. There is
no forced end-of-data close. Optional imported cash settlement and physical delivery
can be applied during a run. This is scheduled basket replay, not automatic option
contract selection or a multi-leg parameter optimizer.

## Tests

```powershell
python -m pytest -q
npm --prefix frontend run build
npm --prefix frontend exec -- playwright install chromium
python scripts/browser_tests.py
```

The browser runner starts its own loopback server and disposable test database; port
8000 must be free. Synthetic test data is never installed in the normal application.
