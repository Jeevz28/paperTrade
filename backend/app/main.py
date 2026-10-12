from dataclasses import asdict
from contextlib import asynccontextmanager
from pathlib import Path
import io
import json
import os
import re
import sqlite3
import threading
from urllib.parse import urlparse
import pandas as pd
from fastapi import FastAPI, UploadFile, File, Form, Request, Query
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .core import DomainError, Policy, TIMEFRAMES, money, now, timestamp
from .database import Database
from .schemas import InstrumentInput, AccountInput, OrderInput, BacktestInput, Strict
from .data import create_instrument, import_data, candles, analysis_frame
from .paper import PaperBroker
from .indicators import compute, regime
from .strategies import REGISTRY
from .backtesting import run, walk_forward
from .research import study
from .options import greeks, implied_vol, payoff, chain_statistics
from .providers import UpstoxProvider
from .ai import NoAIProvider, LocalQwenProvider


class NoteInput(Strict):
    notes: str = Field(default="", max_length=10000)
    tags: str = Field(default="", max_length=500)


class HistoryInput(Strict):
    instrument_id: int
    start: str
    end: str
    timeframe: str = "5m"


class GreekInput(Strict):
    spot: float = Field(gt=0)
    strike: float = Field(gt=0)
    years: float = Field(gt=0, le=20)
    rate: float = Field(default=0.06, ge=-1, le=1)
    vol: float = Field(default=0.2, gt=0, le=5)
    kind: str = "CALL"
    premium: float | None = Field(default=None, gt=0)


class OptionLeg(Strict):
    kind: str
    quantity: int = Field(ge=-1000000, le=1000000)
    premium: float = Field(ge=0)
    strike: float = Field(default=0, ge=0)


class PayoffInput(Strict):
    legs: list[OptionLeg] = Field(min_length=1, max_length=4)
    spots: list[float] = Field(min_length=1, max_length=1000)


