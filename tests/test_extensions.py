"""Synthetic accounting fixtures only. No fixture is installed as market data."""

import json
import uuid
import pandas as pd
import pytest
from backend.app.core import Policy, DomainError
from backend.app.data import create_instrument, import_data
from backend.app.paper import PaperBroker
from backend.app.derivatives import DerivativeBroker
from backend.app.evidence import import_evidence, option_series
from backend.app.operations import backup_database, restore_copy
from backend.app.database import Database
from backend.app.analytics import stratify


def setup(db, kinds=("FUTURE",), prices=None):
    ids = []
    rows = []
    for index, kind in enumerate(kinds):
        symbol = f"TEST_ONLY_{index}"
        ids.append(
            create_instrument(
                db,
                dict(
                    symbol=symbol,
                    kind=kind,
                    expiry="2024-01-25",
                    strike=100 + index * 10 if kind in ("CALL", "PUT") else None,
                    underlying="TEST_INDEX",
                    metadata_source="SYNTHETIC UNIT TEST",
                    tick_size=0.01,
                ),
            )
        )
        for j, p in enumerate((prices or [[100, 100, 102, 103]])[index]):
            rows.append(
                dict(
                    timestamp=f"2024-01-02T{3+(45+j*5)//60:02}:{(45+j*5)%60:02}:00+00:00",
                    symbol=symbol,
                    open=p,
                    high=p + 1,
                    low=max(0.01, p - 1),
                    close=p,
                    volume=100,
                )
            )
    dataset = import_data(
        db,
        pd.DataFrame(rows).to_csv(index=False).encode(),
        "test.csv",
        "5m",
        "SYNTHETIC UNIT TEST",
        Policy(),
    )["id"]
    broker = PaperBroker(db)
    account = broker.create_account(
        dict(name="TEST ONLY", dataset_id=dataset, initial_capital=10000)
    )
    broker.advance_replay(account)
    return ids, broker, account, DerivativeBroker(db)


def evidence(db, kind, rows):
    return import_evidence(
        db,
        pd.DataFrame(rows).to_csv(index=False).encode(),
        kind,
        "SYNTHETIC UNIT TEST",
        Policy(),
    )


def margin(db, i, available="2024-01-01T00:00:00Z", initial=100):
    evidence(
        db,
        "margin",
        [
            dict(
                instrument_id=i,
                available_at=available,
                valid_from="2024-01-01T00:00:00Z",
                valid_until="2024-01-30T00:00:00Z",
                initial_per_lot=initial,
                maintenance_per_lot=80,
            )
        ],
    )


@pytest.mark.parametrize("quantity", [1, -1])
def test_future_roundtrip_cash_and_collateral(db, quantity):
    ids, b, a, d = setup(db)
    margin(db, ids[0])
    ticket = d.submit(
        a,
        "future",
        [dict(instrument_id=ids[0], quantity=quantity)],
        str(uuid.uuid4()),
        9900 if quantity > 0 else 11000,
    )
    b.advance_replay(a)
    s = b.snapshot(a)
    assert (
        s["cash_balance"] == 1000000
        and s["used_margin_paise"] == 10000
        and s["available_cash_paise"] == 990000
    )
    d.request_close(a, ticket["id"])
    b.advance_replay(a)
    assert b.snapshot(a)["cash_balance"] == 1000000 + quantity * 200
    assert b.reconcile(a)["ok"]


@pytest.mark.parametrize("future_evidence", [False, True])
def test_missing_or_future_margin_rejects_without_debit(db, future_evidence):
    ids, b, a, d = setup(db)
    if future_evidence:
        margin(db, ids[0], "2024-01-03T00:00:00Z")
    d.submit(
        a, "future", [dict(instrument_id=ids[0], quantity=1)], str(uuid.uuid4()), 9900
    )
    b.advance_replay(a)
    assert db.rows("SELECT status FROM baskets")[0]["status"] == "REJECTED"
    assert b.snapshot(a)["cash_balance"] == 1000000


def test_atomic_spread_close_and_tamper_detection(db):
    ids, b, a, d = setup(db, ("CALL", "CALL"), [[10, 10, 12, 13], [5, 5, 6, 7]])
    t = d.submit(
        a,
        "bull_call",
        [
            dict(instrument_id=ids[0], quantity=1),
            dict(instrument_id=ids[1], quantity=-1),
        ],
        str(uuid.uuid4()),
    )
    b.advance_replay(a)
    assert b.snapshot(a)["cash_balance"] == 999500 and b.reconcile(a)["ok"]
    d.request_close(a, t["id"])
    b.advance_replay(a)
    assert b.snapshot(a)["cash_balance"] == 1000100 and b.reconcile(a)["ok"]


def test_margin_position_tamper_halts(db):
    ids, b, a, d = setup(db)
    margin(db, ids[0])
    d.submit(
        a, "future", [dict(instrument_id=ids[0], quantity=1)], str(uuid.uuid4()), 9900
    )
    b.advance_replay(a)
    with db.connect(write=True) as c:
        c.execute("UPDATE basket_positions SET margin=1")
    assert not b.reconcile(a)["ok"] and b.snapshot(a)["halted"]


