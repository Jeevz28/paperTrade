"""Synthetic software tests; rankings here are not market performance claims."""

import json
import pytest
from backend.app import adaptive
from backend.app.automation import Automation
from backend.app.core import Policy
from backend.app.data import import_data
from backend.app.paper import PaperBroker
from backend.app.strategies import REGISTRY, Signal
from conftest import test_csv as fixture_csv


def setup(db, instrument):
    prices = [100] * 201 + [99] * 49
    ds = import_data(
        db, fixture_csv(prices), "ADAPTIVE_TEST.csv", "5m", "SYNTHETIC TEST", Policy()
    )["id"]
    broker = PaperBroker(db)
    aid = broker.create_account({"name": "ADAPTIVE TEST ONLY", "dataset_id": ds})
    auto = Automation(db)
    auto.configure(aid, instrument, "adaptive", 4, {"atr_stop": 1.5})
    return broker, aid, auto


def state(db):
    return json.loads(db.rows("SELECT payload FROM adaptive_state")[0]["payload"])


def candidates(monkeypatch):
    class Always:
        def __init__(self, name):
            self.name = name

        def signal(self, frame, parameters):
            price = float(frame.iloc[-1]["close"])
            return Signal(
                self.name,
                "LONG",
                price,
                price - 0.2,
                price + 0.4,
                "SYNTHETIC TEST",
                "TEST",
                str(frame.iloc[-1]["timestamp"]),
            )

    for name in ("test_alpha", "test_beta"):
        monkeypatch.setitem(REGISTRY, name, Always(name))

    def ranking(rows, instrument, account, parameters, excluded):
        return [
            {"strategy": name, "eligible": name not in excluded, "score": 1}
            for name in ("test_alpha", "test_beta")
        ]

    monkeypatch.setattr(adaptive, "rank", ranking)


def test_adaptive_loss_pause_switch_and_nonincreasing_size(db, instrument, monkeypatch):
    candidates(monkeypatch)
    b, aid, auto = setup(db, instrument)
    for _ in range(200):
        b.advance_replay(aid)
    auto.evaluate(aid)
    auto.evaluate(aid)
    orders = db.rows("SELECT * FROM paper_orders")
    assert len(orders) == 1 and orders[0]["strategy"] == "test_alpha"
    assert orders[0]["status"] == "PENDING" and orders[0]["quantity"] == 4
    b.advance_replay(aid)
    auto.evaluate(aid)
    assert state(db)["status"] == "EXPOSURE_OR_PENDING_ORDER"
    b.advance_replay(aid)
    auto.evaluate(aid)
    assert state(db)["status"] == "LOSS_COOLDOWN"
    assert state(db)["last_loss"]["net_pnl_paise"] == -400
    assert state(db)["quantity_cap"] == 2
    # Disable/re-enable with a larger requested cap cannot erase this loss.
    auto.stop(aid)
    auto.configure(aid, instrument, "adaptive", 10, {"atr_stop": 1.5})
    for _ in range(9):
        b.advance_replay(aid)
        auto.evaluate(aid)
        assert not db.rows("SELECT * FROM paper_orders WHERE status='PENDING'")
    b.advance_replay(aid)
    auto.evaluate(aid)
    pending = db.rows("SELECT * FROM paper_orders WHERE status='PENDING'")
    assert len(pending) == 1
    assert pending[0]["strategy"] == "test_beta" and pending[0]["quantity"] == 2
    assert state(db)["selected"] == "test_beta"
    assert b.reconcile(aid)["ok"]


def test_warmup_and_replay_visibility(db, instrument, monkeypatch):
    b, aid, auto = setup(db, instrument)
    observed = []

    def ranking(rows, inst, account, params, excluded):
        assert all(row["available_at"] <= account["clock"] for row in rows)
        observed.append(len(rows))
        return []

    monkeypatch.setattr(adaptive, "rank", ranking)
    for _ in range(199):
        b.advance_replay(aid)
    auto.evaluate(aid)
    assert state(db)["status"] == "WARMUP" and not observed
    b.advance_replay(aid)
    auto.evaluate(aid)
    assert observed == [200]
    assert state(db)["status"] == "NO_QUALIFYING_STRATEGY"
    assert not db.rows("SELECT * FROM paper_orders")
    auto.evaluate(aid)
    assert observed == [200]


def test_drawdown_halt_survives_reconfiguration_and_recovery(db, instrument):
    b, aid, auto = setup(db, instrument)
    rows = db.rows("SELECT * FROM candles ORDER BY timestamp")[:200]
    rule = db.rows("SELECT * FROM automation_rules")[0]
    account = b.snapshot(aid)
    account["equity_paise"] = 94000
    selected, s = adaptive.choose(db, rule, account, rows, False)
    assert selected is None and s["status"] == "ADAPTIVE_HALTED"
    auto.configure(aid, instrument, "adaptive", 4, {"atr_stop": 1.5})
    account["equity_paise"] = 100000
    selected, s = adaptive.choose(db, rule, account, rows, False)
    assert selected is None and s["halted"]


@pytest.mark.parametrize(
    "trades,pnl,drawdown,factor,excluded,expected",
    [
        (2, 100, 1, 2, set(), False),
        (3, -100, 1, 0.8, set(), False),
        (3, 100, 6, 2, set(), False),
        (3, 100, 1, 1.1, set(), False),
        (3, 100, 1, 2, {"test_candidate"}, False),
        (3, 100, 1, 2, set(), True),
    ],
)
def test_candidate_evidence_filters(
    monkeypatch, trades, pnl, drawdown, factor, excluded, expected
):
    monkeypatch.setattr(adaptive, "REGISTRY", {"test_candidate": object()})
    monkeypatch.setattr(
        adaptive,
        "run",
        lambda *args: {
            "metrics": {
                "total_trades": trades,
                "closed_trade_net_pnl_paise": pnl,
                "net_pnl_paise": pnl,
                "profit_factor": factor,
                "max_drawdown_pct": drawdown,
            }
        },
    )
    ranked = adaptive.rank(
        [],
        {},
        {"initial_capital": 100000, "risk_json": "{}", "cost_json": "{}"},
        {},
        excluded,
    )
    assert ranked[0]["eligible"] is expected


def test_real_rank_is_causal_bounded_and_obeys_quantity_cap(db, instrument):
    b, aid, auto = setup(db, instrument)
    rows = db.rows("SELECT * FROM candles ORDER BY timestamp")[:200]
    inst = db.rows("SELECT * FROM instruments WHERE id=?", (instrument,))[0]
    account = b.snapshot(aid)
    first = adaptive.rank(
        rows, inst, account, {"atr_stop": 1.5, "max_quantity": 1}, set()
    )
    second = adaptive.rank(
        rows, inst, account, {"atr_stop": 1.5, "max_quantity": 1}, set()
    )
    assert first == second and len(first) == len(REGISTRY)
    assert not any(r["eligible"] for r in first)
    assert not db.rows("SELECT * FROM paper_orders")
