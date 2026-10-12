import json
from dataclasses import asdict
from .core import DomainError, now, money
from .paper import PaperBroker
from .data import analysis_frame
from .indicators import compute
from .strategies import REGISTRY
from .risk import size_position
from .activity import record
from .adaptive import choose


class Automation:
    def __init__(self, db):
        self.db = db

    def configure(self, account_id, instrument_id, strategy, max_quantity, parameters):
        if self.db.rows('SELECT 1 FROM fno_sessions WHERE account_id=?',(account_id,)):
            raise DomainError('Use F&O Auto for this managed account')
        b = PaperBroker(self.db)
        a = b.snapshot(account_id)
        inst = self.db.rows("SELECT * FROM instruments WHERE id=?", (instrument_id,))
        if (
            (strategy not in REGISTRY and strategy not in ("adaptive", "learned_daily"))
            or not inst
            or inst[0]["kind"] not in ("SPOT", "CALL", "PUT")
        ):
            raise DomainError(
                "Choose a registered long strategy and spot/option instrument"
            )
        if a["halted"] or max_quantity < 1 or max_quantity % inst[0]["lot_size"]:
            raise DomainError("Active account and whole-lot size required")
        if strategy == "learned_daily":
            from .learning import profile, validate_binding

            validate_binding(
                self.db,
                profile(self.db, parameters.get("learning_profile_id")),
                a,
                inst[0],
                max_quantity,
            )
        if a["mode"] == "REPLAY" and not self.db.rows(
            "SELECT 1 FROM candles WHERE dataset_id=? AND instrument_id=?",
            (a["dataset_id"], instrument_id),
        ):
            raise DomainError("Instrument absent from dataset")
        with self.db.connect(write=True) as c:
            prior = c.execute(
                "SELECT id FROM automation_rules WHERE account_id=?", (account_id,)
            ).fetchone()
            if prior:
                c.execute(
                    "UPDATE paper_orders SET status='CANCELLED',reason='Automation reconfigured' WHERE account_id=? AND status='PENDING' AND request_key LIKE ?",
                    (account_id, f"auto:{prior[0]}:%"),
                )
            c.execute(
                "INSERT INTO automation_rules(account_id,instrument_id,strategy,enabled,max_quantity,parameters,created_at) VALUES(?,?,?,1,?,?,?) ON CONFLICT(account_id) DO UPDATE SET instrument_id=excluded.instrument_id,strategy=excluded.strategy,enabled=1,max_quantity=excluded.max_quantity,parameters=excluded.parameters",
                (
                    account_id,
                    instrument_id,
                    strategy,
                    max_quantity,
                    json.dumps(parameters),
                    now().isoformat(),
                ),
            )
        record(
            self.db,
            "automation",
            "ENABLED",
            {"strategy": strategy, "instrument_id": instrument_id},
            account_id,
        )

    def stop(self, account_id):
        with self.db.connect(write=True) as c:
            c.execute(
                "UPDATE automation_rules SET enabled=0 WHERE account_id=?",
                (account_id,),
            )
            for r in c.execute(
                "SELECT id FROM automation_rules WHERE account_id=?", (account_id,)
            ).fetchall():
                c.execute(
                    "UPDATE paper_orders SET status='CANCELLED',reason='Automation stopped' WHERE account_id=? AND status='PENDING' AND request_key LIKE ?",
                    (account_id, f"auto:{r[0]}:%"),
                )
        record(
            self.db,
            "automation",
            "STOPPED",
            {"positions": "Existing positions retain protective exits"},
            account_id,
        )

    def evaluate(self, account_id, live_rows=None, marks=None):
        rules = self.db.rows(
            "SELECT * FROM automation_rules WHERE account_id=? AND enabled=1",
            (account_id,),
        )
        if not rules:
            return
        r = rules[0]
        b = PaperBroker(self.db)
        a = b.snapshot(account_id, marks)
        if a["halted"]:
            self.stop(account_id)
            return
        rows = (
            live_rows
            if live_rows is not None
            else self.db.rows(
                "SELECT * FROM candles WHERE dataset_id=? AND instrument_id=? AND available_at<=? ORDER BY timestamp",
                (a["dataset_id"], r["instrument_id"], a["clock"]),
            )
        )
        if not rows:
            return
        at = rows[-1]["available_at"]
        if self.db.rows(
            "SELECT 1 FROM automation_decisions WHERE rule_id=? AND timestamp=?",
            (r["id"], at),
        ):
            return
        outcome = "NO_SIGNAL"
        details = {}
        try:
            signal_parameters = json.loads(r["parameters"])
            learned = r["strategy"] == "learned_daily"
            selector = None
            if learned:
                from .learning import daily_context, learned_rank

                p, rows = daily_context(self.db, r, a, at)
                details["learning_profile_id"] = p["id"]
                details["daily_signal_available_at"] = rows[-1]["available_at"]
                selector = lambda excluded: learned_rank(p, rows, excluded)
            exposed = bool(
                a["positions"]
                or a["basket_positions"]
                or self.db.rows(
                    "SELECT 1 FROM paper_orders WHERE account_id=? AND status='PENDING'",
                    (account_id,),
                )
            )
            strategy = r["strategy"]
            quantity_cap = r["max_quantity"]
            if strategy in ("adaptive", "learned_daily"):
                strategy, state = choose(
                    self.db, r, a, rows, exposed, selector=selector, event_at=at
                )
                details["adaptive"] = state
                if learned:
                    signal_parameters["atr_stop"] = (
                        state.get("selected_atr_stop") or 1.5
                    )
                quantity_cap = state["quantity_cap"]
                if not strategy:
                    outcome = state["status"]
                if state.get("halted"):
                    with self.db.connect(write=True) as c:
                        c.execute(
                            "UPDATE paper_orders SET status='CANCELLED',reason='Adaptive drawdown halt' WHERE account_id=? AND status='PENDING' AND request_key LIKE ?",
                            (account_id, f'auto:{r["id"]}:%'),
                        )
            if exposed:
                outcome = "EXPOSURE_OR_PENDING_ORDER"
            elif len(rows) < 51:
                outcome = "WARMUP"
                details.update(
                    bars=len(rows), required=51 if r["strategy"] != "adaptive" else 200
                )
            elif strategy:
                signal = REGISTRY[strategy].signal(
                    compute(analysis_frame(rows)), signal_parameters
                )
                if signal:
                    inst = self.db.rows(
                        "SELECT * FROM instruments WHERE id=?", (r["instrument_id"],)
                    )[0]
                    tick = inst["tick_paise"]
                    price = money(signal.entry_price)
                    if learned and a["mode"] == "LIVE":
                        if not marks or inst["id"] not in marks:
                            raise DomainError("Fresh live price required")
                        price = marks[inst["id"]]
                    stop = money(signal.stop_loss) // tick * tick
                    target = money(signal.target) // tick * tick
                    if learned and not stop < price < target:
                        raise DomainError(
                            "Current price has moved beyond the daily entry's stop or target"
                        )
                    qty = min(
                        quantity_cap // inst["lot_size"] * inst["lot_size"],
                        size_position(
                            a["equity_paise"] or 0,
                            a["available_cash_paise"],
                            price,
                            stop,
                            inst["lot_size"],
                            json.loads(a["risk_json"]),
                            json.loads(a["cost_json"]),
                        ),
                    )
                    details.update(asdict(signal))
                    details["quantity"] = qty
                    if qty:
                        request_key = (
                            f'auto:{r["id"]}:daily:{rows[-1]["available_at"]}'
                            if learned
                            else f'auto:{r["id"]}:{at}'
                        )
                        if learned and self.db.rows(
                            "SELECT 1 FROM paper_orders WHERE account_id=? AND request_key=?",
                            (account_id, request_key),
                        ):
                            raise DomainError(
                                "This daily signal already submitted an entry; waiting for a new completed day"
                            )
                        order = b.place_paper_order(
                            account_id,
                            {
                                "instrument_id": inst["id"],
                                "side": "BUY",
                                "quantity": qty,
                                "stop_loss": stop / 100,
                                "target": target / 100,
                                "strategy": strategy,
                                "request_key": request_key,
                            },
                        )
                        outcome = "ORDER_QUEUED"
                        details["order_id"] = order["id"]
                    else:
                        outcome = "RISK_SIZE_ZERO"
        except DomainError as e:
            outcome = "BLOCKED"
            details = {"reason": str(e)}
        except Exception as e:
            outcome = "ERROR"
            details = {"reason": type(e).__name__, "action": "Strategy disabled"}
            self.stop(account_id)
        with self.db.connect(write=True) as c:
            c.execute(
                "INSERT OR IGNORE INTO automation_decisions(rule_id,timestamp,outcome,details) VALUES(?,?,?,?)",
                (r["id"], at, outcome, json.dumps(details)),
            )
        record(
            self.db,
            "automation",
            outcome,
            {"bar_available_at": at, **details},
            account_id,
        )
