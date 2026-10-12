## v0.8.1 Railway support — 12 October 2026

Final full Python suite: **183 passed**, including 11 new hosted-mode cases.
Production frontend build passed. A real subprocess smoke check passed assigned
PORT binding, the anonymous Railway health probe, authenticated requests through
HTTPS proxy headers and process termination. The existing eight browser tests
passed for v0.8; frontend behavior is unchanged in this patch.

New tests cover page/API/asset authentication, malformed credentials, host/origin
and HTTP rejection, request markers, credential/storage validation, automatic
Railway detection, single-worker ownership and database persistence after restart.
Docker is not available in this execution environment: an actual image build and
Railway deployment have not been performed. Live authenticated Upstox acceptance
remains unverified. See RAILWAY.md for required deployment settings.

## v0.6.1 verification

130 Python tests passed. The new synthetic 2,597-stock fixture verifies that preparation keeps every match while the stream subscribes to 2,000 and reports 597 queued. Planner and socket tests cover complete coverage across batches, pinning, unsubscribe-before-subscribe ordering, continuity invalidation, and reconnect-on-subscription failure. Authenticated live rotation has not been tested.

# Verification Report

Date: 8 October 2026, version 0.5.0. All numerical test data is explicitly synthetic software
test data; no test results below describe market strategy profitability.

## Automated Results

- Python suite: **118 passed** using Python 3.12.14.
- Frontend: TypeScript check and Vite production build passed using Node 24.19.0.
- Playwright: **5 passed**, including desktop/mobile empty states, Activity tabs,
  derivative/evidence controls, backup download, import, account creation, replay,
  next-open fill, ledger, adaptive strategy enable/stop, monitor state and background replay controls.
- Desktop and 390px-wide mobile screenshots visually inspected. No horizontal
  document overflow in the mobile test; no browser page errors in the empty-state
  test. A rendered candlestick chart was inspected in the labelled test workflow.

The browser test's explicit INR 1,000 account bought one synthetic test unit at
INR 100.10, leaving INR 899.90 cash and unchanged INR 1,000 marked equity at that
observation, with zero configured costs. Initial capital remained INR 1,000 and
the ledger reconciled. This is an accounting test, not a market result.

## Coverage

| Area | Checks |
|---|---|
| Accounting | Fixed initial capital, buy/sell/partial exits, costs, no top-ups |
| Integrity | SQL immutability, cash corruption halt, quantity corruption halt |
| Orders | Idempotency, cancellation, limit modification, next-bar fill, naked-short rejection |
| Risk | Insufficient capital, lot-aware sizing, stop-triggered exit |
| Data | Invalid OHLC/time/volume/prices, duplicates, atomic rejection, Parquet |
| Time | As-of filtering, indicator prefix invariance, future price/volume perturbations |
| Strategies | Prefix isolation for all eleven registered strategies and regimes |
| Backtests | Repeatability, equity-prefix invariance, walk-forward window ordering |
| Options | Black-Scholes known value, put-call parity, IV inversion, long-option payoff |
| Provider | GET-only requests, nondata endpoint rejection, no redirects, stale quote rejection, HTTP failures |
| AI | Disabled mode, provider outage, no order writes |
| API | Import/replay workflow, referenced data deletion rejection, host/origin/request-header controls |
| UI | Desktop/mobile, truthful empty data, import/account/replay/order/ledger flow |
| Derivatives | Signed futures, collateral, margin increase close, spread atomicity, missing legs, daily settlement, final zero-price expiry, physical refusal, hash reconciliation |
| Operations | Backup/restore, no overwrite, schema upgrade preserves ledger, malformed evidence atomic rejection |
| Research additions | IV/OI as-of filtering, observation percentile, IST grouping and Wilson interval |
| Physical delivery | ITM/OTM long call, covered call, protective put, long future; stock receipt/resale, cash/share shortage blocks, due-time gating and idempotency |
| V3 streaming | Official Protobuf roundtrip, valid/invalid socket hosts, stale/duplicate quotes, market-state blocking, reconnect/resubscription and secret redaction |
| Live execution | Synthetic streamed entry and stop-loss exit reconcile; duplicate update cannot double-fill |
| Automation | Completed-prefix warm-up, idempotent decisions, queued-before-next-bar entry, stop, background replay completion, API controls |
| Adaptive selection | Causal replay visibility, 200-bar warm-up, duplicate evaluation, real candidate ranking determinism, evidence filters, closed-loss cooldown, alternative strategy selection, nonincreasing size and persistent drawdown halt |
| Basket jobs | Deterministic repeated runs, isolation from source ledger, asynchronous API completion, cancellation signal and malformed timestamp rejection |

