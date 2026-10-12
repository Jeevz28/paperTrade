import json
import threading
from typing import Literal
from pydantic import Field
from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import FileResponse
from .schemas import Strict
from .core import DomainError, money, timestamp
from .derivatives import DerivativeBroker
from .evidence import import_evidence, option_series
from .analytics import stratify
from .operations import backup_database


class BasketLeg(Strict):
    instrument_id: int
    quantity: int = Field(ge=-1000000, le=1000000)


class BasketInput(Strict):
    strategy: Literal[
        "future",
        "long_call",
        "long_put",
        "bull_call",
        "bear_put",
        "covered_call",
        "protective_put",
    ]
    legs: list[BasketLeg] = Field(min_length=1, max_length=2)
    request_key: str = Field(min_length=8, max_length=100)
    stop_loss: float | None = Field(default=None, gt=0)


def register_extensions(app, db, broker, policy, lock):
    router = APIRouter(prefix="/api")
    derivative = DerivativeBroker(db)

    @router.post("/evidence/{kind}")
    async def upload(kind: str, file: UploadFile = File(...), source: str = Form(...)):
        return import_evidence(db, await file.read(5_000_001), kind, source, policy)

    @router.get("/evidence/{kind}")
    def list_evidence(kind: str):
        tables = {
            "margin": "margin_evidence",
            "settlement": "settlement_evidence",
            "calendar": "session_calendar",
            "delivery": "delivery_evidence",
        }
        if kind not in tables:
            raise DomainError("Unknown evidence type")
        return db.rows("SELECT * FROM " + tables[kind] + " LIMIT 1000")

    @router.get("/accounts/{id}/baskets")
    def baskets(id: int):
        return db.rows(
            "SELECT * FROM baskets WHERE account_id=? ORDER BY id DESC", (id,)
        )

    @router.post("/accounts/{id}/baskets")
    def submit(id: int, v: BasketInput):
        with lock:
            a = broker.snapshot(id)
            if a["clock"]:
                policy.check(a["clock"])
            return derivative.submit(
                id,
                v.strategy,
                [x.model_dump() for x in v.legs],
                v.request_key,
                money(v.stop_loss) if v.stop_loss else None,
            )

    @router.post("/accounts/{id}/baskets/{basket_id}/close")
    def close(id: int, basket_id: int):
        with lock:
            derivative.request_close(id, basket_id)
        return {"status": "CLOSING"}

    @router.delete("/accounts/{id}/baskets/{basket_id}")
    def cancel(id: int, basket_id: int):
        with lock, db.connect(write=True) as c:
            cur = c.execute(
                "UPDATE baskets SET status='CANCELLED' WHERE account_id=? AND id=? AND status='PENDING'",
                (id, basket_id),
            )
            if not cur.rowcount:
                raise DomainError("Only pending baskets can be cancelled")
        return {"cancelled": True}

    @router.post("/accounts/{id}/settle")
    def settle(id: int):
        with lock:
            a = broker.snapshot(id)
            if a["clock"]:
                policy.check(a["clock"])
            return derivative.settle(id)

    @router.get("/accounts/{id}/basket-trades")
    def basket_trades(id: int):
        return db.rows(
            "SELECT t.*,b.strategy FROM basket_trades t JOIN baskets b ON b.id=t.basket_id WHERE b.account_id=? ORDER BY t.id DESC",
            (id,),
        )

    @router.get("/accounts/{id}/analytics")
    def account_analytics(id: int):
        trades = db.rows(
            "SELECT t.*,i.symbol FROM paper_trades t JOIN instruments i ON i.id=t.instrument_id WHERE account_id=?",
            (id,),
        )
        trades += db.rows(
            "SELECT b.opened_at AS entry_time,t.timestamp AS exit_time,t.gross_pnl-t.fees AS net_pnl,b.strategy,'basket' AS symbol FROM basket_trades t JOIN baskets b ON b.id=t.basket_id WHERE b.account_id=?",
            (id,),
        )
        return stratify(trades)

    @router.get("/accounts/{id}/snapshots")
    def snapshots(id: int):
        return [
            json.loads(r["payload"])
            for r in db.rows(
                "SELECT payload FROM snapshots WHERE account_id=? ORDER BY timestamp",
                (id,),
            )
        ]

    @router.get("/backtests/{id}/analytics")
    def backtest_analytics(id: int):
        rows = db.rows("SELECT * FROM backtests WHERE id=?", (id,))
        if not rows:
            raise DomainError("Backtest not found")
        result = json.loads(rows[0]["results"])
        params = json.loads(rows[0]["parameters"])
        return stratify(
            [
                {
                    "strategy": rows[0]["strategy"],
                    "instrument_id": params.get("instrument_id"),
                    **t,
                }
                for t in result["trades"]
            ]
        )

    @router.get("/options/history")
    def history(instrument_id: int, account_id: int | None = None):
        at = policy.cutoff().isoformat()
        if account_id:
            a = broker.snapshot(account_id)
            if not a["clock"]:
                return {"rows": [], "iv_percentile": None, "oi_change": None}
            at = min(at, a["clock"])
        return option_series(db, instrument_id, at)

    @router.post("/operations/backup")
    def backup():
        with lock:
            path = backup_database(db)
        return FileResponse(
            path, media_type="application/vnd.sqlite3", filename=path.name
        )

    @router.get("/operations/status")
    def status():
        with db.connect() as c:
            return {
                "schema_version": c.execute("PRAGMA user_version").fetchone()[0],
                "integrity": c.execute("PRAGMA integrity_check").fetchone()[0],
                "migrations": [
                    dict(r)
                    for r in c.execute(
                        "SELECT * FROM schema_migrations ORDER BY version"
                    )
                ],
            }

    app.include_router(router)
