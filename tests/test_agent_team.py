"""Synthetic local SQLite fixtures only; no app startup, providers or services."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.agent_team import AgentTeam, ROLES, _choices, _frame, _proxy
from backend.app.indicators import compute
from backend.app.paper import PaperBroker, append_event


def bars(count=120, descending=False):
    start = datetime(2024, 1, 2, 4, tzinfo=timezone.utc)
    sign = -1 if descending else 1
    return [dict(timestamp=(start + timedelta(minutes=i)).isoformat(),
                 open=20000 + sign * i * 20, high=20030 + sign * i * 20,
                 low=19990 + sign * i * 20, close=20010 + sign * i * 20,
                 volume=0) for i in range(count)]


@pytest.fixture
def team(db):
    return AgentTeam(db)


@pytest.fixture
def account(db):
    return PaperBroker(db).create_account(dict(name="AGENT SYNTHETIC TEST", mode="LIVE", initial_capital=100000))


def closed_trade(team, account, instrument, *, gross=-100, fees=20,
                 context=True, ledger=True, strategy="trend", regime="TREND"):
    """Persist the real schema's basket/event/ledger linkage, without execution."""
    db = team.db
    opened, closed = "2024-01-02T05:00:00+00:00", "2024-01-02T05:05:00+00:00"
    with db.connect(write=True) as c:
        bid = c.execute("""INSERT INTO baskets(account_id,strategy,status,legs_json,stop_paise,created_at,opened_at,request_key)
                           VALUES(?,'future','CLOSED',?,950,?,?,?)""",
                        (account, json.dumps([dict(instrument_id=instrument, quantity=1)]), opened, opened,
                         str(c.execute("SELECT COUNT(*) FROM baskets").fetchone()[0]))).lastrowid
        c.execute("INSERT INTO fno_tickets(basket_id,signal,signal_at,stop_paise,target_paise,expires_at) VALUES(?,?,?,950,1200,?)",
                  (bid, strategy, opened, closed))
        c.execute("INSERT INTO basket_trades(basket_id,timestamp,gross_pnl,fees) VALUES(?,?,?,?)", (bid, closed, gross, fees))
        if ledger:
            for kind, at, pnl, fee in [("BASKET_OPEN", opened, 0, fees // 2), ("BASKET_CLOSE", closed, gross, fees - fees // 2)]:
                eid = c.execute("INSERT INTO basket_events(basket_id,timestamp,kind,payload,digest) VALUES(?,?,?,'{}','fixture')", (bid, at, kind)).lastrowid
                append_event(c, account, at, kind, 10000000, 10000000 + pnl - fee,
                             f"agent-test:{bid}:{kind}", fees=fee, realized=pnl,
                             source=f"basket_event:{eid}:fixture")
        for at, price in [("2024-01-02T04:59:00+00:00", 1), (opened, 1000),
                          ("2024-01-02T05:02:00+00:00", 930), (closed, 900),
                          ("2024-01-02T05:06:00+00:00", 2000)]:
            c.execute("INSERT INTO quotes(instrument_id,timestamp,received_at,price,source,payload) VALUES(?,?,?,?,'SYNTHETIC_TEST','{}')", (instrument, at, at, price))
    if context:
        team.record_decision(account, bid, dict(name=strategy, regime=regime, direction=1,
                             atr=40, stop_factor=1.5, target_rr=2,
                             features=dict(close_paise=1000, timestamp=opened, z_score=-1.8)))
    return bid


def test_schema_restart_and_warmup_are_persisted(team, account):
    assert team.evaluate(account, bars(59)) is None
    restarted = AgentTeam(team.db)
    state = restarted.status(account)
    assert {m["role"] for m in state["messages"]} == set(ROLES)
    assert all(m["status"] == "INSUFFICIENT_DATA" for m in state["messages"])
    assert state["candidates"] == []
    json.dumps(state, allow_nan=False)


def test_public_message_appends_execution_and_risk_decisions(team, account):
    team.message(account, "execution", "QUEUED", "Paper order queued", {"basket_id": 7})
    team.message(account, "execution", "FILLED", "Observed paper fill")
    team.message(account, "risk", "BLOCKED", "Full lot exceeds risk budget", {"ratio": float("nan")})
    messages = AgentTeam(team.db).status(account)["messages"]
    assert [m["status"] for m in messages] == ["BLOCKED", "FILLED", "QUEUED"]
    assert messages[0]["payload"] == {"ratio": None}
    assert messages[1]["payload"] == {}
    assert messages[2]["payload"] == {"basket_id": 7}
    json.dumps(messages, allow_nan=False)


def test_execution_messages_do_not_hide_latest_candidates(team, account):
    team.evaluate(account, bars(60))
    expected = team.status(account)["candidates"]
    for _ in range(61):
        team.message(account, "execution", "MONITORING", "Paper protection active")
    assert team.status(account)["candidates"] == expected


@pytest.mark.parametrize("descending,direction", [(False, 1), (True, -1)])
def test_signal_units_direction_and_sparse_proxy(team, account, descending, direction):
    signal = team.evaluate(account, bars(60, descending))
    assert signal["name"] == "trend" and signal["direction"] == direction
    assert signal["atr"] == 40  # paise, not rupees and not multiplied by 100
    assert signal["features"]["proxy"]["evidence"] == "insufficient_data"
    assert signal["stop_factor"] >= 1.5 and signal["target_rr"] == 2
    assert set(signal) == {"name", "direction", "atr", "regime", "stop_factor", "target_rr", "reason", "features"}
    assert all(m["payload"] is not None for m in team.status(account)["messages"])


def test_future_and_incomplete_bars_cannot_change_signal(team, account):
    rows = bars()
    expected = team.evaluate(account, rows)
    invalid = dict(rows[-1], timestamp="2099-01-01T00:00:00+00:00", close=1)
    pending = dict(rows[-1], timestamp="2024-01-03T00:00:00+00:00", completed=False, close=1)
    unavailable = dict(rows[-1], timestamp="2024-01-04T00:00:00+00:00", available_at="2099-01-01T00:00:00+00:00", close=1)
    assert team.evaluate(account, rows + [pending, unavailable, invalid]) == expected
    assert rows[-1]["close"] > 20000  # caller data wasn't rewritten


def test_indicator_choices_and_proxy_are_causal():
    rows = bars(150)
    full = compute(_frame(rows))
    prefix = compute(_frame(rows[:100]))
    assert _choices(full, 99) == _choices(prefix, 99)
    baseline = _proxy(prefix, "trend", "TREND")
    changed = rows[:100]
    changed[-1] = dict(changed[-1], open=999900, low=999800, close=1000000, high=1000100)
    assert _proxy(compute(_frame(changed)), "trend", "TREND") == baseline
    assert baseline["samples"] >= 3
    assert baseline["evaluated_before"] == rows[99]["timestamp"]


@pytest.mark.parametrize("mutation", ["duplicate", "reversed", "rupees", "bounds"])
def test_invalid_input_fails_closed(team, account, mutation):
    rows = bars(60)
    if mutation == "duplicate":
        rows.append(rows[-1])
    elif mutation == "reversed":
        rows.reverse()
    elif mutation == "rupees":
        rows[-1]["close"] = 21.90
    else:
        rows[-1]["low"] = rows[-1]["high"] + 1
    assert team.evaluate(account, rows) is None
    assert team.status(account)["messages"][0]["status"] == "INVALID_DATA"


def test_exclusion_and_strategy_specific_stops(team, account):
    rows = bars()
    rows[-1].update(close=22500, high=22510)
    team.evaluate(account, rows)
    candidates = {s["name"]: s for s in team.status(account)["candidates"]}
    assert set(candidates) == {"trend", "breakout"}
    assert candidates["trend"]["stop_factor"] != candidates["breakout"]["stop_factor"]
    assert candidates["trend"]["target_rr"] != candidates["breakout"]["target_rr"]
    assert team.evaluate(account, rows, excluded=("trend",))["name"] == "breakout"
    assert team.evaluate(account, rows, excluded=("trend", "breakout")) is None


def test_range_reversal_uses_existing_indicators():
    # Hand-set indicator observations isolate this rule from indicator warm-up.
    f = compute(_frame(bars(60)))
    f.loc[59, ["adx", "z_score", "close", "low", "high"]] = [15, -1.8, 21200, 21170, 21210]
    assert _choices(f, 59)[0]["name"] == "range_reversal"
    assert _choices(f, 59)[0]["target_rr"] == 1.25


def test_loss_review_idempotent_factual_and_account_scoped(team, account, instrument):
    bid = closed_trade(team, account, instrument)
    before = team.db.rows("SELECT * FROM paper_accounts")
    review = team.review_closed(account)[0]
    assert review["basket_id"] == bid
    assert review["classification"] == "negative_gross_with_fees"
    assert review["evidence_ok"] is True
    obs = review["observations"][0]
    assert (obs["samples"], obs["min_paise"], obs["max_paise"]) == (3, 900, 1000)
    assert obs["observed_stop_cross"] and not obs["observed_target_cross"]
    assert review["loss_observations"] == ["managed_stop_cross_observed", "adverse_sampled_contract_move"]
    assert review["decision_context"]["signal"]["features"]["z_score"] == -1.8
    assert team.review_closed(account) == []
    assert AgentTeam(team.db).review_closed(account) == []
    assert len(team.status(account)["reviews"]) == 1
    assert team.status(account + 1) == dict(messages=[], reviews=[], candidates=[])
    assert team.db.rows("SELECT * FROM paper_accounts") == before
    json.dumps(team.status(account), allow_nan=False)


def test_fees_and_missing_evidence_are_explicit(team, account, instrument):
    closed_trade(team, account, instrument, gross=10, fees=20, context=False, ledger=False)
    review = team.review_closed(account)[0]
    assert review["classification"] == "fees_exceed_nonnegative_gross"
    assert review["evidence_status"] == "insufficient_data"
    assert review["regime"] == "UNKNOWN" and not review["evidence_ok"]


def test_veto_requires_five_verified_trades_and_same_regime(team, account, instrument):
    closed_trade(team, account, instrument, ledger=False)
    for _ in range(4):
        closed_trade(team, account, instrument)
    assert team.evaluate(account, bars())["name"] == "trend"
    closed_trade(team, account, instrument, regime="RANGE")
    assert team.evaluate(account, bars())["name"] == "trend"
    closed_trade(team, account, instrument)
    assert team.evaluate(account, bars()) is None
    candidate = team.status(account)["candidates"][0]
    assert candidate["exclusion"] == "negative_real_trade_evidence"
    assert candidate["review_evidence"]["trades"] == 5
    assert team.evaluate(account + 1, bars())["name"] == "trend"
    # Re-evaluating an earlier completed prefix cannot use subsequent closes.
    assert team.evaluate(account, bars(60))["name"] == "trend"


def test_context_freezes_first_decision_and_rejects_wrong_account(team, account, instrument):
    bid = closed_trade(team, account, instrument)
    original = team.db.rows("SELECT payload FROM decision_context")[0]["payload"]
    signal = team.evaluate(account, bars())
    team.record_decision(account, bid, signal, bars())
    assert team.db.rows("SELECT payload FROM decision_context")[0]["payload"] == original
    payload = json.loads(original)
    assert payload["expected"]["stop_paise"] == 940
    assert payload["expected"]["target_paise"] == 1120
    assert payload["managed_contract"]["stop_paise"] == 950
    with pytest.raises(ValueError, match="belong"):
        team.record_decision(account + 1, bid, signal)
