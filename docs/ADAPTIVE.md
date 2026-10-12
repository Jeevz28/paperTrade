# Adaptive paper execution — 0.4

In **Activity & Automation**, select a paper account and a SPOT, CALL or PUT
instrument, choose **Adaptive — select strategies automatically**, set the maximum
units and ATR stop multiplier, then enable. In replay, start background replay;
in live mode, first start the licensed Upstox stream with that account enabled for
paper fills. The server must stay running. Startup disables automation, so inspect
the account and explicitly re-enable after restarting.

The system selects among the eleven existing long strategies. It does not invent
strategies, choose contracts, execute baskets automatically, or send broker orders.
Once enabled, qualifying signals queue paper entries without per-trade approval.
Stops and targets remain active for existing positions even when automation stops.

## Selection

- Requires 200 completed bars; live bars are observed one-minute bars from the
  current uninterrupted stream session. A reconnect or gap needs warm-up again.
- Every 20 observed evaluations, when the account is flat and has no pending
  orders, simulate each candidate on the latest 300 or fewer visible bars.
- Simulations use existing account risk settings, costs, ATR multiplier and the
  current quantity cap. Entry occurs at a subsequent candle open, never at the
  price of an unfinished signal candle. Protective checks use candle opens.
- A candidate needs at least three closed trades, positive closed-trade net P&L,
  positive marked net P&L, at most 5% drawdown and profit factor at least 1.2
  (or no losing trades). Quarantined candidates cannot qualify.
- Rank qualified candidates by marked net P&L minus drawdown expressed in paise.
  Ties use strategy name for deterministic results. The selected strategy still
  needs a current entry signal. No qualifying candidate means no entry.

These are historical heuristics, not an out-of-sample profitability guarantee.
Three trades are a low minimum, not statistical validation. Selection can overfit;
costs default to zero until configured. Rolling simulations reset their virtual
capital to the account's initial capital for comparison; actual orders use current
cash and equity. Open simulation positions are marked rather than force-closed.
Live tick fills can differ from historical candle-open simulations. Candidate
assessment runs locally and may briefly delay processing; stale quotes remain
ineligible for execution.

## Loss response

Only closed trades originating from this account's automated entries trigger the
loss response; manually labelled trades cannot impersonate automatic entries.
Every losing closed trade causes:

1. A pause for 10 subsequent observed completed-bar evaluations.
2. Exclusion of its strategy for 100 evaluations, then eligibility for reassessment.
3. A nonincreasing quantity cap, reduced toward half the losing quantity and rounded
   down to whole lots, with a minimum of one lot when affordable. Normal risk and
   cash checks can still reduce size to zero. A one-lot trade cannot be halved.
4. Reassessment after cooldown. An alternative must pass the same evidence checks;
   a loss never forces entry into another strategy.

A 5% drawdown from the account equity high-water mark observed by the adaptive
engine permanently disables adaptive entries for that account and cancels pending
automatic entries. This does not force-liquidate positions. Account risk limits
remain independent and may block orders sooner. Drawdown is checked on completed
bar evaluations, so this is not a guaranteed maximum loss or a tick-level exit.

State is persisted by account rule. Stop/start, application restarts, configuration
changes and increases to requested maximum units do not reset cooldowns, the
reduced cap or the drawdown halt. Paused periods do not consume cooldown bars.
Changing instrument or signal settings invalidates rankings; account-level loss
history remains. Current positions retain their original strategy attribution.

The monitor shows selected strategy, reason, warm-up, cooldown, drawdown, quantity
cap, last loss and candidate evidence. Timeline records selection changes; Decisions
records each evaluation. Rankings are the latest completed assessment, not a
real-time forecast. Tests use explicitly synthetic data; no live Upstox session
or strategy profitability was verified for this release.
