# Upstox API and Indian Regulatory Review

Research checked: 5 October 2026. This is an engineering assessment, not legal advice,
a legal opinion, SEBI approval, or permission to use exchange data. Obtain qualified
Indian securities-law advice before offering the software to anyone else.

## The material 2026 change

SEBI's 8 May 2026 circular, effective 1 July 2026, sets a 30-day lag for educational
sharing and usage of price data. It replaces the earlier one-day sharing and
three-month education-use provisions. The one-day simulation-lab exception is for
NISM, not general-purpose paper-trading apps. Data-sharing agreements, diligence
and usage audit trails remain relevant. It also addresses using recent named-security
data to indicate future prices, advice or recommendations.

**Product decision:** education is the default, and the backend rejects data newer
than its rolling 30-day cutoff. This is a deliberately conservative technical rule;
the lag alone does not confer a data licence or settle the legal classification of
an actual business. No prizes, referrals, monetization, public signals or shared
live-data service are included.

Source: [SEBI May 2026 circular](https://www.sebi.gov.in/sebi_data/attachdocs/may-2026/1778242522289.pdf).

## Real-time data is not automatically licensed for simulation

The May 2024 circular restricts intermediaries' and market infrastructure
institutions' sharing of real-time price data with third parties, subject to its
market-functioning/regulatory purposes and agreement requirements. Its original
educational lag was subsequently amended as described above. A free API, an access
token, and a disclaimer do not establish rights to redistribute data or operate a
public virtual-trading service.

**Product decision:** bind to loopback only. Live access requires
`PAPERLAB_MODE=licensed_private`, `DATA_PERMISSION_REFERENCE` and explicit Upstox
configuration. The reference is an operator assertion, NOT a verification or
exemption. Keep it disabled until the proposed use has been independently approved
by the relevant data provider and advisers.

Source: [SEBI May 2024 circular](https://www.sebi.gov.in/sebi_data/attachdocs/may-2024/1716559553659.pdf).

## Actual algorithmic order routing

SEBI's February 2025 framework concerns retail algorithmic trading through brokers.
It describes broker accountability, API authentication and traceability, order
identifiers, provider empanelment and registration conditions. It also treats
black-box algo providers differently. Those duties should not be treated as a
paper-simulation licence or as applicable merely because an indicator is computed.

Source: [SEBI February 2025 framework](https://www.sebi.gov.in/sebi_data/attachdocs/feb-2025/1738665456458.pdf).

Upstox's official March 31, 2026 announcement says its April 1 changes are deployed,
including registered static-IP requirements for API orders, the 10-orders-per-second
registration threshold, and changes to market-order handling. This project does
not route orders, implement static-IP registration, or claim to be a registered algo.
Internal `MARKET` paper orders are simulator instructions, not Upstox order types.

Source: [Upstox official rollout announcement](https://community.upstox.com/t/important-update-regulatory-changes-for-api-and-algo-trading-are-now-live/14874).

## Verified market-data integration

| Capability | Implemented endpoint | Boundary |
|---|---|---|
| Historical OHLCV | GET `/v3/historical-candle/{key}/{unit}/{interval}/{to}/{from}` | Validated imported data; 28-day request chunks |
| Observed quote | GET `/v2/market-quote/quotes` | Exact instrument token match; positive price; feed and trade timestamps <=10s old |
| Exchange status | GET `/v2/market/status/NSE` | Requires `NORMAL_OPEN` before live observations |
| Option chain | GET `/v2/option/chain` | Analytical snapshot only, never a fill source |
| Option contracts | GET `/v2/option/contract` | Adapter method; no automatic account changes |
| Instrument file | GET `https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz` | No bearer token sent to asset host |

The V3 historical documentation defines units, intervals, request-range limits,
availability and the candle tuple. The adapter uses supported app timeframes,
normalizes ascending timestamps, and does not invent unavailable history.
[Official historical API](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/).

Full quotes expose last trade time, feed timestamp, last price and instrument token.
LTP is not a promise of liquidity or a fill. The application explicitly models
observed-price fills, not queue priority or exchange execution.
[Official full quotes](https://upstox.com/developer/api-documentation/get-full-market-quote/).

Chain and contract schemas are verified against the official documentation.
The chain's retrieval time is not represented as a synchronized exchange tick.
[Option chain](https://upstox.com/developer/api-documentation/get-pc-option-chain/),
[Option contracts](https://upstox.com/developer/api-documentation/get-option-contracts/),
[Market status](https://upstox.com/developer/api-documentation/get-market-status/).

Instrument identifiers and lot sizes come from registered metadata, not permanent
NIFTY/BANKNIFTY constants. Historical contract metadata must correspond to the
historical contract, not today's instrument master.
[Instrument documentation](https://upstox.com/developer/api-documentation/instruments/).

Other standard API limits are currently documented as 50/second, 500/minute and
2,000/30 minutes. The in-process adapter uses lower ceilings of 40, 450 and 1,900,
stops on 429 and honors a bounded Retry-After cooldown. Other programs using the
same token share the provider's quota. There is no auto retry storm.
[Rate limits](https://upstox.com/developer/api-documentation/rate-limiting/).

Authentication is through a backend environment token obtained using the official
Upstox process. No password, PIN, TOTP collection, account funds, holdings or trading
SDK is implemented. Invalid authorization fails closed; the app does not log tokens.
[Authentication](https://upstox.com/developer/api-documentation/authentication/).

Version 0.3 implements V3 WebSocket/Protobuf market data with the official schema,
reconnect/resubscribe, quote freshness and market-status guards, and optional paper
execution. Protocol and fault tests use synthetic fixtures. Real-account entitlement,
provider acceptance and production legal approval remain unverified. Manual REST
polling remains available. No broker trading endpoint has been added.
[Market Data Feed V3](https://upstox.com/developer/api-documentation/v3/get-market-data-feed/).

## Open legal and operational questions

- No broker account credentials were supplied, so live entitlement, token lifetime
  behavior and real account integration have not been acceptance-tested.
- The exact user's Upstox contract and NSE data licence were not supplied or reviewed.
  This document does not claim redistribution, retention or live-simulation rights.
- No entitlement requiring payment is purchased or required. User-sourced offline
  data works without Upstox. Data availability and optional API pricing can change.
- Public/commercial deployment, advice, research recommendations, performance
  advertising and broker associations need separate review. Local-only deployment
  is an engineering limit, not proof of a regulatory exemption.
- Charge schedules, holidays, corporate actions, lot-size changes and physical
  settlement obligations require maintained authoritative data. Zero fee defaults
  are deliberately labelled uncalibrated, not described as actual trading costs.
- Verify intervening circulars and the exact intended activity again before release.