def test_daily_settlement_resets_basis_and_is_idempotent(db):
    ids, b, a, d = setup(db)
    margin(db, ids[0])
    t = d.submit(
        a, "future", [dict(instrument_id=ids[0], quantity=1)], str(uuid.uuid4()), 9900
    )
    b.advance_replay(a)
    evidence(
        db,
        "settlement",
        [
            dict(
                instrument_id=ids[0],
                effective_at="2024-01-02T03:51:00Z",
                available_at="2024-01-02T03:54:00Z",
                price=101,
                final=0,
            )
        ],
    )
    assert len(d.settle(a)["applied_evidence"]) == 1
    assert d.settle(a)["applied_evidence"] == []
    assert b.snapshot(a)["cash_balance"] == 1000100 and b.reconcile(a)["ok"]
    d.request_close(a, t["id"])
    b.advance_replay(a)
    assert b.snapshot(a)["cash_balance"] == 1000200 and b.reconcile(a)["ok"]
    assert db.rows("SELECT gross_pnl FROM basket_trades")[0]["gross_pnl"] == 200


def test_missing_leg_waits_without_partial_fill(db):
    ids, b, a, d = setup(db, ("CALL", "CALL"), [[10, 10, 12, 13], [5]])
    d.submit(
        a,
        "bull_call",
        [
            dict(instrument_id=ids[0], quantity=1),
            dict(instrument_id=ids[1], quantity=-1),
        ],
        str(uuid.uuid4()),
    )
    b.advance_replay(a)
    assert db.rows("SELECT status FROM baskets")[0]["status"] == "PENDING"
    assert (
        not db.rows("SELECT * FROM basket_positions")
        and b.snapshot(a)["cash_balance"] == 1000000
    )


def test_option_history_causal_and_backup_roundtrip(db, tmp_path):
    ids, b, a, d = setup(db, ("CALL",), [[10, 10, 12, 13]])
    evidence(
        db,
        "option_history",
        [
            dict(
                instrument_id=ids[0],
                timestamp=f"2023-12-{j+1:02}T04:00:00Z",
                iv=0.10 + j / 100,
                open_interest=100 + j,
            )
            for j in range(22)
        ],
    )
    series = option_series(db, ids[0], "2023-12-21T05:00:00+00:00")
    assert (
        len(series["rows"]) == 21
        and series["iv_percentile"] == 100
        and series["oi_change"] == 1
    )
    restored = Database(restore_copy(backup_database(db), tmp_path / "restored.db"))
    assert (
        PaperBroker(restored).reconcile(a)["ok"]
        and len(restored.rows("SELECT * FROM option_history")) == 22
    )
    with pytest.raises(DomainError):
        restore_copy(db.path, restored.path)


def test_strata_ist_and_uncertainty():
    r = stratify(
        [
            dict(
                entry_time="2024-01-02T04:00:00Z",
                exit_time="2024-01-02T05:00:00Z",
                net_pnl=100,
                strategy="test",
            )
        ]
    )
    assert r["groups"]["entry_hour_IST"][0]["group"] == "09:00"
    assert r["overall"]["win_rate_95pct_interval"][0] < 1


@pytest.mark.parametrize("physical", [False, True])
def test_final_zero_option_settlement_or_physical_refusal(db, physical):
    ids, b, a, d = setup(db, ("CALL",), [[10, 10, 12, 13]])
    if physical:
        with db.connect(write=True) as c:
            c.execute(
                "UPDATE instruments SET settlement='PHYSICAL' WHERE id=?", (ids[0],)
            )
    d.submit(
        a, "long_call", [dict(instrument_id=ids[0], quantity=1)], str(uuid.uuid4())
    )
    b.advance_replay(a)
    evidence(
        db,
        "settlement",
        [
            dict(
                instrument_id=ids[0],
                effective_at="2024-01-25T10:00:00Z",
                available_at="2024-01-25T11:00:00Z",
                price=0,
                final=1,
            )
        ],
    )
    assert d.settle(a)["applied_evidence"] == []
    # Advance the synthetic test clock directly to isolate settlement accounting.
    with db.connect(write=True) as c:
        c.execute(
            "UPDATE paper_accounts SET clock='2024-01-25T12:00:00+00:00' WHERE id=?",
            (a,),
        )
    if physical:
        with pytest.raises(DomainError, match="Physical"):
            d.settle(a)
        assert db.rows("SELECT * FROM basket_positions")
    else:
        assert len(d.settle(a)["applied_evidence"]) == 1
        assert not db.rows("SELECT * FROM basket_positions")
        assert b.snapshot(a)["cash_balance"] == 999000
        assert b.snapshot(a)["realized_pnl"] == -1000 and b.reconcile(a)["ok"]


def test_increased_margin_closes_and_records_risk(db):
    ids, b, a, d = setup(db)
    margin(db, ids[0])
    d.submit(
        a, "future", [dict(instrument_id=ids[0], quantity=1)], str(uuid.uuid4()), 9900
    )
    b.advance_replay(a)
    margin(db, ids[0], "2024-01-02T03:54:00Z", 200)
    b.advance_replay(a)
    assert not b.snapshot(a)["basket_positions"] and b.reconcile(a)["ok"]
    assert "increased" in db.rows("SELECT reason FROM risk_events")[0]["reason"]


def test_bad_evidence_atomic_and_v1_migration_preserves_ledger(db, dataset):
    b = PaperBroker(db)
    a = b.create_account(dict(name="TEST", dataset_id=dataset))
    before = db.rows("SELECT * FROM account_ledger")
    with db.connect(write=True) as c:
        c.execute("PRAGMA user_version=1")
    upgraded = Database(db.path)
    assert (
        before == upgraded.rows("SELECT * FROM account_ledger")
        and PaperBroker(upgraded).reconcile(a)["ok"]
    )
    with pytest.raises(DomainError):
        evidence(
            db, "calendar", [dict(session_date="bad", opens_at="bad", closes_at="bad")]
        )
    assert not db.rows("SELECT * FROM session_calendar")
