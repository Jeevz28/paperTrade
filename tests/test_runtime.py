"""All prices and frames here are explicitly synthetic software-test fixtures."""

import json
import threading
import uuid
from datetime import datetime, timedelta
import pandas as pd
import pytest
from google.protobuf.json_format import MessageToDict, ParseDict
from backend.app.core import DomainError, Policy, UTC
from backend.app.data import create_instrument, import_data
from backend.app.paper import PaperBroker
from backend.app.derivatives import DerivativeBroker
from backend.app.delivery import deliver
from backend.app.automation import Automation
from backend.app.streaming import MarketStream, authorized_uri
from backend.app.providers import UpstoxProvider
from backend.app.MarketDataFeed_pb2 import FeedResponse
from backend.app.basket_backtesting import run_baskets
from backend.app.runtime_api import BasketRun
from backend.app.strategies import Signal, REGISTRY
from test_extensions import setup, evidence, margin
from conftest import test_csv as fixture_csv


def physical(db, strategy="long_call", capital=10000, reference=120, strike=100):
    stock = create_instrument(
        db,
        {
            "symbol": "PHYSICAL_TEST_STOCK",
            "metadata_source": "SYNTHETIC TEST",
            "tick_size": 0.01,
        },
    )
    kind = (
        "PUT"
        if strategy in ("protective_put", "long_put")
        else "FUTURE" if strategy == "future" else "CALL"
    )
    derivative = create_instrument(
        db,
        {
            "symbol": "PHYSICAL_TEST_CONTRACT",
            "kind": kind,
            "expiry": "2024-01-25",
            "strike": strike if kind != "FUTURE" else None,
            "underlying": "PHYSICAL_TEST_STOCK",
            "settlement": "PHYSICAL",
            "metadata_source": "SYNTHETIC TEST",
            "tick_size": 0.01,
        },
    )
    rows = []
    for symbol, price in [
        ("PHYSICAL_TEST_STOCK", 100),
        ("PHYSICAL_TEST_CONTRACT", 100 if kind == "FUTURE" else 10),
    ]:
        for t in ["2024-01-02T03:45:00Z", "2024-01-02T03:50:00Z"]:
            rows.append(
                dict(
                    timestamp=t,
                    symbol=symbol,
                    open=price,
                    high=price + 1,
                    low=price - 1,
                    close=price,
                    volume=100,
                )
            )
    rows.extend(
        [
            dict(
                timestamp=t,
                symbol="PHYSICAL_TEST_STOCK",
                open=reference,
                high=reference + 1,
                low=reference - 1,
                close=reference,
                volume=100,
            )
            for t in ["2024-01-26T04:00:00Z", "2024-01-26T04:05:00Z"]
        ]
    )
    ds = import_data(
        db,
        pd.DataFrame(rows).to_csv(index=False).encode(),
        "physical-test.csv",
        "5m",
        "SYNTHETIC TEST",
        Policy(),
    )["id"]
    b = PaperBroker(db)
    a = b.create_account(
        {"name": "SYNTHETIC TEST", "dataset_id": ds, "initial_capital": capital}
    )
    d = DerivativeBroker(db)
    b.advance_replay(a)
    legs = [
        {
            "instrument_id": derivative,
            "quantity": -1 if strategy == "covered_call" else 1,
        }
    ]
    if strategy in ("covered_call", "protective_put"):
        legs.insert(0, {"instrument_id": stock, "quantity": 1})
    if strategy == "future":
        margin(db, derivative)
    ticket = d.submit(
        a, strategy, legs, str(uuid.uuid4()), 9900 if strategy == "future" else None
    )
    b.advance_replay(a)
    assert db.rows("SELECT status FROM baskets")[0]["status"] == "OPEN"
    evidence(
        db,
        "delivery",
        [
            dict(
                instrument_id=derivative,
                underlying_id=stock,
                effective_at="2024-01-25T10:00:00Z",
                available_at="2024-01-25T11:00:00Z",
                delivery_at="2024-01-26T04:00:00Z",
                reference_price=reference,
                fees=0,
            )
        ],
    )
    return stock, derivative, b, a, d, ticket


