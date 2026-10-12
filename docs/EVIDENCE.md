# Derivative replay and evidence (updated for 0.3)

Use **Options / F&O** with a replay account and a dataset containing every leg.
Advance one candle before queuing a basket. Quantities are signed units, not lots.
All legs require observed opens at the same timestamp. Missing legs keep the order
pending; there are no interpolated or model-price fills. One basket can be open per
account, with no concurrent standalone positions. Close requests execute at a later
eligible replay open. Long/short futures, long calls/puts, debit bull-call/bear-put
spreads, fully covered calls and protective puts are supported.

Futures require directional stops and imported margin. Their notional is not
debited from cash; initial collateral is reserved and subtracted from available
cash. Daily settlement transfers variation P&L and resets basis. At observed opens,
expired margin evidence, increased collateral requirements or a maintenance breach
cause conservative closure. Stops cannot guarantee the planned loss through gaps.
This is an evidence-driven model, not a SPAN/exchange portfolio-margin calculator.

## Evidence CSV schemas

Every import requires a source/publication reference. The file's SHA-256 and source
are stored. All timestamps require timezone offsets and are normalized to UTC.
Education-mode data cutoffs apply. Imports are atomic; duplicate records reject the
file. No authentic exchange evidence is bundled.

| Type | Required CSV headers | Units |
|---|---|---|
| margin | instrument_id,available_at,valid_from,valid_until,initial_per_lot,maintenance_per_lot | Margin in INR per registered lot; initial ≥ maintenance > 0 |
| settlement | instrument_id,effective_at,available_at,price,final | Actual contract settlement price in INR; final is 0 or 1 |
| option_history | instrument_id,timestamp,iv,open_interest | IV decimal (0.20 = 20%); OI nonnegative integer |
| calendar | session_date,opens_at,closes_at | ISO date and timezone-aware session timestamps |

`available_at` must reflect when the evidence was actually published. Settlement
publication cannot precede effectiveness. Margin is selected only when published
and valid at the fill timestamp. Imported evidence is not independently certified.
Calendar rows are reference records in this release; they do not gate execution.

**Apply published cash settlements** applies eligible evidence at the replay clock,
idempotently. Final settlement must be on or after the registered expiry date. It
uses the imported CONTRACT value, never a manufactured intrinsic-value estimate.
Daily variation applies only to futures; options require final settlement. Physical
delivery uses the separate delivery evidence/action described in [RUNTIME.md](RUNTIME.md).
Use basket long options for this workflow; the older standalone
long-option engine does not acquire settlement behavior automatically. Keep enough
dataset observations to advance the replay clock through settlement publication.

IV percentile compares the current observed IV against at least 20 earlier
observations; OI change compares consecutive observations. No future rows are exposed
at the replay clock. This is an observation percentile, not necessarily a 252-day
percentile. Sampling frequency belongs to the source.

## Integrity, replay and restore

Database schema 2 upgrades additively. Immutable basket events are linked to the
account ledger by ID and payload digest. Reconciliation reconstructs signed
positions, basis and collateral. Realized basket summaries include prior daily
settlements and all basket fees. Replay snapshots are persisted after each step.

Replay autoplay advances approximately one candle per second while the page is open;
it pauses on errors, account/page changes or end-of-data. The separate Activity &
Automation background replay worker continues across page changes until paused.

Download a SQLite online backup in **Settings**. Restore only into a NEW path:

```bash
python restore.py paperlab-backup.sqlite data/restored.sqlite
PAPERLAB_DB=data/restored.sqlite python run.py
```

The restore command refuses an existing destination. Integrity and schema-version
checks run before restoration. Stop the old app before choosing the restored DB.

Research strata show strategy, regime, IST entry hour, weekday and instrument, with
Wilson win-rate intervals. Serially correlated trades violate the independent-trial
assumption. Missing labels remain `unknown`; no regimes are invented.

## Remaining work

Corporate actions, auction/default penalties, intrabar liquidity/queue models, the
full short-strategy catalog and production provider acceptance testing remain outside
this release. See RUNTIME.md for the scope of the implemented V3 stream, automation,
physical delivery and multi-leg backtesting.
