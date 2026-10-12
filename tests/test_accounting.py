import sqlite3
import uuid
import pytest
from backend.app.paper import PaperBroker
from backend.app.core import DomainError


def order(b, a, i, side="BUY", qty=1, **kw):
    return b.place_paper_order(
        a,
        {
            "instrument_id": i,
            "side": side,
            "quantity": qty,
            "stop_loss": 98 if side == "BUY" else None,
            "request_key": str(uuid.uuid4()),
            **kw,
        },
    )


def test_closed_account_and_partial_exit(db, instrument, dataset):
    b = PaperBroker(db)
    a = b.create_account({"name": "TEST ONLY", "dataset_id": dataset})
    assert b.snapshot(a)["cash_balance"] == 100000
    b.advance_replay(a)
    order(b, a, instrument, qty=2)
    b.advance_replay(a)
    assert b.snapshot(a)["cash_balance"] == 80000
    order(b, a, instrument, "SELL")
    b.advance_replay(a)
    s = b.snapshot(a)
    assert s["realized_pnl"] == -100 and s["equity_paise"] == 99800
    order(b, a, instrument, "SELL")
    b.advance_replay(a)
    s = b.snapshot(a)
    assert (
        s["initial_capital"] == 100000
        and s["cash_balance"] == 99900
        and not s["positions"]
    )
    assert b.reconcile(a)["ok"]
    assert len(db.rows("SELECT * FROM journal")) == 2


def test_capital_and_ledger_immutable(db, dataset):
    b = PaperBroker(db)
    a = b.create_account({"name": "TEST", "dataset_id": dataset})
    for sql in (
        "UPDATE paper_accounts SET initial_capital=200000",
        "UPDATE account_ledger SET balance_after=200000",
        "DELETE FROM account_ledger",
    ):
        with pytest.raises(sqlite3.IntegrityError), db.connect(write=True) as c:
            c.execute(sql)
    assert b.snapshot(a)["cash_balance"] == 100000


def test_reconcile_halts_without_repair(db, dataset):
    b = PaperBroker(db)
    a = b.create_account({"name": "TEST", "dataset_id": dataset})
    with db.connect(write=True) as c:
        c.execute("UPDATE paper_accounts SET cash_balance=200000 WHERE id=?", (a,))
    assert not b.reconcile(a)["ok"]
    s = b.snapshot(a)
    assert s["halted"] and s["cash_balance"] == 200000


def test_unaffordable_and_risk_rejected(db, instrument, dataset):
    b = PaperBroker(db)
    a = b.create_account({"name": "TEST", "dataset_id": dataset})
    b.advance_replay(a)
    order(b, a, instrument, qty=20)
    b.advance_replay(a)
    assert db.rows("SELECT status FROM paper_orders")[0]["status"] == "REJECTED"
    assert b.snapshot(a)["cash_balance"] == 100000


def test_idempotency_and_cancellation(db, instrument, dataset):
    b = PaperBroker(db)
    a = b.create_account({"name": "TEST", "dataset_id": dataset})
    b.advance_replay(a)
    payload = {
        "instrument_id": instrument,
        "side": "BUY",
        "quantity": 1,
        "stop_loss": 98,
        "request_key": "same-key-123",
    }
    first = b.place_paper_order(a, payload)
    second = b.place_paper_order(a, payload)
    assert first["id"] == second["id"]
    b.cancel_paper_order(a, first["id"])
    b.advance_replay(a)
    assert not b.snapshot(a)["positions"]


def test_costs_reconcile(db, instrument, dataset):
    b = PaperBroker(db)
    a = b.create_account(
        {
            "name": "TEST",
            "dataset_id": dataset,
            "costs": {"brokerage_paise": 50, "slippage_bps": 1},
        }
    )
    b.advance_replay(a)
    order(b, a, instrument)
    b.advance_replay(a)
    order(b, a, instrument, "SELL")
    b.advance_replay(a)
    s = b.snapshot(a)
    assert (
        s["costs"] > 100
        and s["equity_paise"] == s["initial_capital"] + s["realized_pnl"] - s["costs"]
    )
    assert b.reconcile(a)["ok"]


def test_no_same_candle_fill(db, instrument, dataset):
    b = PaperBroker(db)
    a = b.create_account({"name": "TEST", "dataset_id": dataset})
    b.advance_replay(a)
    order(b, a, instrument)
    assert b.snapshot(a)["positions"] == []
    b.advance_replay(a)
    assert len(b.snapshot(a)["positions"]) == 1


def test_naked_short_rejected(db, instrument, dataset):
    b = PaperBroker(db)
    a = b.create_account({"name": "TEST", "dataset_id": dataset})
    b.advance_replay(a)
    order(b, a, instrument, "SELL")
    b.advance_replay(a)
    assert db.rows("SELECT status FROM paper_orders")[0]["status"] == "REJECTED"


def test_pending_limit_modify(db, instrument, dataset):
    b = PaperBroker(db)
    a = b.create_account({"name": "TEST", "dataset_id": dataset})
    b.advance_replay(a)
    o = order(b, a, instrument, order_type="LIMIT", limit_price=99)
    b.advance_replay(a)
    assert not b.snapshot(a)["positions"]
    b.modify_paper_order(a, o["id"], 100)
    b.advance_replay(a)
    assert b.snapshot(a)["positions"][0]["entry_price"] == 9900
