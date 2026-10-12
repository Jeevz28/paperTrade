## v0.7.0 managed intraday F&O

Default NIFTY 50 **index** F&O account, separate eligible stock F&O accounts, automatic master lookup/near-expiry contract discovery, prior-minute history warm-up, bullish/bearish regime rules, observed-quote long options and signed futures, product-specific simulation costs, full-lot/capital/risk checks, pause/loss/drawdown controls and 15:15 IST exit requests. Orders, open positions, P&L and completed trades are visible on one home screen. See FNO_AUTO.md for limits. Authenticated Upstox acceptance remains untested; this release does not add live multi-leg execution or AI training.

## v0.6.1 larger CSV stock lists

Removed the 2,000-stock preparation rejection. Full-feed subscriptions remain capped at 2,000 and rotate automatically, with held/pending stocks pinned. Dashboard shows active/queued/total coverage. Complete coverage can span trading days; live subscription changes have synthetic tests but no authenticated Upstox acceptance test.

## v0.6.0 Auto Paper

Added a simplified home page, CSV ISIN-to-Upstox matching, automatic shared-capital LIVE account setup, percentage/capped cost presets, multi-stock rolling strategy scanning, pause/loss controls and per-stock status monitoring. See AUTO_PAPER.md for warm-up and 2,000-stock limits. Actual full-universe live acceptance is not yet tested.

# Implementation Status

Version 0.3.0 is a functioning local research release, not a claim that all 73 sections
of the original brief are complete. The runnable application and tests are included.

