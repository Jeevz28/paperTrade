# PaperLab v0.7.0 — automated NIFTY index F&O paper trading

The default **F&O Auto** screen creates a **NIFTY 50 index futures/options** account. Enter virtual capital once, create the account, then press **Start automated F&O**. Contract keys, current expiries, whole-lot quantities, minute-history warm-up, cost presets, signal rules and intraday exit requests are automatic. Orders, open positions, fresh-price P&L and completed trades appear on the same page. Use **Add stock F&O account** for an eligible individual stock with separate capital. No CSV is needed for this mode.

Read [F&O setup, Windows upgrade and limitations](docs/FNO_AUTO.md). **With ₹5,000 and 1% risk, only ₹50 can be risked; a full F&O lot will usually be blocked.** Costs and margin are explicit simulation assumptions. No guaranteed profit and no real broker orders.

The older cash-equity CSV workflow remains available:

Open **Auto Paper** → choose/upload NSE CSV → enter capital → **Prepare automatic account** → **Start automatic paper trading**. Instrument mapping, shared account binding, strategy selection and simulation cost defaults are automatic. See [Auto Paper setup and limitations](docs/AUTO_PAPER.md).

# NSE PaperLab

A runnable, local-only **paper trading and research release**, built with FastAPI,
React/TypeScript, SQLite, Pandas, NumPy, SciPy and Apache ECharts. Local paper accounting requires no paid service; provider data access and historical coverage depend on your Upstox permissions and plan. No broker order, fund, payment or holdings endpoint is implemented.

**This is not the entire production-complete specification.** The core workflows
work; advanced execution and market-data cases are explicitly disabled instead of
being represented as complete. See [feature status](docs/FEATURE_STATUS.md).

## Run Locally

Requires Python 3.11+ and Node 20.19+ (Node 22+ recommended).

```bash
cd paperlab
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
npm --prefix frontend ci
npm --prefix frontend run build
python run.py
```

Windows CMD: activate with `.venv\Scripts\activate.bat` and install with `python -m pip install -e ".[test]"` (double quotes). `run.py` binds only to
127.0.0.1, uses port 8000 or the next free port, and prints the exact URL. Production
assets are served by FastAPI; a second server is not needed. SQLite initializes at
`data/trading.db`. Keep one server process/worker per database.

For frontend development, run `npm --prefix frontend run dev` alongside the backend
on port 8000. The Vite server proxies `/api` locally.

## First Workflow

1. Open **Data Management** and register an instrument with its source, lot size
   and tick size. Use `INDEX` for nontradable indices; register actual contracts for
   options. Current metadata must not be assumed valid for an expired contract.
2. Import a legitimately obtained CSV or Parquet file. Required fields:
   `timestamp,symbol,open,high,low,close,volume`. Timestamps are candle START times
   and must include a timezone, e.g. `2024-01-02T09:15:00+05:30`. Prices are INR;
   quantities and volume are units, not lot counts. Optional `open_interest`,
   `expiry`, `strike`, `option_type` are validated where supplied.
3. Select the exact timeframe of the file. There is no implicit resampling. An
   invalid row rejects the whole file; inspect the import audit for reasons.
4. Use **+** beside the account selector to create a replay account. Default capital
   is exactly INR 1,000, not funded automatically. Select the imported dataset.
5. Open **Historical Replay**, choose the instrument and dataset timeframe, and
   advance one candle. Submit a paper order with a valid stop and full lot quantity.
   Advance again to evaluate it at the next recorded open.
6. Inspect **Portfolio**, **Risk**, **Journal** and **Account Ledger**. Export the
   immutable ledger as CSV. Entry rejections never inject virtual money.
7. Run **Backtesting** or **Historical Research** on the imported data. A lack of
   qualifying trades is a legitimate result. No market data is bundled.

All synthetic fixtures live only in tests and are labelled TEST DATA ONLY.

## Added in 0.5

Combined NSE bhavcopy CSV import (up to 1 GB / 5 million source rows), daily strategy
learning with chronological validation and holdout, immutable evidence by market
trend, and guarded reuse in live paper trading. The frontend provides import,
study, review and activation controls. Completed daily history refreshes from Upstox
in the background for this mode. See [CSV learning guide](docs/CSV_LEARNING.md).
This is per-security learning; qualifying performance and future profit are not guaranteed.

## Added in 0.4

Adaptive strategy selection, automatic paper entries, loss cooldowns and strategy
quarantine, reduced quantity caps, persistent drawdown halts, and frontend candidate
rankings. Select **Adaptive** in Activity & Automation, then enable and start the
replay/stream. See [adaptive execution](docs/ADAPTIVE.md) for the 200-bar warm-up,
selection rules, risk behavior and limitations. No market profitability is claimed.

## Added in 0.3

Upstox V3 WebSocket streaming, automatic long-strategy paper execution, a background
replay worker, evidence-based physical stock delivery, scheduled multi-leg backtests,
and the **Activity & Automation** frontend. See [setup and workflows](docs/RUNTIME.md).
Streaming requires valid credentials and documented data-use permission; real-account
connectivity is not acceptance-tested here. All execution and delivery remain simulated.

