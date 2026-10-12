# CSV learning and daily live paper execution — 0.5

## Run the workflow

1. In **Data Management**, choose **Combined NSE bhavcopy CSV (daily)**. Upload
   `nse_bhavcopy_combined.csv` with its original headers and a provenance reference.
   One header row; upload an uncompressed CSV. Stop paper streaming and background
   replay during a large import. Limits: 1 GB and 5 million source rows.
2. The importer maps date, symbol, OHLC and volume automatically. It imports NSE CM
   STK EQ records, explicitly counts other series as excluded, and creates historical
   instruments identified by dataset, ISIN and series. A different combined file
   creates a separate immutable dataset. Duplicate dates for the same ISIN or invalid
   eligible rows reject the entire import. Identical files are rejected.
3. Create/select the paper account whose capital, costs and risk settings will be
   used in learning. Account creation includes editable cost JSON. Set positive
   slippage and at least one fee/tax cost using your own assumptions; zero-cost
   training cannot produce an approved profile. Costs are not a verified tax schedule.
4. In **Activity & Automation → Learn from daily CSV history**, select the dataset
   and one security, set maximum units, then **Study and validate strategies**.
   Learning is per selected security; importing a market-wide file does not launch
   thousands of training jobs automatically. At least 600 daily observations per
   security are required; the most recent 1,500 are used at most.
5. Review the study status and profile evidence. A completed study may have no
   approved settings. There is no fallback that forces a trade.
6. For live use, register a separate SPOT instrument with its current verified lot
   size, tick size and Upstox `NSE_EQ|ISIN` key. Its ISIN must match the learned
   security. Historical research instruments have no provider key: their one-paise
   precision is not assumed to be current exchange metadata.
7. Configure the existing licensed-private Upstox connection and start streaming
   with the LIVE paper account enabled for fills. Select **CSV learned daily —
   validated trend settings**, its passing profile, and the matching live instrument,
   then enable. Account capital, risk and costs must match training; maximum units
   cannot exceed training. The profile supplies ATR parameters automatically.

The server must remain running. Automation still disables on restart. Neither this
mode nor its data refresh sends any broker order or moves real funds.

## What is learned

Nine existing long strategies are tested with ATR multipliers 1.5 and 2.0.
Intraday VWAP and opening-range strategies are excluded from daily learning.
Chronological disjoint windows use 50% training, 25% validation and 25% holdout,
each with its own indicator warm-up and isolated simulated capital. Training and
validation require at least five closed trades, positive closed and marked net P&L,
at most 5% drawdown, and profit factor at least 1.2 when losses exist.

For each observed regime, validation needs three profitable-in-aggregate closed
trades. Two distinct strategy finalists are selected by validation expectancy
before accessing holdout results. Each finalist must independently pass the same
overall holdout gates and the regime sample/profit gates, including a cost stress
test. The stress doubles brokerage and basis-point costs, capped at schema limits;
the GST percentage is unchanged. No previously unselected candidate is promoted
because a finalist failed holdout. Full candidate metrics are retained for review.

Daily ADX, EMA relationships, ATR and breakout conditions classify the current
regime. The best validation-ranked approved strategy for that regime supplies the
entry signal and ATR multiplier. This is deterministic parameter/strategy selection,
not a neural model or code-writing agent. The system does not guarantee profits or
automatically retrain itself indefinitely. New history requires another study and
explicit profile selection; repeated holdout selection can itself cause overfitting.

## Live data and safeguards

Live activation and subsequent stream-bar evaluations schedule a background daily
history refresh. The existing read-only Upstox V3 daily endpoint fetches completed
days over the past 370 calendar days. Data is normalized to daily observations,
checked for duplicates, invalid prices and disagreement with overlapping CSV prices,
then cached separately from immutable training evidence. A successful refresh is
required for the current IST date. Failures are visible in the activity timeline;
the system does not invent candles or use partial current-day OHLC.

Daily observations assume an opening timestamp of 09:15 IST and availability at the
following IST midnight. This is a conservative modeling convention, not verified
publication metadata or a special-session calendar. Raw bhavcopy is not corporate-
action adjusted. A gap above 15% blocks learning or recent daily entry evaluation
pending data review; smaller discontinuities may still affect results.

New entries require at least 200 completed daily bars, daily history no more than
seven calendar days old, and a profile no more than 90 calendar days past its
training cutoff. Old CSV prices alone cannot trigger a live paper fill. Fresh live
stream prices determine sizing and execution, and must remain between the signal's
stop and target. Queued live daily entries expire after 60 seconds without a fill.
There is at most one submitted entry per rule and completed daily signal.

The existing loss cooldown, strategy quarantine, nonincreasing quantity cap and 5%
observed equity drawdown halt remain in force. In this mode cooldown/quarantine count
daily observations, not one-minute bars. Protective exits remain processed from live
prices independently. Stops do not guarantee a maximum loss across gaps/outages.

## Replay and limitations

The imported dataset works with daily historical replay. To forward-test a learned
profile within that dataset, choose an earlier **Study through** date and leave
later dates untouched. Profile use at or before its holdout cutoff is blocked.
Using all available rows for learning leaves no later replay period in that file.
Live and replay balances stay separate; only validated settings are reused.

Daily research cannot establish intraday profitability. Live tick fills and historical
candle-open fills differ; current live lot/tick metadata can also change rounding.
Positive historical results, small samples, trend matching and cost stress do not
prove future profitability. Waiting, rejected entries and losses are valid outcomes.

Two user-provided January 2024 samples were imported into disposable test databases:
1 January: 1,818 EQ rows, 865 excluded; 2 January: 1,815 EQ rows, 843 excluded.
The full three-year combined CSV was not available for end-to-end validation, and no
live Upstox credentials were used. No uploaded price data is bundled in the build.

API references checked during implementation:
- https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/
- https://upstox.com/developer/api-documentation/instruments/