@pytest.mark.parametrize(
    "strategy,ref,expected_cash,stock_qty,pnl",
    [
        ("long_call", 120, 989000, 1, 1000),
        ("covered_call", 120, 1001000, 0, 1000),
        ("protective_put", 80, 999000, 0, -1000),
        ("future", 120, 990000, 1, 2000),
        ("long_call", 90, 999000, 0, -1000),
    ],
)
def test_delivery_accounting_and_reconciliation(
    db, strategy, ref, expected_cash, stock_qty, pnl
):
    stock, _, b, a, d, ticket = physical(db, strategy, reference=ref)
    assert deliver(db, a)["delivered"] == []
    b.advance_replay(a)
    assert deliver(db, a)["delivered"] == [ticket["id"]]
    s = b.snapshot(a)
    assert s["cash_balance"] == expected_cash and s["realized_pnl"] == pnl
    assert sum(p["signed_quantity"] for p in s["basket_positions"]) == stock_qty
    assert s["reconciliation"]["ok"] and deliver(db, a)["delivered"] == []
    if stock_qty:
        d.request_close(a, ticket["id"])
        b.advance_replay(a)
        assert not b.snapshot(a)["basket_positions"] and b.reconcile(a)["ok"]
        assert b.snapshot(a)["cash_balance"] == 1000000 + pnl


def test_delivery_insufficient_cash_never_creates_funds(db):
    stock, _, b, a, d, t = physical(db, capital=1000, reference=2100, strike=2000)
    b.advance_replay(a)
    before = db.rows("SELECT * FROM account_ledger")
    result = deliver(db, a)
    assert result["blocked"] and "cash" in result["blocked"][0]["reason"]
    assert not result["delivered"] and db.rows("SELECT * FROM account_ledger") == before
    assert b.reconcile(a)["ok"] and b.snapshot(a)["cash_balance"] == 99000


def test_delivery_insufficient_shares_blocks_without_short_stock(db):
    stock, _, b, a, d, t = physical(db, strategy="long_put", reference=80)
    b.advance_replay(a)
    before = db.rows("SELECT * FROM account_ledger")
    result = deliver(db, a)
    assert result["blocked"] and "shares" in result["blocked"][0]["reason"]
    assert db.rows("SELECT * FROM account_ledger") == before and b.reconcile(a)["ok"]


def test_physical_evidence_validation(db):
    stock, contract, b, a, d, t = physical(db)
    with pytest.raises(DomainError):
        evidence(
            db,
            "delivery",
            [
                dict(
                    instrument_id=contract,
                    underlying_id=contract,
                    effective_at="2024-01-25T10:00:00Z",
                    available_at="2024-01-25T11:00:00Z",
                    delivery_at="2024-01-26T04:00:00Z",
                    reference_price=120,
                    fees=0,
                )
            ],
        )
    assert len(db.rows("SELECT * FROM delivery_evidence")) == 1


def test_basket_backtest_reuses_engine_without_account_mutation(db):
    ids, b, a, d = setup(db, ("CALL", "CALL"), [[10, 10, 12, 13], [5, 5, 6, 7]])
    ds = b.snapshot(a)["dataset_id"]
    before = db.rows("SELECT * FROM account_ledger")
    config = BasketRun(
        dataset_id=ds,
        initial_capital=10000,
        tickets=[
            dict(
                strategy="bull_call",
                legs=[
                    dict(instrument_id=ids[0], quantity=1),
                    dict(instrument_id=ids[1], quantity=-1),
                ],
                enter_after="2024-01-02T03:50:00Z",
                exit_after="2024-01-02T03:55:00Z",
            )
        ],
    ).model_dump()
    first = run_baskets(db, config)
    second = run_baskets(db, config)
    assert first["net_pnl_paise"] == 100 and first["reconciliation"]["ok"]
    assert first["equity"] == second["equity"] and first["trades"] == second["trades"]
    assert db.rows("SELECT * FROM account_ledger") == before
    assert run_baskets(db, config, cancelled=lambda: True) == {"cancelled": True}


