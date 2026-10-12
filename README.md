# PaperLab 0.8.1 — live F&O paper trading

**Railway hosting:** [step-by-step deployment and variables](docs/RAILWAY.md). Includes Docker build, HTTPS password protection and persistent-volume checks.

A single automated workspace for NIFTY 50 index futures and options. Set `PAPER_INITIAL_CAPITAL=5000` in `.env`, retain your Upstox data credentials, and run `python run.py`. The app prepares the account and feed automatically. No CSV, strategy, instrument-key or manual-order forms.

The dashboard shows live candles, selected strategy, cooperating agent decisions, positions, strategy-based SL/targets, orders, cash, reserved margin, costs and P&L. Quotes, completed bars, decision context and post-trade reviews persist in SQLite. Loss evidence can reduce/exclude later trades. These are bounded rule-based agents; no guaranteed optimal strategy, neural-network training or profit promise.

**[Setup, Windows upgrade, balances, agent behavior and limits](docs/LIVE_FNO.md)**

Windows CMD, from the project folder:

```bat
.venv\Scripts\activate.bat
python -m pip install -e ".[test]"
python run.py
```

For a new installation, run `python -m venv .venv` first and copy `.env.example` to `.env`. Keep `.env`, your virtual environment and the `data` directory when upgrading. Initial capital and loss history are not reset by an upgrade or an environment edit. A different configured capital value blocks entries rather than adding funds.

Compiled frontend included. For development: `npm --prefix frontend ci` then `npm --prefix frontend run build`. Python 3.11+, one local process, no real broker-order endpoint. The default public API and UI expose F&O only. Historical engine code is retained for migration/accounting and regression tests; earlier release docs describe legacy features, not the current menu.

At ₹5,000 and 1% risk, the ₹50 risk budget often cannot support a full F&O lot. The app reports the reason rather than placing an invalid trade. A ₹400 option purchase would leave ₹4,600 **before charges**, if all risk checks allowed that trade.

Tests: `python -m pytest -q`; browser checks: `python scripts/browser_tests.py` with Playwright Chromium available. See `docs/VERIFICATION.md` for verification scope. Authenticated live Upstox acceptance remains unverified.
