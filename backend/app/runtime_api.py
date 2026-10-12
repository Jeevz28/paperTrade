import json
from fastapi import APIRouter, Query
from pydantic import Field, field_validator
from .schemas import Strict, RiskConfig, CostConfig
from .extensions_api import BasketInput
from .core import DomainError, timestamp
from .automation import Automation
from .delivery import deliver


class StreamInput(Strict):
    instrument_ids: list[int] = Field(min_length=1, max_length=2000)
    account_ids: list[int] = Field(default_factory=list, max_length=20)


class AutoInput(Strict):
    instrument_id: int
    strategy: str
    max_quantity: int = Field(ge=1, le=1000000)
    atr_stop: float = Field(default=1.5, gt=0, le=20)
    learning_profile_id: int | None = Field(default=None, ge=1)


class Ticket(BasketInput):
    request_key: str = "scheduled-ticket"
    enter_after: str
    exit_after: str

    @field_validator("enter_after", "exit_after")
    @classmethod
    def valid_time(cls, v):
        return timestamp(v).isoformat()


class ReplayInput(Strict):
    account_id: int
    interval_seconds: float = Field(default=1, ge=0.25, le=30)


class BasketRun(Strict):
    dataset_id: int
    initial_capital: float = Field(default=1000, gt=0, le=100000000)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    costs: CostConfig = Field(default_factory=CostConfig)
    tickets: list[Ticket] = Field(min_length=1, max_length=100)
    apply_delivery: bool = False
    apply_cash_settlement: bool = False


def register_runtime(app, db, broker, policy, lock, stream, jobs, replay):
    router = APIRouter(prefix="/api")

    @router.get("/replay-worker")
    def replay_status():
        return replay.status()

    @router.post("/replay-worker/start")
    def replay_start(v: ReplayInput):
        with lock:
            replay.start(v.account_id, v.interval_seconds)
        return replay.status()

    @router.post("/replay-worker/stop")
    def replay_stop():
        replay.stop()
        return replay.status()

    @router.get("/stream")
    def status():
        return stream.status()

    @router.post("/stream/start")
    def start(v: StreamInput):
        with lock:
            stream.start(v.instrument_ids, v.account_ids)
        return stream.status()

    @router.post("/stream/stop")
    def stop():
        stream.stop()
        return stream.status()

    @router.get("/automation")
    def rules():
        rows = db.rows(
            "SELECT r.*,s.payload AS adaptive FROM automation_rules r LEFT JOIN adaptive_state s ON s.rule_id=r.id ORDER BY r.id"
        )
        for row in rows:
            row["adaptive"] = json.loads(row["adaptive"]) if row["adaptive"] else None
        return rows

    @router.post("/accounts/{id}/automation")
    def enable(id: int, v: AutoInput):
        with lock:
            if db.rows("SELECT 1 FROM auto_sessions WHERE account_id=?", (id,)):
                raise DomainError("Use the Auto Paper page to start or pause this account")
            s = broker.snapshot(id)
            if s["mode"] == "LIVE":
                policy.require_live()
                if (
                    id not in stream.accounts
                    or v.instrument_id not in stream.keys.values()
                ):
                    raise DomainError(
                        "This account is not attached to the selected feed. In Live WebSocket feed, tick Process queued paper orders, select this instrument, then stop and restart the stream. Or use Auto Paper for automatic setup."
                    )
            Automation(db).configure(
                id,
                v.instrument_id,
                v.strategy,
                v.max_quantity,
                {
                    "atr_stop": v.atr_stop,
                    **(
                        {"learning_profile_id": v.learning_profile_id}
                        if v.learning_profile_id
                        else {}
                    ),
                },
            )
            if s["mode"] == "LIVE" and v.strategy == "learned_daily":
                app.state.learning.refresh_for_account(id)
        return {"enabled": True}

    @router.delete("/accounts/{id}/automation")
    def disable(id: int):
        with lock:
            Automation(db).stop(id)
        return {"enabled": False}

    @router.get("/accounts/{id}/decisions")
    def decisions(id: int):
        return db.rows(
            "SELECT d.* FROM automation_decisions d JOIN automation_rules r ON r.id=d.rule_id WHERE r.account_id=? ORDER BY d.id DESC LIMIT 500",
            (id,),
        )

    @router.post("/accounts/{id}/delivery")
    def delivery(id: int):
        with lock:
            a = broker.snapshot(id)
            if a["clock"]:
                policy.check(a["clock"])
            return deliver(db, id)

    @router.get("/accounts/{id}/deliveries")
    def deliveries(id: int):
        return db.rows(
            "SELECT d.* FROM delivery_obligations d JOIN baskets b ON b.id=d.basket_id WHERE b.account_id=? ORDER BY d.id DESC",
            (id,),
        )

    @router.get("/activities")
    def activities(
        after: int = Query(0, ge=0),
        account_id: int | None = None,
        category: str | None = None,
    ):
        query = "SELECT * FROM activity_events WHERE id>?"
        args = [after]
        if account_id is not None:
            query += " AND (account_id=? OR account_id IS NULL)"
            args.append(account_id)
        if category:
            query += " AND category=?"
            args.append(category)
        query += " ORDER BY id DESC LIMIT 300"
        return db.rows(query, args)

    @router.post("/basket-backtests")
    def backtest(v: BasketRun):
        rows = db.rows(
            "SELECT MAX(available_at) AS latest FROM candles WHERE dataset_id=?",
            (v.dataset_id,),
        )
        if not rows or not rows[0]["latest"]:
            raise DomainError("Empty dataset")
        policy.check(rows[0]["latest"])
        for t in v.tickets:
            if timestamp(t.enter_after) >= timestamp(t.exit_after):
                raise DomainError("Entry must precede exit")
        return jobs.submit(v.model_dump())

    @router.get("/basket-backtests")
    def backtests():
        return db.rows(
            "SELECT id,created_at,status,progress,error FROM basket_backtests ORDER BY id DESC LIMIT 100"
        )

    @router.get("/basket-backtests/{id}")
    def result(id: int):
        rows = db.rows("SELECT * FROM basket_backtests WHERE id=?", (id,))
        if not rows:
            raise DomainError("Unknown backtest")
        r = rows[0]
        r["result"] = json.loads(r["result"]) if r["result"] else None
        return r

    @router.delete("/basket-backtests/{id}")
    def cancel(id: int):
        with db.connect(write=True) as c:
            c.execute(
                "UPDATE basket_backtests SET status='CANCEL_REQUESTED' WHERE id=? AND status IN ('QUEUED','RUNNING')",
                (id,),
            )
        return {"cancel_requested": True}

    app.include_router(router)