def test_auto_queues_after_signal_then_next_bar_fills_once(db, instrument, monkeypatch):
    ds = import_data(
        db, fixture_csv(count=60), "auto.csv", "5m", "SYNTHETIC TEST", Policy()
    )["id"]
    b = PaperBroker(db)
    a = b.create_account({"name": "TEST", "dataset_id": ds})
    auto = Automation(db)

    class Always:
        def signal(self, f, p):
            return Signal(
                "test_always",
                "LONG",
                99,
                98,
                110,
                "TEST ONLY",
                "TEST",
                str(f.iloc[-1]["timestamp"]),
            )

    monkeypatch.setitem(REGISTRY, "test_always", Always())
    auto.configure(a, instrument, "test_always", 1, {})
    for _ in range(51):
        b.advance_replay(a)
    auto.evaluate(a)
    auto.evaluate(a)
    assert len(db.rows("SELECT * FROM automation_decisions")) == 1
    assert db.rows("SELECT status FROM paper_orders")[0]["status"] == "PENDING"
    assert not b.snapshot(a)["positions"]
    b.advance_replay(a)
    assert db.rows("SELECT status FROM paper_orders")[0]["status"] == "FILLED"
    assert b.reconcile(a)["ok"]
    auto.stop(a)
    assert not db.rows("SELECT * FROM automation_rules WHERE enabled=1")


@pytest.mark.parametrize(
    "url",
    [
        "ws://x.upstox.com",
        "wss://upstox.com.evil.test",
        "wss://user:secret@x.upstox.com",
        "wss://127.0.0.1",
        "wss://x.upstox.com:8443",
    ],
)
def test_stream_uri_guard(url):
    with pytest.raises(DomainError):
        authorized_uri(url)


def test_stream_proto_freshness_status_duplicates(db, monkeypatch):
    at = datetime(2024, 1, 2, 4, 0, tzinfo=UTC)
    monkeypatch.setattr("backend.app.streaming.now", lambda: at)
    i = create_instrument(
        db,
        {
            "symbol": "STREAM_TEST",
            "provider_key": "NSE_EQ|TEST",
            "metadata_source": "SYNTHETIC TEST",
            "tick_size": 0.01,
        },
    )
    s = MarketStream(db, UpstoxProvider(Policy()), threading.RLock())
    s.keys = {"NSE_EQ|TEST": i}
    s.session = "test"
    ms = int(at.timestamp() * 1000)
    frame = {
        "currentTs": str(ms),
        "type": "live_feed",
        "feeds": {
            "NSE_EQ|TEST": {
                "fullFeed": {
                    "marketFF": {"ltpc": {"ltp": 100, "ltt": str(ms)}, "vtt": "100"}
                }
            }
        },
    }
    protobuf = ParseDict(frame, FeedResponse())
    decoded = FeedResponse()
    decoded.ParseFromString(protobuf.SerializeToString())
    s.ingest(MessageToDict(decoded))
    assert s.accepted == 0
    s.ingest(
        {
            "currentTs": str(ms),
            "marketInfo": {"segmentStatus": {"NSE_EQ": "NORMAL_OPEN"}},
        }
    )
    s.ingest(frame)
    s.ingest(frame)
    assert s.accepted == 1 and len(db.rows("SELECT * FROM quotes")) == 1
    frame["feeds"]["NSE_EQ|TEST"]["fullFeed"]["marketFF"]["ltpc"]["ltt"] = str(
        ms - 11000
    )
    s.ingest(frame)
    assert s.accepted == 1 and s.rejected == 2
    assert "token" not in json.dumps(s.status()).lower()


def test_stream_bar_first_partial_and_gap_handling(db, instrument):
    s = MarketStream(db, UpstoxProvider(Policy()), threading.RLock())
    s.session = "TEST"
    at = datetime(2024, 1, 2, 4, 0, 30, tzinfo=UTC)
    assert not s._bar(instrument, at, 10000, 100)
    assert not s._bar(instrument, at + timedelta(minutes=1), 10100, 105)
    assert s._bar(instrument, at + timedelta(minutes=2), 10200, 108)
    assert len(db.rows("SELECT * FROM stream_bars")) == 1
    assert not s._bar(instrument, at + timedelta(minutes=4), 10300, 110)


