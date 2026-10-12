import uuid
import pytest
from backend.app.paper import PaperBroker
from backend.app.data import create_instrument, import_data, candles
from backend.app.core import DomainError, Policy
from backend.app.backtesting import walk_forward
from backend.app.schemas import RiskConfig, CostConfig
from conftest import test_csv as fixture_csv


def test_quantity_corruption_halts(db, instrument, dataset):
    b = PaperBroker(db)
    a = b.create_account({"name": "TEST", "dataset_id": dataset})
    b.advance_replay(a)
    b.place_paper_order(
        a,
        {
            "instrument_id": instrument,
            "side": "BUY",
            "quantity": 1,
            "stop_loss": 98,
            "request_key": str(uuid.uuid4()),
        },
    )
    b.advance_replay(a)
    with db.connect(write=True) as c:
        c.execute("UPDATE paper_positions SET quantity=2 WHERE account_id=?", (a,))
    assert not b.reconcile(a)["ok"]


def test_option_metadata_required(db):
    with pytest.raises(DomainError):
        create_instrument(
            db, {"symbol": "TEST_CALL", "kind": "CALL", "metadata_source": "TEST ONLY"}
        )


def test_walk_forward_disjoint_windows(db, instrument):
    d = import_data(
        db, fixture_csv(count=200), "TEST_DATA.csv", "5m", "TEST DATA ONLY", Policy()
    )["id"]
    rows = candles(db, instrument, dataset_id=d)
    inst = db.rows("SELECT * FROM instruments")[0]
    result = walk_forward(
        rows,
        inst,
        "breakout",
        100000,
        RiskConfig().model_dump(),
        CostConfig().model_dump(),
        60,
        60,
        60,
    )
    assert len(result["windows"]) == 1
    w = result["windows"][0]
    assert w["train_start"] < w["validation_start"] < w["test_start"] < w["test_end"]


def test_protective_exit_and_no_refunding(db, instrument, dataset):
    b = PaperBroker(db)
    a = b.create_account({"name": "TEST", "dataset_id": dataset})
    b.advance_replay(a)
    b.place_paper_order(
        a,
        {
            "instrument_id": instrument,
            "side": "BUY",
            "quantity": 1,
            "stop_loss": 99,
            "request_key": str(uuid.uuid4()),
        },
    )
    b.advance_replay(a)
    b.advance_replay(a)
    s = b.snapshot(a)
    assert (
        not s["positions"]
        and s["cash_balance"] == 99900
        and s["initial_capital"] == 100000
    )
    assert len(db.rows("SELECT * FROM alerts")) == 1


def test_unavailable_price_is_not_entry_price_fallback(db, instrument, dataset):
    b = PaperBroker(db)
    a = b.create_account({"name": "TEST", "mode": "LIVE"})
    with db.connect(write=True) as c:
        c.execute(
            "INSERT INTO paper_positions(account_id,instrument_id,quantity,cost_basis,entry_price,entry_fees,stop_paise,high_water,opened_at,strategy) VALUES(?,?,1,10000,10000,0,9900,10000,'2024-01-01T00:00:00+00:00','TEST')",
            (a, instrument),
        )
    s = b.snapshot(a)
    assert s["equity_paise"] is None and s["positions"][0]["mark_paise"] is None