## Verification Limits

- No Upstox credentials were used. No live market request, entitlement check,
  actual broker integration acceptance test, or legal permission verification occurred.
- No locally hosted Qwen model was running; successful inference is not acceptance-tested.
- No real market dataset was supplied. Strategy profitability, historical accuracy,
  contract metadata and product-specific fees have not been validated.
- Synthetic coverage does not establish the completeness of exchange calendars,
  corporate-action handling, expiry settlement, futures margin or complex orders.
- Default Playwright CDN downloads initially returned invalid archives. An alternate
  official mirror supplied Chromium Headless Shell 134; tests used an explicit
  `CHROMIUM_EXECUTABLE_PATH`. The project does not ship that browser binary.
- API tests needed loopback socket access in this runtime. A restricted run hung;
  rerunning with that access completed successfully.
- In this update, server and browser commands initially ran in separate network
  contexts, causing connection-refused failures. `scripts/browser_tests.py` runs
  both in one context with a fresh temporary database; all four tests passed.
- A new browser workflow initially timed out on a scoped form-label locator. The
  strategy region and instrument selector now have explicit accessible names; the
  full enable/run/pause/stop workflow passed after the correction.
- The installed Starlette emitted one deprecation warning about its HTTPX test-client
  compatibility. The tests pass; migrate that development integration when needed.
- Vite reports a large chart-inclusive JavaScript chunk. This is a local application
  build warning, not a failed build. Additional lazy loading remains an optimization.

See FEATURE_STATUS.md for the explicit gap between this release and the full brief.

## Release 0.5 checks

Nine additional Python tests cover streamed CSV identity/timing, atomic rollback
across batches, explicit series exclusions, history/cost prerequisites, real study
with no invented profits, disjoint windows, fixed pre-holdout finalists, profile
identity/cost/time gates, daily live entry plus loss cooldown, signal order expiry,
and rejection of provider/CSV disagreement. The two supplied daily samples were
accepted with 1,818 and 1,815 EQ records respectively in disposable databases.
The complete three-year CSV and live Upstox credentials were not available.
The 1 GB / 5 million-row limits are implemented bounds, not a full-size load-test claim.

The additional browser workflow covers combined NSE CSV import, security selection,
background study, an honest no-qualifying-settings result and full profile review.

## v0.7.0 managed F&O verification

Full Python regression run: 142 passed before the final four focused cases were added. Final affected-module run: 62 passed, including all 16 managed-F&O tests (four additional cases cover real-rule scan/warm-up, stale stored quotes, drawdown restart protection and additive migration). Frontend production TypeScript/Vite build passed. Playwright: 9 passed, including desktop/mobile managed-account creation and visibly rendered synthetic open positions/orders. All test prices/contracts are explicitly synthetic; screenshots with a position are marked as software-test data.

Authenticated Upstox network acceptance and trading profitability are not established by these tests. Fees/margin are declared simulation assumptions. The app remains single-user, single-process and paper-only.
## v0.8.0 final verification — 12 October 2026

Final full Python regression: **172 passed**. TypeScript/Vite production build:
**passed**. Chromium/Playwright browser checks: **8 passed**, covering the focused
live workspace, balances, chart/contract selection, emergency controls, missing
configuration, immutable capital mismatch, mobile layout and explicit API errors.
Backend cases include environment-driven startup, pause persistence with data
recording, shutdown races, API isolation, reservations, strategy stops, persistent
charts and ledger-backed agent reviews. Test market data is synthetic.

The compiled frontend is included in the release ZIP. No credentials, account
database, browser binaries or dependency folders are included. Live authenticated
Upstox acceptance has not been performed. Tests establish software behavior, not
strategy profitability. Agents are bounded rules with retrospective evidence;
they do not train a neural model or discover arbitrary new strategies.

Non-blocking warnings: a Starlette/HTTPX test-client deprecation and Vite's large
chart-inclusive JavaScript chunk warning. The older results below are historical.