def test_stream_education_gate(db, instrument):
    s = MarketStream(db, UpstoxProvider(Policy()), threading.RLock())
    with pytest.raises(DomainError, match="Live data disabled"):
        s.start([instrument], [])


def test_live_stream_paper_fill_and_stop_loss(db, monkeypatch):
    at = datetime(2024, 1, 2, 4, 0, tzinfo=UTC)
    monkeypatch.setattr("backend.app.paper.now", lambda: at)
    monkeypatch.setattr("backend.app.streaming.now", lambda: at)
    i = create_instrument(
        db,
        {
            "symbol": "LIVE_TEST_ONLY",
            "provider_key": "NSE_EQ|TEST",
            "metadata_source": "SYNTHETIC TEST",
            "tick_size": 0.01,
        },
    )
    b = PaperBroker(db)
    a = b.create_account({"name": "TEST", "mode": "LIVE"})
    b.place_paper_order(
        a,
        {
            "instrument_id": i,
            "side": "BUY",
            "quantity": 1,
            "stop_loss": 99,
            "request_key": "stream-order-test",
        },
    )
    s = MarketStream(db, UpstoxProvider(Policy()), threading.RLock())
    s.keys = {"NSE_EQ|TEST": i}
    s.accounts = [a]
    s.session = "TEST"
    s.segments = {"NSE_EQ": "NORMAL_OPEN"}

    def frame(price):
        return {
            "currentTs": str(int(at.timestamp() * 1000)),
            "feeds": {
                "NSE_EQ|TEST": {
                    "ltpc": {"ltp": price, "ltt": str(int(at.timestamp() * 1000))}
                }
            },
        }

    s.ingest(frame(100))
    s.ingest(frame(100))
    assert (
        b.snapshot(a)["cash_balance"] == 90000
        and b.snapshot(a)["positions"][0]["quantity"] == 1
    )
    at += timedelta(seconds=1)
    s.ingest(frame(98))
    assert not b.snapshot(a)["positions"] and b.snapshot(a)["cash_balance"] == 99800
    assert (
        b.reconcile(a)["ok"]
        and len(db.rows("SELECT * FROM activity_events WHERE action='PAPER_FILL'")) == 2
    )


def test_stream_reconnect_resubscribes_and_redacts_errors(db, monkeypatch):
    import backend.app.streaming as module

    s = MarketStream(db, UpstoxProvider(Policy()), threading.RLock())
    s.keys = {"NSE_EQ|TEST": 1}
    i = create_instrument(
        db,
        {
            "symbol": "RECONNECT_TEST_ONLY",
            "metadata_source": "SYNTHETIC TEST",
            "tick_size": 0.01,
        },
    )
    b = PaperBroker(db)
    a = b.create_account({"name": "RECONNECT TEST", "mode": "LIVE"})
    for key in ("auto:1:old-signal", "manual-retained"):
        b.place_paper_order(
            a,
            {
                "instrument_id": i,
                "quantity": 1,
                "side": "BUY",
                "stop_loss": 99,
                "request_key": key,
            },
        )
    s.accounts = [a]
    monkeypatch.setattr(
        s.provider,
        "_get",
        lambda path: {
            "authorized_redirect_uri": "wss://test.upstox.com/feed?secret=TEST_TOKEN"
        },
    )
    sockets = []

    class Socket:
        def __enter__(self):
            sockets.append(self)
            return self

        def __exit__(self, *args):
            pass

        def send(self, raw):
            assert isinstance(raw, bytes)
            assert json.loads(raw)["data"]["instrumentKeys"] == ["NSE_EQ|TEST"]

        def recv(self, timeout):
            if len(sockets) == 1:
                raise OSError("TEST_TOKEN must never be logged")
            s.stop_event.set()
            return FeedResponse(
                currentTs=int(datetime.now(UTC).timestamp() * 1000)
            ).SerializeToString()

    monkeypatch.setattr(module, "connect", lambda *args, **kwargs: Socket())
    s._run()
    assert len(sockets) == 2 and s.reconnects == 1 and s.state == "STOPPED"
    assert [o["status"] for o in db.rows("SELECT * FROM paper_orders ORDER BY id")] == [
        "CANCELLED",
        "PENDING",
    ]
    assert "TEST_TOKEN" not in json.dumps(db.rows("SELECT * FROM activity_events"))