## Added in 0.2

Atomic replay baskets, collateralized long/short futures, evidence-based daily and
final cash settlement, IV/OI history, research strata with uncertainty intervals,
replay autoplay and snapshots, additive migrations and verified backup/restore.
See [evidence and workflow instructions](docs/EVIDENCE.md), including the CSV schemas
and explicit restrictions. No market dataset, credentials or exchange margin values
are bundled.

## Safety and Data Policy

- Default `PAPERLAB_MODE=education` enforces a rolling 30-day data lag, reflecting
  the May 2026 SEBI educational-data update. It is not a legal clearance.
- Upstox is disabled by default. Valid credentials do not establish a right to use
  or redistribute live data. Read [the regulatory/API review](docs/REGULATORY.md).
- All money is integer paise. Initial capital and ledger events are immutable at
  the database level. Reconciliation failures halt without silent correction.
- No deposits, reset endpoint, automatic account creation or real execution code.
- No AI-generated prices, interpolated candles or invented performance.
- Local host/origin checks and a non-simple request header reject cross-origin
  mutations. This is single-user software, not a multi-tenant service. Do not expose
  it through a public reverse proxy or bind it to a public interface.

## Configuration

`.env.example` documents variables; the application reads the **process environment**.
It does not automatically load an `.env` file. For example, in a POSIX shell:

```bash
export PAPERLAB_DB=data/trading.db
export PAPERLAB_MODE=education
export UPSTOX_ENABLED=false
python run.py
```

Optional Upstox access requires `UPSTOX_ENABLED=true` and `UPSTOX_ACCESS_TOKEN`.
Use the official Upstox developer authorization flow outside the application; never
put secrets in frontend files or paste them into source code. Token expiry stops
data retrieval until the operator supplies a valid token.

Recent/live requests additionally require `PAPERLAB_MODE=licensed_private` and
`DATA_PERMISSION_REFERENCE` naming independently established usage permission.
This is a configuration gate, not permission obtained by the software. Do not
enable it solely because a token works. Live paper orders process on explicit REST
polls or an explicitly started V3 WebSocket stream with account execution enabled,
subject to session and freshness checks. See docs/RUNTIME.md before enabling automation.

Optional local AI:

```bash
export QWEN_ENABLED=true
export QWEN_MODEL=qwen3:8b
export QWEN_RUNTIME=ollama
```

Provision/download the local model separately; hardware, storage and electricity
remain the user's responsibility. Ollama uses loopback port 11434. Any non-`ollama`
runtime selects a local OpenAI-compatible server on port 8080. No cloud API is used.

## Costs and Risk

Costs default to **zero and uncalibrated**, not to a claimed current Indian charge
schedule. Configuration is immutable per experiment. Create a new experiment for
different assumptions. Available JSON fields:

| Field | Unit |
|---|---|
| `brokerage_paise` | Fixed paise per fill |
| `exchange_bps`, `sebi_bps`, `slippage_bps` | Basis points of observed notional |
| `stt_buy_bps`, `stt_sell_bps`, `stamp_buy_bps` | Side-specific basis points |
| `gst_pct` | Percentage on configured brokerage + exchange + SEBI charges |

This generic model cannot certify all segment/product/expiry-specific tax bases,
caps or rounding rules. Slippage is a separate labelled cost included once in total
costs; observed market price is retained unchanged.

Default risk per trade is 1% of current equity, including estimated round-trip costs.
Daily loss budget is conservatively 2% of initial capital. Whole contract lots and
fully funded affordability are mandatory. Gaps and open-only stop observation can
produce losses beyond the planned stop budget.

## Verification

```bash
python -m pytest -q
npm --prefix frontend run build
```

For browser tests, leave port 8000 free and use the isolated runner below. It creates
a temporary database and clearly labelled synthetic test data.

```bash
npm --prefix frontend exec -- playwright install chromium
python scripts/browser_tests.py
```

The test suite covers the INR 1,000 invariant, partial exits, fees, reconciliation,
immutability, idempotency, no-future-data prefix tests, import validation, lot sizing,
option formulas, provider allowlisting, stale-data rejection, AI failure and local
API workflow/security. Broker responses are mocked; live Upstox access is untested.

## Documentation

- [Architecture and entity relationships](docs/ARCHITECTURE.md)
- [Regulatory and API source review](docs/REGULATORY.md)
- [Feature-by-feature status and remaining work](docs/FEATURE_STATUS.md)
- [Verification results](docs/VERIFICATION.md)
- [Streaming, automation, physical delivery and basket backtests](docs/RUNTIME.md)
- Interactive API reference: `/docs` on the local server

Download verified SQLite backups from Settings. Restore to a new path with
`python restore.py backup.sqlite data/restored.sqlite`. Schema 3 upgrades additively;
back up before upgrading. Copying only a live WAL database can miss committed data.