| Requirement | Implemented | Remaining / limitations |
|---|---|---|
| FastAPI + React/TS + SQLite | Yes | Single-user, single-worker; no distributed jobs |
| Frontend | Original 14 screens plus Activity & Automation, live quote/status tables, decision/order/delivery tabs and basket-job controls | Some advanced analytics remain API-only |
| INR 1,000 default capital | Yes | Explicit account creation; arbitrary new experiment capital allowed |
| Closed accounts and immutable ledger | Yes | Administrator-level tamper prevention requires external controls |
| Reconciliation | Cash, basis, quantities, costs, realized P&L and hash chain | No independently signed external audit log |
| CSV/Parquet import | Yes, all-or-nothing, provenance hashes | No external archive licensing verification |
| Data quality | OHLC, timestamps, volume, duplicate, tick, expiry and gap checks | Official calendar, corporate actions and survivorship not certified |
| Timeframes | 1/3/5/10/15/30m, 1h, 1d stored separately | No implicit aggregation/resampling |
| Indicators | SMA/EMA/WMA/VWAP/anchored VWAP, RSI, MACD, stochastic, ROC, bands, ATR, ADX/DI, volume SMA/RVOL/OBV, Supertrend, per-bar volatility | UI overlays limited to EMA20, VWAP, bands and volume; anchoring uses dataset start; most periods configured in Python rather than UI |
| Structure | Delayed confirmed swings, HH/HL/LH/LL, rolling support/resistance, breakouts | No generalized multiscale level clustering; prior week levels not exposed |
| Regimes | All ten requested regime labels, reasons and heuristic strength | Confidence is not calibrated probability |
| Strategies | Separate Box, Breakout, Retest, Trend, Pullback, VWAP, Bollinger, ORB, Momentum, Mean Reversion, S/R classes | Baseline long variants; full short/variant catalog not implemented |
| Signals and automation | Adaptive selection among eleven long strategies, loss cooldown/quarantine, nonincreasing size cap, drawdown halt and candidate monitor; one enabled rule per account, persisted decisions, lot/risk sizing, next-observation paper orders and stop controls | Managed F&O adds bearish long puts / short futures; no automated multi-leg selection; 51-bar fixed / 200-bar adaptive warm-up; historical selection is not profit validation |
| PaperBroker | Fully funded long spot/options; atomic derivative replay baskets; market/limit, cancel/limit edit, partial standalone exit | No spot shorts; managed single-leg F&O also supports LIVE paper execution; one basket at a time |
| Protection | Stop, target and completed-close trailing stop | Replay evaluates at opens only; no intrabar queue/liquidity model |
| Risk | Round-trip cost-aware size, lot sizes, affordability, exposure, position/trade/loss limits | Daily loss uses conservative initial-capital budget; no margin portfolio engine |
| Costs | Configurable brokerage, exchange, STT, GST, stamp, SEBI, slippage | Generic model, no verified current product-specific schedule |
| Live provider | Read-only REST and V3 Protobuf WebSocket, reconnect/resubscribe, session and freshness gates, quote evidence, optional automatic paper fills | Real broker credentials and entitlements not acceptance-tested; managed single-leg F&O paper execution enabled; live multi-leg execution disabled |
| Backtesting | Single-instrument strategy research plus isolated scheduled multi-leg basket replay with shared accounting, costs, margin and optional delivery | Basket runs limited to 2,000 timestamps and one open basket; no automatic multi-leg optimizer; original single-instrument backtester lacks partial/trailing exits |
| Walk-forward/OOS | Chronological train/validation/test windows and ATR-stop selection | Small fixed parameter grid, independent segments, no compounded OOS equity |
| Research | Event study, regime distribution, strategy comparison; strategy/regime/hour/weekday/instrument strata and Wilson win-rate intervals | Expiry/volatility strata, multiple-testing controls and dependence-adjusted uncertainty remain |
| Replay | Server-owned clock, step/autoplay and background worker, snapshots, automatic strategy decisions and orders | No rewind; one background replay account and one process only |
| Options | Contract metadata, actual-price P&L, chain, PCR/max pain, Greeks/IV; causal imported IV percentile and OI change | No licensed history bundled; model estimates never fill orders |
| Options strategies | Atomic long calls/puts, bull-call/bear-put debit spreads, covered calls and protective puts in replay and scheduled backtests | Exact synchronized leg prices required; no live multi-leg execution; managed long calls/puts supported |
| Futures | Signed replay exposure, evidence-based collateral, margin checks, daily variation and physical delivery obligations | Managed live futures use explicitly assumed 25% notional collateral; no SPAN portfolio-margin calculation |
| Expiry | Evidence-based cash settlement and physical stock delivery for baskets; blocked cash/share obligations, idempotency and ledger reconstruction | No depository transfer, auction/default penalties, corporate actions or standalone-option settlement |
| Journal | Automatic closed-trade records, partial exits, editable notes/tags | Rich indicator-at-entry snapshots and mistake classification remain |
| Alerts | Protective exits and risk-event log; acknowledgement | Full market-event alert subscriptions absent |
| AI | Disabled provider; local Ollama/compatible Qwen, snapshot hash audit | Natural-language SQL and tool invocation intentionally absent |
| Security | Loopback, host/origin checks, mutation header, no frontend credentials | No multi-tenant auth, penetration test or deployment hardening |
| Operations | Additive schema migration, SQLite online backup, new-path restore with integrity checks | No unattended retention policy or cross-version downgrade |
| Verification | Unit, integration, causality and UI test sources included | No real broker credentials, licensed datasets, legal sign-off or independent model validation |

## Priority Work Before A Production Claim

1. Confirm the intended use, data licence and legal obligations with qualified counsel
   and the actual provider. A boolean switch must not be treated as an exemption.
2. Unify a richer execution state machine between backtesting and replay, adding
   tested intrabar/tick behavior, exchange calendars, order reservations, trailing
   exits and liquidity assumptions. Present uncertainty instead of invented fills.
3. Add point-in-time contract-master certification, corporate actions, delivery
   auctions/default penalties. Validate margin/settlement evidence against licensed sources.
4. Acceptance-test V3 streaming and automation with an entitled account and real
   market sessions. Expand stress tests beyond the included protocol/fault fixtures.
5. Expand research strata, complete parameterized strategy variants, confidence
   intervals and reproducibility manifests tied to code and data versions.
6. Expand migration/restore coverage, data versioning and broader security testing.

## Release 0.5

Combined NSE CSV imports, daily trend-conditioned strategy learning, disjoint
validation/holdout evidence, and guarded live daily paper execution are implemented.
See CSV_LEARNING.md for the required per-security study and activation workflow.
No profit guarantee, full-market automatic training or permanent autonomous retraining.
