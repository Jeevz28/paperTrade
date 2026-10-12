"""Evidence-driven paper delivery. Net obligations only within a single basket."""

import json
from .core import DomainError
from .risk import day
from .activity import record


def plan(positions, instruments, evidence):
    derivatives = [
        p for p in positions if instruments[p["instrument_id"]]["kind"] != "SPOT"
    ]
    if not derivatives:
        raise DomainError("No derivative delivery positions")
    underlyings = {evidence[p["instrument_id"]]["underlying_id"] for p in derivatives}
    refs = {evidence[p["instrument_id"]]["reference_price"] for p in derivatives}
    if len(underlyings) != 1 or len(refs) != 1:
        raise DomainError(
            "Basket evidence must agree on underlying and reference price"
        )
    uid = underlyings.pop()
    reference = refs.pop()
    cash = realized = fees = delta = 0
    remaining = []
    for p in derivatives:
        inst = instruments[p["instrument_id"]]
        e = evidence[p["instrument_id"]]
        q = p["signed_quantity"]
        if inst["settlement"] != "PHYSICAL":
            raise DomainError(
                "Delivery basket cannot mix cash and physical derivatives"
            )
        if inst["kind"] == "FUTURE":
            qty = q
            consideration = reference
            variation = (reference - p["basis_price"]) * q
            cash += variation
            realized += variation
        else:
            strike = inst["strike_paise"]
            itm = reference > strike if inst["kind"] == "CALL" else reference < strike
            qty = (q if inst["kind"] == "CALL" else -q) if itm else 0
            consideration = strike
            intrinsic = abs(reference - strike) if itm else 0
            realized += intrinsic * q - p["book_cost"]
        cash -= consideration * qty
        delta += qty
        fees += e["fees"] * (abs(q) // inst["lot_size"])
    stock = next((p for p in positions if p["instrument_id"] == uid), None)
    other = [
        p
        for p in positions
        if instruments[p["instrument_id"]]["kind"] == "SPOT"
        and p["instrument_id"] != uid
    ]
    if other:
        raise DomainError("Unmatched basket stock")
    oldq = stock["signed_quantity"] if stock else 0
    oldbook = stock["book_cost"] if stock else 0
    newq = oldq + delta
    if newq < 0:
        raise DomainError("BLOCKED: insufficient shares for physical delivery")
    if delta < 0:
        removed = oldbook * (-delta) // oldq
        newbook = oldbook - removed
        realized += reference * (-delta) - removed
    else:
        newbook = oldbook + reference * delta
    if newq:
        remaining = [
            dict(
                instrument_id=uid,
                signed_quantity=newq,
                basis_price=newbook // newq,
                book_cost=newbook,
                margin=0,
                entry_fees=0,
                last_settlement_id=None,
            )
        ]
    return dict(
        positions=remaining,
        gross=cash,
        realized=realized,
        fees=fees,
        share_change=delta,
        underlying_id=uid,
        reference_price=reference,
    )


def deliver(db, account_id):
    from .paper import PaperBroker
    from .derivatives import DerivativeBroker, exposure

    broker = PaperBroker(db)
    derivative = DerivativeBroker(db)
    if not broker.reconcile(account_id)["ok"]:
        raise DomainError("Accounting error")
    applied = []
    blocked = []
    with db.connect(write=True) as c:
        a = broker.account(c, account_id)
        if a["halted"] or a["mode"] != "REPLAY" or not a["clock"]:
            raise DomainError("Active replay clock required")
        groups = {p["basket_id"] for p in exposure(c, account_id)}
        for bid in groups:
            b = dict(c.execute("SELECT * FROM baskets WHERE id=?", (bid,)).fetchone())
            positions = [
                dict(p)
                for p in c.execute(
                    "SELECT * FROM basket_positions WHERE basket_id=?", (bid,)
                )
            ]
            instruments = {
                p["instrument_id"]: dict(
                    c.execute(
                        "SELECT * FROM instruments WHERE id=?", (p["instrument_id"],)
                    ).fetchone()
                )
                for p in positions
            }
            derivs = [
                p
                for p in positions
                if instruments[p["instrument_id"]]["kind"] != "SPOT"
            ]
            if not derivs or not any(
                instruments[p["instrument_id"]]["settlement"] == "PHYSICAL"
                for p in derivs
            ):
                continue
            evidence = {}
            for p in derivs:
                e = c.execute(
                    "SELECT * FROM delivery_evidence WHERE instrument_id=? AND available_at<=? AND delivery_at<=? ORDER BY effective_at DESC LIMIT 1",
                    (p["instrument_id"], a["clock"], a["clock"]),
                ).fetchone()
                if (
                    e
                    and e["effective_at"] >= b["opened_at"]
                    and day(e["effective_at"])
                    >= instruments[p["instrument_id"]]["expiry"]
                ):
                    evidence[p["instrument_id"]] = dict(e)
            if len(evidence) != len(derivs):
                continue
            try:
                result = plan(positions, instruments, evidence)
                a = broker.account(c, account_id)
                if a["cash_balance"] + result["gross"] - result["fees"] < 0:
                    raise DomainError("BLOCKED: insufficient cash for delivery")
            except DomainError as err:
                details = json.dumps(
                    {
                        "reason": str(err),
                        "evidence_ids": [e["id"] for e in evidence.values()],
                    },
                    sort_keys=True,
                )
                prior = c.execute(
                    "SELECT details FROM delivery_obligations WHERE basket_id=? ORDER BY id DESC LIMIT 1",
                    (bid,),
                ).fetchone()
                if not prior or prior[0] != details:
                    c.execute(
                        "INSERT INTO delivery_obligations(basket_id,timestamp,status,details) VALUES(?,?,?,?)",
                        (bid, a["clock"], "BLOCKED", details),
                    )
                blocked.append({"basket_id": bid, "reason": str(err)})
                continue
            payload = {"evidence_ids": [e["id"] for e in evidence.values()], **result}
            derivative._event(
                c,
                a,
                b,
                a["clock"],
                "BASKET_DELIVERY",
                result["gross"] - result["fees"],
                result["realized"],
                result["fees"],
                payload,
            )
            c.execute("DELETE FROM basket_positions WHERE basket_id=?", (bid,))
            for p in result["positions"]:
                c.execute(
                    "INSERT INTO basket_positions(basket_id,instrument_id,signed_quantity,basis_price,book_cost,margin,entry_fees) VALUES(?,?,?,?,?,?,?)",
                    (
                        bid,
                        p["instrument_id"],
                        p["signed_quantity"],
                        p["basis_price"],
                        p["book_cost"],
                        0,
                        0,
                    ),
                )
            status = "OPEN" if result["positions"] else "CLOSED"
            c.execute("UPDATE baskets SET status=? WHERE id=?", (status, bid))
            if status == "CLOSED":
                derivative._closed_trade(c, b, a["clock"])
            c.execute(
                "INSERT INTO delivery_obligations(basket_id,timestamp,status,details) VALUES(?,?,?,?)",
                (bid, a["clock"], "DELIVERED", json.dumps(payload)),
            )
            applied.append(bid)
    for bid in applied:
        record(db, "delivery", "DELIVERED", {"basket_id": bid}, account_id)
    for block in blocked:
        record(db, "delivery", "BLOCKED", block, account_id)
    return {"delivered": applied, "blocked": blocked}
