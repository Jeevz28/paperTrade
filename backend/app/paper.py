from datetime import timedelta
import hashlib
import json
from .core import DomainError, money, now, timestamp
from .risk import charges, check_entry, day
from .schemas import AccountInput, OrderInput

LEDGER_FIELDS = [
    "account_id",
    "timestamp",
    "event_type",
    "symbol",
    "quantity",
    "price",
    "gross_amount",
    "fees",
    "slippage",
    "realized_pnl",
    "balance_before",
    "balance_after",
    "source",
    "reference_id",
    "prev_hash",
]


def event_hash(event):
    return hashlib.sha256(
        json.dumps(
            {k: event[k] for k in LEDGER_FIELDS}, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def append_event(
    c,
    account_id,
    at,
    kind,
    before,
    after,
    ref,
    symbol=None,
    quantity=0,
    price=0,
    gross=0,
    fees=0,
    realized=0,
    source="INTERNAL",
    slippage=0,
):
    prev = c.execute(
        "SELECT event_hash FROM account_ledger WHERE account_id=? ORDER BY event_id DESC LIMIT 1",
        (account_id,),
    ).fetchone()
    values = [
        account_id,
        at,
        kind,
        symbol,
        quantity,
        price,
        gross,
        fees,
        slippage,
        realized,
        before,
        after,
        source,
        ref,
        prev[0] if prev else "GENESIS",
    ]
    digest = event_hash(dict(zip(LEDGER_FIELDS, values)))
    c.execute(
        "INSERT INTO account_ledger("
        + ",".join(LEDGER_FIELDS)
        + ",event_hash) VALUES("
        + ",".join("?" for _ in range(16))
        + ")",
        (*values, digest),
    )


class PaperBroker:
    """Only SQLite state. No network client, provider credential, or broker SDK."""

    def __init__(self, db):
        self.db = db

    def account(self, c, account_id):
        row = c.execute(
            "SELECT * FROM paper_accounts WHERE id=?", (account_id,)
        ).fetchone()
        if not row:
            raise DomainError("Paper account not found")
        return dict(row)

    def create_account(self, data):
        v = AccountInput.model_validate(data)
        capital = money(v.initial_capital)
        if capital < 1:
            raise DomainError("Capital must be at least one paise")
        with self.db.connect(write=True) as c:
            if v.mode == "REPLAY":
                if (
                    not v.dataset_id
                    or not c.execute(
                        "SELECT 1 FROM datasets WHERE id=?", (v.dataset_id,)
                    ).fetchone()
                ):
                    raise DomainError("Replay requires an imported dataset")
            at = now().isoformat()
            cur = c.execute(
                "INSERT INTO paper_accounts(name,initial_capital,cash_balance,mode,dataset_id,risk_json,cost_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    v.name,
                    capital,
                    capital,
                    v.mode,
                    v.dataset_id,
                    v.risk.model_dump_json(),
                    v.costs.model_dump_json(),
                    at,
                ),
            )
            account_id = cur.lastrowid
            append_event(c, account_id, at, "INITIAL_CAPITAL", 0, capital, "initial")
        return account_id

    def reconcile(self, account_id):
        with self.db.connect(write=True) as c:
            a = self.account(c, account_id)
            events = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM account_ledger WHERE account_id=? ORDER BY event_id",
                    (account_id,),
                )
            ]
            prev, balance, costs, pnl = "GENESIS", 0, 0, 0
            ok = (
                bool(events)
                and events[0]["event_type"] == "INITIAL_CAPITAL"
                and events[0]["balance_after"] == a["initial_capital"]
            )
            for e in events:
                ok = (
                    ok
                    and e["prev_hash"] == prev
                    and e["event_hash"] == event_hash(e)
                    and e["balance_before"] == balance
                )
                if e["event_type"] == "PAPER_BUY":
                    ok = (
                        ok
                        and e["balance_after"]
                        == balance - e["gross_amount"] - e["fees"]
                    )
                elif e["event_type"] == "PAPER_SELL":
                    ok = (
                        ok
                        and e["balance_after"]
                        == balance + e["gross_amount"] - e["fees"]
                    )
                elif e["event_type"] in (
                    "BASKET_OPEN",
                    "BASKET_CLOSE",
                    "BASKET_SETTLEMENT",
                    "BASKET_DELIVERY",
                ):
                    ok = (
                        ok
                        and e["balance_after"]
                        == balance + e["gross_amount"] - e["fees"]
                    )
                elif e["event_type"] != "INITIAL_CAPITAL":
                    ok = False
                prev, balance = e["event_hash"], e["balance_after"]
                costs += e["fees"]
                pnl += e["realized_pnl"]
            book = c.execute(
                "SELECT COALESCE(SUM(cost_basis),0) FROM paper_positions WHERE account_id=?",
                (account_id,),
            ).fetchone()[0]
            from .derivatives import exposure, audit

            book += sum(p["book_cost"] for p in exposure(c, account_id))
            ok = ok and audit(c, account_id, events)
            ok = (
                ok
                and balance == a["cash_balance"]
                and costs == a["costs"]
                and pnl == a["realized_pnl"]
                and balance + book == a["initial_capital"] + pnl - costs
            )
            quantities = {}
            for e in events:
                if e["symbol"]:
                    quantities[e["symbol"]] = quantities.get(e["symbol"], 0) + (
                        e["quantity"]
                        if e["event_type"] == "PAPER_BUY"
                        else -e["quantity"]
                    )
            held = {
                r["symbol"]: r["quantity"]
                for r in c.execute(
                    "SELECT p.quantity,i.symbol FROM paper_positions p JOIN instruments i ON i.id=p.instrument_id WHERE account_id=?",
                    (account_id,),
                )
            }
            ok = ok and {k: v for k, v in quantities.items() if v} == held
            if not ok:
                c.execute(
                    "UPDATE paper_accounts SET halted=1,halt_reason='ACCOUNTING ERROR - PAPER TRADING HALTED' WHERE id=?",
                    (account_id,),
                )
                c.execute(
                    "INSERT INTO risk_events(account_id,timestamp,reason) VALUES(?,?,?)",
                    (
                        account_id,
                        now().isoformat(),
                        "ACCOUNTING ERROR - PAPER TRADING HALTED",
                    ),
                )
        return {
            "ok": bool(ok),
            "cash_paise": balance,
            "book_cost_paise": book,
            "ledger_events": len(events),
        }

    def snapshot(self, account_id, live_marks=None):
        check = self.reconcile(account_id)
        with self.db.connect() as c:
            a = self.account(c, account_id)
            positions = [
                dict(x)
                for x in c.execute(
                    "SELECT p.*,i.symbol FROM paper_positions p JOIN instruments i ON i.id=p.instrument_id WHERE account_id=?",
                    (account_id,),
                )
            ]
            value = 0
            available = True
            for p in positions:
                mark = None
                if a["mode"] == "REPLAY" and a["clock"]:
                    row = c.execute(
                        "SELECT close,available_at FROM candles WHERE instrument_id=? AND dataset_id=? AND available_at<=? ORDER BY available_at DESC LIMIT 1",
                        (p["instrument_id"], a["dataset_id"], a["clock"]),
                    ).fetchone()
                    if row and row["available_at"] == a["clock"]:
                        mark = row["close"]
                elif live_marks:
                    mark = live_marks.get(p["instrument_id"])
                p["mark_paise"] = mark
                p["unrealized_pnl_paise"] = (
                    mark * p["quantity"] - p["cost_basis"] if mark is not None else None
                )
                value += mark * p["quantity"] if mark is not None else 0
                available = available and mark is not None
            from .derivatives import mark_positions, collateral

            basket_positions, basket_pnl = mark_positions(c, a, live_marks)
            basket_book = sum(p["book_cost"] for p in basket_positions)
            equity = (
                a["cash_balance"] + value + basket_book + basket_pnl
                if available and basket_pnl is not None
                else None
            )
            book = sum(p["cost_basis"] for p in positions)
            return {
                **a,
                "positions": positions,
                "basket_positions": basket_positions,
                "equity_paise": equity,
                "unrealized_pnl_paise": (
                    value - book + basket_pnl
                    if available and basket_pnl is not None
                    else None
                ),
                "available_cash_paise": a["cash_balance"] - collateral(c, account_id),
                "used_margin_paise": collateral(c, account_id),
                "reconciliation": check,
                "price_status": (
                    "AVAILABLE"
                    if available and basket_pnl is not None
                    else "PRICE UNAVAILABLE"
                ),
            }

    def place_paper_order(self, account_id, data):
        v = OrderInput.model_validate(data)
        if not self.reconcile(account_id)["ok"]:
            raise DomainError("ACCOUNTING ERROR - PAPER TRADING HALTED")
        with self.db.connect(write=True) as c:
            a = self.account(c, account_id)
            if c.execute('SELECT 1 FROM fno_sessions WHERE account_id=?',(account_id,)).fetchone():
                raise DomainError('Use F&O Auto for this managed account; standalone orders are disabled')
            existing = c.execute(
                "SELECT * FROM paper_orders WHERE account_id=? AND request_key=?",
                (account_id, v.request_key),
            ).fetchone()
            if existing:
                return dict(existing)
            if a["halted"]:
                raise DomainError(a["halt_reason"] or "PAPER TRADING HALTED")
            inst = c.execute(
                "SELECT * FROM instruments WHERE id=?", (v.instrument_id,)
            ).fetchone()
            if not inst:
                raise DomainError("Unknown instrument")
            if v.quantity % inst["lot_size"]:
                raise DomainError("Quantity must respect contract lot size")
            if a["mode"] == "REPLAY":
                if not a["clock"]:
                    raise DomainError(
                        "Advance replay to a visible candle before submitting an order"
                    )
                if not c.execute(
                    "SELECT 1 FROM candles WHERE dataset_id=? AND instrument_id=?",
                    (a["dataset_id"], v.instrument_id),
                ).fetchone():
                    raise DomainError("Instrument absent from this replay dataset")
            if v.order_type == "LIMIT" and v.limit_price is None:
                raise DomainError("Limit price required")
            if v.side == "BUY" and v.stop_loss is None:
                raise DomainError("Stop loss required")
            for price in (v.limit_price, v.stop_loss, v.target, v.trailing_distance):
                if price is not None and (
                    money(price) <= 0 or money(price) % inst["tick_paise"]
                ):
                    raise DomainError("Order prices must respect tick size")
            at = a["clock"] if a["mode"] == "REPLAY" else now().isoformat()
            cur = c.execute(
                "INSERT INTO paper_orders(account_id,instrument_id,side,quantity,order_type,limit_paise,stop_paise,target_paise,trail_paise,status,strategy,created_at,request_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    account_id,
                    v.instrument_id,
                    v.side,
                    v.quantity,
                    v.order_type,
                    money(v.limit_price) if v.limit_price else None,
                    money(v.stop_loss) if v.stop_loss else None,
                    money(v.target) if v.target else None,
                    money(v.trailing_distance) if v.trailing_distance else None,
                    "PENDING",
                    v.strategy,
                    at,
                    v.request_key,
                ),
            )
            return dict(
                c.execute(
                    "SELECT * FROM paper_orders WHERE id=?", (cur.lastrowid,)
                ).fetchone()
            )

    def cancel_paper_order(self, account_id, order_id):
        with self.db.connect(write=True) as c:
            cur = c.execute(
                "UPDATE paper_orders SET status='CANCELLED' WHERE id=? AND account_id=? AND status='PENDING'",
                (order_id, account_id),
            )
            if not cur.rowcount:
                raise DomainError("Only pending paper orders can be cancelled")

    def modify_paper_order(self, account_id, order_id, limit_price):
        with self.db.connect(write=True) as c:
            o = c.execute(
                "SELECT o.*,i.tick_paise FROM paper_orders o JOIN instruments i ON i.id=o.instrument_id WHERE o.id=? AND account_id=?",
                (order_id, account_id),
            ).fetchone()
            if not o or o["status"] != "PENDING" or o["order_type"] != "LIMIT":
                raise DomainError("Only pending limit orders can be modified")
            p = money(limit_price)
            if p <= 0 or p % o["tick_paise"]:
                raise DomainError("Invalid limit tick")
            c.execute("UPDATE paper_orders SET limit_paise=? WHERE id=?", (p, order_id))

    def _reject(self, c, a, o, reason, at):
        c.execute(
            "UPDATE paper_orders SET status='REJECTED',reason=? WHERE id=?",
            (reason, o["id"]),
        )
        c.execute(
            "INSERT INTO risk_events(account_id,timestamp,reason) VALUES(?,?,?)",
            (a["id"], at, reason),
        )

    def execute_observation(
        self, account_id, instrument_id, price, at, source_ref, marks
    ):
        """Internal service boundary: only validated stored candle opens or provider observations."""
        if not self.reconcile(account_id)["ok"]:
            return
        with self.db.connect(write=True) as c:
            a = self.account(c, account_id)
            if a["halted"]:
                return
            inst = dict(
                c.execute(
                    "SELECT * FROM instruments WHERE id=?", (instrument_id,)
                ).fetchone()
            )
            pending = [
                dict(x)
                for x in c.execute(
                    "SELECT * FROM paper_orders WHERE account_id=? AND instrument_id=? AND status='PENDING' AND created_at<=? ORDER BY id",
                    (account_id, instrument_id, at),
                )
            ]
            for o in pending:
                a = self.account(c, account_id)
                if o["order_type"] == "LIMIT" and (
                    (o["side"] == "BUY" and price > o["limit_paise"])
                    or (o["side"] == "SELL" and price < o["limit_paise"])
                ):
                    continue
                positions = [
                    dict(x)
                    for x in c.execute(
                        "SELECT * FROM paper_positions WHERE account_id=?",
                        (account_id,),
                    )
                ]
                pos = next(
                    (p for p in positions if p["instrument_id"] == instrument_id), None
                )
                costs = json.loads(a["cost_json"])
                risk = json.loads(a["risk_json"])
                qty = o["quantity"]
                gross = qty * price
                fee = charges(gross, o["side"], costs)
                try:
                    if (
                        a["mode"] == "LIVE"
                        and o["request_key"].startswith("auto:")
                        and (":daily:" in o["request_key"] or o["request_key"].startswith("auto:scan:"))
                        and (timestamp(at) - timestamp(o["created_at"])).total_seconds()
                        > 60
                    ):
                        raise DomainError(
                            "Daily signal order expired after 60 seconds without a fresh fill"
                        )
                    if price <= 0:
                        raise DomainError(
                            "TRADE NOT EXECUTED - VALID MARKET PRICE UNAVAILABLE"
                        )
                    if price % inst["tick_paise"]:
                        raise DomainError(
                            "Observed price is not aligned to registered contract tick size"
                        )
                    if inst["expiry"] and day(at) > inst["expiry"]:
                        raise DomainError(
                            "Expired contract: settlement evidence required, execution halted"
                        )
                    if o["side"] == "BUY":
                        from .derivatives import exposure

                        if exposure(c, account_id):
                            raise DomainError(
                                "Close atomic baskets before opening standalone positions"
                            )
                        equity = (
                            a["cash_balance"]
                            + sum(
                                marks[p["instrument_id"]] * p["quantity"]
                                for p in positions
                            )
                            if all(p["instrument_id"] in marks for p in positions)
                            else None
                        )
                        check_entry(
                            a,
                            positions,
                            [
                                dict(x)
                                for x in c.execute(
                                    "SELECT * FROM paper_orders WHERE account_id=?",
                                    (account_id,),
                                )
                            ],
                            [
                                dict(x)
                                for x in c.execute(
                                    "SELECT * FROM account_ledger WHERE account_id=?",
                                    (account_id,),
                                )
                            ],
                            price,
                            qty,
                            o["stop_paise"],
                            inst,
                            equity,
                            risk,
                            costs,
                            at,
                        )
                        if o["target_paise"] is not None and o["target_paise"] <= price:
                            raise DomainError("Target is not above observed entry")
                        balance = a["cash_balance"] - gross - fee
                        realized = 0
                        c.execute(
                            "INSERT INTO paper_positions(account_id,instrument_id,quantity,cost_basis,entry_price,entry_fees,stop_paise,target_paise,trail_paise,high_water,opened_at,strategy) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                account_id,
                                instrument_id,
                                qty,
                                gross,
                                price,
                                fee,
                                o["stop_paise"],
                                o["target_paise"],
                                o["trail_paise"],
                                price,
                                at,
                                o["strategy"],
                            ),
                        )
                    else:
                        if not pos or qty > pos["quantity"]:
                            raise DomainError(
                                "Naked shorting disabled; insufficient paper position"
                            )
                        basis = (
                            pos["cost_basis"]
                            if qty == pos["quantity"]
                            else pos["cost_basis"] * qty // pos["quantity"]
                        )
                        entry_fee = (
                            pos["entry_fees"]
                            if qty == pos["quantity"]
                            else pos["entry_fees"] * qty // pos["quantity"]
                        )
                        realized = gross - basis
                        balance = a["cash_balance"] + gross - fee
                        cur = c.execute(
                            "INSERT INTO paper_trades(account_id,instrument_id,entry_time,exit_time,entry_price,exit_price,quantity,gross_pnl,fees,net_pnl,strategy,order_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                account_id,
                                instrument_id,
                                pos["opened_at"],
                                at,
                                pos["entry_price"],
                                price,
                                qty,
                                realized,
                                entry_fee + fee,
                                realized - entry_fee - fee,
                                pos["strategy"],
                                o["id"],
                            ),
                        )
                        c.execute(
                            "INSERT INTO journal(trade_id) VALUES(?)", (cur.lastrowid,)
                        )
                        if qty == pos["quantity"]:
                            c.execute(
                                "DELETE FROM paper_positions WHERE id=?", (pos["id"],)
                            )
                        else:
                            c.execute(
                                "UPDATE paper_positions SET quantity=quantity-?,cost_basis=cost_basis-?,entry_fees=entry_fees-? WHERE id=?",
                                (qty, basis, entry_fee, pos["id"]),
                            )
                    c.execute(
                        "UPDATE paper_accounts SET cash_balance=?,realized_pnl=realized_pnl+?,costs=costs+? WHERE id=?",
                        (balance, realized, fee, account_id),
                    )
                    c.execute(
                        "UPDATE paper_orders SET status='FILLED',filled_at=?,price=?,fees=?,source_ref=? WHERE id=?",
                        (at, price, fee, source_ref, o["id"]),
                    )
                    slip = charges(
                        gross, o["side"], {"slippage_bps": costs["slippage_bps"]}
                    )
                    append_event(
                        c,
                        account_id,
                        at,
                        "PAPER_" + o["side"],
                        a["cash_balance"],
                        balance,
                        f"order:{o['id']}",
                        inst["symbol"],
                        qty,
                        price,
                        gross,
                        fee,
                        realized,
                        source_ref,
                        slip,
                    )
                except DomainError as e:
                    self._reject(c, a, o, str(e), at)

    def protective_orders(self, account_id, inst, price, at):
        with self.db.connect(write=True) as c:
            pos = c.execute(
                "SELECT * FROM paper_positions WHERE account_id=? AND instrument_id=?",
                (account_id, inst),
            ).fetchone()
            if not pos:
                return
            reason = (
                "stop-loss"
                if price <= pos["stop_paise"]
                else (
                    "target"
                    if pos["target_paise"] and price >= pos["target_paise"]
                    else None
                )
            )
            if reason:
                c.execute(
                    "UPDATE paper_orders SET status='CANCELLED',reason='Protective exit supersedes pending orders' WHERE account_id=? AND instrument_id=? AND status='PENDING'",
                    (account_id, inst),
                )
                c.execute(
                    "INSERT OR IGNORE INTO paper_orders(account_id,instrument_id,side,quantity,order_type,status,strategy,created_at,request_key) VALUES(?,?,'SELL',?,'MARKET','PENDING',?,?,?)",
                    (
                        account_id,
                        inst,
                        pos["quantity"],
                        pos["strategy"],
                        at,
                        f'protect:{pos["id"]}:{at}',
                    ),
                )
                c.execute(
                    "INSERT INTO alerts(account_id,timestamp,kind,message) VALUES(?,?,?,?)",
                    (
                        account_id,
                        at,
                        reason,
                        "Paper protective exit triggered at observed price",
                    ),
                )

    def advance_replay(self, account_id):
        with self.db.connect() as c:
            a = self.account(c, account_id)
            if a["mode"] != "REPLAY":
                raise DomainError("Not a replay account")
            times = [
                x[0]
                for x in c.execute(
                    "SELECT DISTINCT timestamp FROM candles WHERE dataset_id=? ORDER BY timestamp",
                    (a["dataset_id"],),
                )
            ]
            cursor = a["cursor"] + 1
            if cursor >= len(times):
                return {"finished": True, "snapshot": self.snapshot(account_id)}
            rows = [
                dict(x)
                for x in c.execute(
                    "SELECT * FROM candles WHERE dataset_id=? AND timestamp=? ORDER BY instrument_id",
                    (a["dataset_id"], times[cursor]),
                )
            ]
        marks = {r["instrument_id"]: r["open"] for r in rows}
        from .derivatives import DerivativeBroker

        DerivativeBroker(self.db).process(account_id, rows)
        for r in rows:
            self.protective_orders(
                account_id, r["instrument_id"], r["open"], r["timestamp"]
            )
            self.execute_observation(
                account_id,
                r["instrument_id"],
                r["open"],
                r["timestamp"],
                f"candle:{r['id']}:open",
                marks,
            )
        clock = max(r["available_at"] for r in rows)
        with self.db.connect(write=True) as c:
            for r in rows:
                p = c.execute(
                    "SELECT * FROM paper_positions WHERE account_id=? AND instrument_id=?",
                    (account_id, r["instrument_id"]),
                ).fetchone()
                if p and p["trail_paise"]:
                    high = max(p["high_water"], r["close"])
                    c.execute(
                        "UPDATE paper_positions SET high_water=?,stop_paise=? WHERE id=?",
                        (high, max(p["stop_paise"], high - p["trail_paise"]), p["id"]),
                    )
            c.execute(
                "UPDATE paper_accounts SET cursor=?,clock=? WHERE id=?",
                (cursor, clock, account_id),
            )
        snapshot = self.snapshot(account_id)
        with self.db.connect(write=True) as c:
            c.execute(
                "INSERT OR IGNORE INTO snapshots(account_id,timestamp,payload) VALUES(?,?,?)",
                (account_id, clock, json.dumps(snapshot, sort_keys=True)),
            )
        return {
            "finished": False,
            "cursor": cursor,
            "total": len(times),
            "snapshot": snapshot,
        }