def create_app(db_path=None, policy=None, *, legacy_api=False, autostart=True):
    from .hosting import Hosting
    hosting = Hosting(db_path)
    db = Database(hosting.db_path)
    policy = policy or Policy.environment()
    broker = PaperBroker(db)
    provider = UpstoxProvider(policy)
    lock = threading.RLock()
    from .streaming import MarketStream
    from .basket_backtesting import BasketJobs
    from .automation import Automation
    from .activity import record

    stream = MarketStream(db, provider, lock)
    from .replay_worker import ReplayWorker, advance

    replay = ReplayWorker(db, broker, policy, lock)
    jobs = BasketJobs(db)
    from .learning import LearningJobs

    learning = LearningJobs(db, policy, provider)
    stream.daily_refresh = learning.refresh_for_account
    for rule in db.rows("SELECT account_id FROM automation_rules WHERE enabled=1"):
        Automation(db).stop(rule["account_id"])

    from .autopilot import AutoPaper, register_autopilot
    auto_paper = AutoPaper(db, provider, stream, lock)

    from .fno import FnoAuto, register_fno
    fno = FnoAuto(db, provider, stream, lock)
    from .fno_runtime import FnoRuntime
    runtime = FnoRuntime(fno)

    @asynccontextmanager
    async def lifespan(app):
        hosting.acquire()
        try:
            if autostart and not legacy_api:
                runtime.boot()
            yield
        finally:
            try:
                runtime.shutdown()
                fno.shutdown()
                auto_paper.shutdown()
                replay.stop()
                stream.stop()
                jobs.shutdown()
                learning.shutdown()
            finally:
                hosting.release()


    app = FastAPI(
        title="NSE PaperLab",
        version="0.8.2",
        lifespan=lifespan,
        description="Private paper simulation. No real-order API.",
    )
    app.state.db = db
    app.state.broker = broker
    app.state.provider = provider
    app.state.stream = stream
    app.state.jobs = jobs
    app.state.replay = replay
    app.state.learning = learning
    app.state.auto_paper = auto_paper
    register_autopilot(app, auto_paper)
    app.state.fno = fno
    register_fno(app, fno)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        path=request.url.path
        if not legacy_api and path.startswith('/api/') and path not in ('/api/health','/api/fno-auto','/api/fno-auto/chart') and not re.fullmatch(r'/api/fno-auto/[1-9][0-9]*/(start|pause|close)',path):
            return JSONResponse({'detail':'This release exposes live F&O paper trading only'},status_code=404)
        blocked = hosting.check(request)
        if blocked is not None:
            return blocked
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and request.headers.get("x-paperlab") != "local"
        ):
            return JSONResponse(
                {"detail": "Missing local request marker"}, status_code=403
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity_error(request, exc):
        return JSONResponse(
            {
                "detail": "Database constraint rejected this change; no financial state was changed"
            },
            status_code=409,
        )

    def instrument(id):
        rows = db.rows("SELECT * FROM instruments WHERE id=?", (id,))
        if not rows:
            raise DomainError("Unknown instrument")
        return rows[0]

    def dataset(id):
        rows = db.rows("SELECT * FROM datasets WHERE id=?", (id,))
        if not rows:
            raise DomainError("Dataset not found")
        last = db.rows(
            "SELECT MAX(available_at) AS last FROM candles WHERE dataset_id=?", (id,)
        )[0]["last"]
        if last:
            policy.check(last)
        return rows[0]

    def load_rows(v):
        d = dataset(v.dataset_id)
        rows = candles(
            db,
            v.instrument_id,
            d["timeframe"],
            v.dataset_id,
            policy.cutoff().isoformat(),
        )
        if v.start:
            rows = [r for r in rows if timestamp(r["timestamp"]) >= timestamp(v.start)]
        if v.end:
            rows = [r for r in rows if timestamp(r["available_at"]) <= timestamp(v.end)]
        return rows

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "paper_only": True,
            "mode": policy.mode,
            "data_cutoff": policy.cutoff().isoformat(),
            "upstox_enabled": os.getenv("UPSTOX_ENABLED", "false") == "true",
            "ai_enabled": os.getenv("QWEN_ENABLED", "false") == "true",
            "version": "0.8.2",
        }

    @app.get("/api/instruments")
    def instruments():
        return db.rows("SELECT * FROM instruments ORDER BY symbol")

    @app.post("/api/instruments")
    def new_instrument(v: InstrumentInput):
        return {"id": create_instrument(db, v.model_dump())}

    @app.get("/api/datasets")
    def datasets():
        return db.rows(
            "SELECT d.*,MIN(c.timestamp) AS first,MAX(c.available_at) AS last FROM datasets d JOIN candles c ON c.dataset_id=d.id GROUP BY d.id ORDER BY d.id DESC"
        )

    @app.post("/api/import")
    async def upload(
        file: UploadFile = File(...),
        timeframe: str = Form("5m"),
        source: str = Form(...),
    ):
        name = Path(file.filename or "import.csv").name
        if not name.lower().endswith((".csv", ".parquet")):
            raise DomainError("Use CSV or Parquet")
        content = await file.read(20_000_001)
        return import_data(db, content, name, timeframe, source, policy)

    @app.delete("/api/datasets/{id}")
    def remove_dataset(id: int):
        with lock, db.connect(write=True) as c:
            if (
                c.execute(
                    "SELECT 1 FROM paper_accounts WHERE dataset_id=?", (id,)
                ).fetchone()
                or c.execute(
                    "SELECT 1 FROM backtests WHERE dataset_id=?", (id,)
                ).fetchone()
            ):
                raise DomainError(
                    "Dataset is referenced by an experiment; audit evidence cannot be deleted"
                )
            if c.execute(
                "SELECT 1 FROM bhav_securities WHERE dataset_id=?", (id,)
            ).fetchone():
                raise DomainError(
                    "NSE learning datasets are retained as immutable research evidence"
                )
            c.execute("DELETE FROM candles WHERE dataset_id=?", (id,))
            c.execute("DELETE FROM datasets WHERE id=?", (id,))
        return {"deleted": id}

    @app.get("/api/quality")
    def quality():
        return db.rows("SELECT * FROM data_quality ORDER BY id DESC LIMIT 100")

    @app.get("/api/candles")
    def get_candles(
        instrument_id: int,
        timeframe: str = "5m",
        dataset_id: int | None = None,
        account_id: int | None = None,
    ):
        as_of = policy.cutoff().isoformat()
        if timeframe not in TIMEFRAMES:
            raise DomainError("Unsupported timeframe")
        if account_id:
            a = broker.snapshot(account_id)
            if a["mode"] == "REPLAY":
                if not a["clock"]:
                    return {"candles": [], "regime": None, "signals": []}
                as_of = min(as_of, a["clock"])
                dataset_id = a["dataset_id"]
        if dataset_id:
            dataset(dataset_id)
        rows = candles(db, instrument_id, timeframe, dataset_id, as_of)
        if not rows:
            return {"candles": [], "regime": None, "signals": []}
        f = compute(analysis_frame(rows))
        signals = [
            asdict(s) for s in (engine.signal(f) for engine in REGISTRY.values()) if s
        ]
        result = json.loads(f.to_json(orient="records", date_format="iso"))
        return {
            "candles": result[-2000:],
            "regime": regime(f),
            "signals": signals,
            "total_rows": len(rows),
        }

    @app.get("/api/accounts")
    def accounts():
        return db.rows(
            "SELECT id,name,initial_capital,mode,dataset_id,halted FROM paper_accounts ORDER BY id DESC"
        )

    @app.post("/api/accounts")
    def new_account(v: AccountInput):
        if v.mode == "LIVE":
            policy.require_live()
        if v.dataset_id:
            dataset(v.dataset_id)
        with lock:
            return {"id": broker.create_account(v.model_dump())}

    @app.get("/api/accounts/{id}")
    def account(id: int):
        with lock:
            return broker.snapshot(id)

    @app.post("/api/accounts/{id}/halt")
    def halt(id: int):
        with lock, db.connect(write=True) as c:
            broker.account(c, id)
            c.execute(
                "UPDATE paper_accounts SET halted=1,halt_reason='User halted experiment' WHERE id=?",
                (id,),
            )
            c.execute(
                "UPDATE paper_orders SET status='CANCELLED',reason='User halted experiment' WHERE account_id=? AND status='PENDING'",
                (id,),
            )
        return {"halted": True}

    @app.post("/api/accounts/{id}/replay/step")
    def step(id: int):
        with lock:
            if replay.state == "RUNNING" and replay.account_id == id:
                raise DomainError("Pause background replay before stepping manually")
            return advance(db, broker, policy, id)

    @app.get("/api/accounts/{id}/orders")
    def orders(id: int):
        return db.rows(
            "SELECT o.*,i.symbol FROM paper_orders o JOIN instruments i ON i.id=o.instrument_id WHERE account_id=? ORDER BY o.id DESC",
            (id,),
        )

    @app.post("/api/accounts/{id}/orders")
    def new_order(id: int, v: OrderInput):
        with lock:
            a = broker.snapshot(id)
            if a["mode"] == "LIVE":
                provider.get_latest_quote(
                    instrument(v.instrument_id)["provider_key"] or ""
                )
            return broker.place_paper_order(id, v.model_dump())

    @app.delete("/api/accounts/{id}/orders/{order_id}")
    def cancel(id: int, order_id: int):
        with lock:
            broker.cancel_paper_order(id, order_id)
        return {"cancelled": order_id}

    @app.patch("/api/accounts/{id}/orders/{order_id}")
    def modify(id: int, order_id: int, limit_price: float = Query(gt=0)):
        with lock:
            broker.modify_paper_order(id, order_id, limit_price)
        return {"modified": order_id}

    @app.get("/api/accounts/{id}/ledger")
    def ledger(id: int):
        return db.rows(
            "SELECT * FROM account_ledger WHERE account_id=? ORDER BY event_id", (id,)
        )

    @app.get("/api/accounts/{id}/ledger.csv")
    def ledger_csv(id: int):
        return Response(
            pd.DataFrame(ledger(id)).to_csv(index=False),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=paper-ledger.csv"},
        )

    @app.get("/api/accounts/{id}/journal")
    def journal(id: int):
        return db.rows(
            "SELECT t.*,i.symbol,j.notes,j.tags FROM paper_trades t JOIN journal j ON j.trade_id=t.id JOIN instruments i ON i.id=t.instrument_id WHERE account_id=? ORDER BY t.id DESC",
            (id,),
        )

    @app.patch("/api/journal/{trade_id}")
    def note(trade_id: int, v: NoteInput):
        with db.connect(write=True) as c:
            c.execute(
                "UPDATE journal SET notes=?,tags=? WHERE trade_id=?",
                (v.notes, v.tags, trade_id),
            )
        return {"saved": True}

    @app.get("/api/accounts/{id}/risk")
    def risk(id: int):
        return {
            "events": db.rows(
                "SELECT * FROM risk_events WHERE account_id=? ORDER BY id DESC LIMIT 100",
                (id,),
            ),
            "account": broker.snapshot(id),
        }

    @app.get("/api/alerts")
    def alerts():
        return db.rows("SELECT * FROM alerts ORDER BY id DESC LIMIT 100")

    @app.post("/api/alerts/{id}/acknowledge")
    def ack(id: int):
        with db.connect(write=True) as c:
            c.execute("UPDATE alerts SET acknowledged=1 WHERE id=?", (id,))
        return {"acknowledged": True}

    @app.get("/api/strategies")
    def strategies():
        return [
            {
                "name": s.name,
                "description": s.description,
                "direction": "LONG",
                "parameters": {"atr_stop": 1.5},
            }
            for s in REGISTRY.values()
        ]

    @app.post("/api/backtests")
    def backtest(v: BacktestInput):
        if set(v.parameters) - {"atr_stop", "opening_minutes"}:
            raise DomainError("Unsupported strategy parameter")
        if not 0.1 <= float(v.parameters.get("atr_stop", 1.5)) <= 10:
            raise DomainError("ATR stop must be 0.1 to 10")
        result = run(
            load_rows(v),
            instrument(v.instrument_id),
            v.strategy,
            money(v.initial_capital),
            v.risk.model_dump(),
            v.costs.model_dump(),
            v.parameters,
        )
        result["dataset_id"] = v.dataset_id
        result["data_source"] = dataset(v.dataset_id)["source"]
        with db.connect(write=True) as c:
            cur = c.execute(
                "INSERT INTO backtests(timestamp,dataset_id,strategy,parameters,results) VALUES(?,?,?,?,?)",
                (
                    now().isoformat(),
                    v.dataset_id,
                    v.strategy,
                    v.model_dump_json(),
                    json.dumps(result),
                ),
            )
            id = cur.lastrowid
            c.executemany(
                "INSERT INTO backtest_trades(backtest_id,payload) VALUES(?,?)",
                [(id, json.dumps(t)) for t in result["trades"]],
            )
        return {"id": id, **result}

    @app.get("/api/backtests")
    def past_tests():
        rows = db.rows("SELECT * FROM backtests ORDER BY id DESC LIMIT 30")
        return [
            {
                **r,
                "results": json.loads(r["results"]),
                "parameters": json.loads(r["parameters"]),
            }
            for r in rows
        ]

    @app.post("/api/research/compare")
    def compare(v: BacktestInput):
        rows = load_rows(v)
        inst = instrument(v.instrument_id)
        return [
            {
                "strategy": name,
                **run(
                    rows,
                    inst,
                    name,
                    money(v.initial_capital),
                    v.risk.model_dump(),
                    v.costs.model_dump(),
                )["metrics"],
            }
            for name in REGISTRY
        ]

    @app.post("/api/research/walk-forward")
    def walk(v: BacktestInput, train: int = 150, validation: int = 75, test: int = 75):
        return walk_forward(
            load_rows(v),
            instrument(v.instrument_id),
            v.strategy,
            money(v.initial_capital),
            v.risk.model_dump(),
            v.costs.model_dump(),
            train,
            validation,
            test,
        )

    @app.post("/api/research/study")
    def research(v: BacktestInput, horizon: int = Query(default=5, ge=1, le=100)):
        return study(load_rows(v), horizon)

    @app.post("/api/options/greeks")
    def options_greeks(v: GreekInput):
        out = greeks(v.spot, v.strike, v.years, v.rate, v.vol, v.kind)
        if v.premium:
            out["implied_volatility"] = implied_vol(
                v.premium, v.spot, v.strike, v.years, v.rate, v.kind
            )
        return out

    @app.post("/api/options/payoff")
    def options_payoff(body: PayoffInput):
        return payoff([x.model_dump() for x in body.legs], body.spots)

    @app.get("/api/upstox/instruments")
    def master():
        return provider.get_instruments()

    @app.post("/api/upstox/history")
    def history(v: HistoryInput):
        inst = instrument(v.instrument_id)
        rows = provider.get_candles(
            inst["provider_key"] or "", v.start, v.end, v.timeframe
        )
        if not rows:
            raise DomainError("Provider returned no historical candles")
        for r in rows:
            r["symbol"] = inst["symbol"]
        content = pd.DataFrame(rows).to_csv(index=False).encode()
        return import_data(
            db,
            content,
            f"upstox-{inst['symbol']}-{v.start}-{v.end}.csv",
            v.timeframe,
            "Upstox historical API",
            policy,
        )

    @app.get("/api/upstox/option-chain")
    def option_chain(instrument_key: str, expiry: str):
        rows = provider.get_option_chain(instrument_key, expiry)
        at = now().isoformat()
        with db.connect(write=True) as c:
            c.execute(
                "INSERT INTO option_chain(underlying,expiry,timestamp,source,payload) VALUES(?,?,?,?,?)",
                (instrument_key, expiry, at, "UPSTOX_REST", json.dumps(rows)),
            )
        stats = (
            chain_statistics(
                [
                    {
                        "strike": r["strike_price"],
                        "call_oi": r.get("call_options", {})
                        .get("market_data", {})
                        .get("oi", 0),
                        "put_oi": r.get("put_options", {})
                        .get("market_data", {})
                        .get("oi", 0),
                    }
                    for r in rows
                ]
            )
            if rows
            else None
        )
        return {
            "rows": rows,
            "received_at": at,
            "statistics": stats,
            "label": "Snapshot, not an execution quote",
        }

    @app.post("/api/accounts/{id}/live/poll")
    def poll(id: int, instrument_id: int):
        policy.require_live()
        with lock:
            a = broker.snapshot(id)
            if a["mode"] != "LIVE":
                raise DomainError("Not a live paper account")
            ids = {instrument_id} | {p["instrument_id"] for p in a["positions"]}
            observations = {
                i: provider.get_latest_quote(instrument(i)["provider_key"] or "")
                for i in ids
            }
            marks = {i: q["price_paise"] for i, q in observations.items()}
            for i, q in observations.items():
                if (now() - timestamp(q["timestamp"])).total_seconds() > 10:
                    raise DomainError("Quote became stale while polling; no execution")
            for i, q in observations.items():
                with db.connect(write=True) as c:
                    cur = c.execute(
                        "INSERT INTO quotes(instrument_id,timestamp,received_at,price,source,payload) VALUES(?,?,?,?,?,?)",
                        (
                            i,
                            q["timestamp"],
                            q["received_at"],
                            q["price_paise"],
                            q["source"],
                            json.dumps(q["raw"]),
                        ),
                    )
                    ref = f"quote:{cur.lastrowid}"
                broker.protective_orders(id, i, q["price_paise"], q["timestamp"])
                broker.execute_observation(
                    id, i, q["price_paise"], q["timestamp"], ref, marks
                )
            return {
                "quotes": [
                    {k: v for k, v in q.items() if k != "raw"}
                    for q in observations.values()
                ],
                "snapshot": broker.snapshot(id, marks),
            }

    @app.post("/api/ai/analyze")
    def ai(account_id: int | None = None):
        snapshot = (
            {"account": broker.snapshot(account_id), "trades": journal(account_id)}
            if account_id
            else {"backtests": past_tests()[:3]}
        )
        ai_provider = (
            LocalQwenProvider(db)
            if os.getenv("QWEN_ENABLED", "false") == "true"
            else NoAIProvider()
        )
        return ai_provider.analyze(
            "Explain observed paper performance and its limitations", snapshot
        )

    from .extensions_api import register_extensions

    register_extensions(app, db, broker, policy, lock)
    from .runtime_api import register_runtime

    register_runtime(app, db, broker, policy, lock, stream, jobs, replay)
    from .learning_api import register_learning

    register_learning(app, db, broker, policy, lock, stream, replay, learning)
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/")
        def index():
            return FileResponse(dist / "index.html")

    if not legacy_api:
        allowed = {'/api/health','/api/fno-auto','/api/fno-auto/chart','/api/fno-auto/{account}/start','/api/fno-auto/{account}/pause','/api/fno-auto/{account}/close'}
        app.router.routes[:] = [r for r in app.router.routes if not getattr(r,'path','').startswith('/api/') or r.path in allowed]
        original_openapi=app.openapi
        def focused_schema():
            schema=original_openapi()
            schema['paths']={p:v for p,v in schema.get('paths',{}).items() if p in allowed or not p.startswith('/api/')}
            return schema
        app.openapi=focused_schema
    return app


app = create_app()