def test_runtime_api_automation_delivery_controls(db, dataset, instrument):
    from fastapi.testclient import TestClient
    from backend.app.main import create_app

    app = create_app(db.path, Policy(), legacy_api=True)
    with TestClient(app, headers={"X-PaperLab": "local"}) as client:
        a = client.post(
            "/api/accounts", json={"name": "TEST", "dataset_id": dataset}
        ).json()["id"]
        assert (
            client.post(
                f"/api/accounts/{a}/automation",
                json={
                    "instrument_id": instrument,
                    "strategy": "breakout",
                    "max_quantity": 1,
                },
            ).status_code
            == 200
        )
        assert client.post(f"/api/accounts/{a}/replay/step").status_code == 200
        assert (
            client.get(f"/api/accounts/{a}/decisions").json()[0]["outcome"] == "WARMUP"
        )
        assert client.get("/api/activities").json()
        assert client.delete(f"/api/accounts/{a}/automation").status_code == 200
        assert not client.get("/api/automation").json()[0]["enabled"]
        assert (
            client.post(
                "/api/stream/start",
                json={"instrument_ids": [instrument], "account_ids": []},
            ).status_code
            == 400
        )
        assert client.post(f"/api/accounts/{a}/delivery").json() == {
            "delivered": [],
            "blocked": [],
        }


def test_background_replay_finishes_and_records_decisions(db, dataset, instrument):
    from backend.app.replay_worker import ReplayWorker

    b = PaperBroker(db)
    a = b.create_account({"name": "TEST", "dataset_id": dataset})
    Automation(db).configure(a, instrument, "breakout", 1, {})
    worker = ReplayWorker(db, b, Policy(), threading.RLock())
    worker.start(a, 0.001)
    worker.thread.join(timeout=5)
    assert (
        worker.state == "FINISHED"
        and len(db.rows("SELECT * FROM automation_decisions")) == 5
    )
    assert worker.status()["snapshot"]["reconciliation"]["ok"]


def test_async_basket_job_api_and_invalid_time(db):
    from fastapi.testclient import TestClient
    from backend.app.main import create_app

    ids, b, a, d = setup(db, ("CALL", "CALL"), [[10, 10, 12, 13], [5, 5, 6, 7]])
    app = create_app(db.path, Policy(), legacy_api=True)
    body = {
        "dataset_id": b.snapshot(a)["dataset_id"],
        "initial_capital": 10000,
        "tickets": [
            {
                "strategy": "bull_call",
                "legs": [
                    {"instrument_id": ids[0], "quantity": 1},
                    {"instrument_id": ids[1], "quantity": -1},
                ],
                "enter_after": "bad",
                "exit_after": "2024-01-02T03:55:00Z",
            }
        ],
    }
    with TestClient(app, headers={"X-PaperLab": "local"}) as client:
        assert client.post("/api/basket-backtests", json=body).status_code == 422
        body["tickets"][0]["enter_after"] = "2024-01-02T03:50:00Z"
        job = client.post("/api/basket-backtests", json=body).json()["id"]
        app.state.jobs.executor.submit(lambda: None).result(timeout=10)
        result = client.get(f"/api/basket-backtests/{job}").json()
        assert (
            result["status"] == "COMPLETED" and result["result"]["net_pnl_paise"] == 100
        )
        assert (
            client.get("/api/activities?category=backtest").json()[0]["action"]
            == "COMPLETED"
        )
