"""Isolated replay of timestamped multi-leg tickets using the production paper engine."""

import json
import sqlite3
import tempfile
from pathlib import Path
from .core import DomainError, now, timestamp
from .database import Database
from .paper import PaperBroker
from .derivatives import DerivativeBroker
from .delivery import deliver
from .activity import record


def run_baskets(db, config, progress=lambda n: None, cancelled=lambda: False):
    with tempfile.TemporaryDirectory(prefix="paperlab-basket-") as directory:
        path = Path(directory) / "isolated.sqlite"
        with db.connect() as source, sqlite3.connect(path) as target:
            source.backup(target)
        isolated = Database(path)
        b = PaperBroker(isolated)
        d = DerivativeBroker(isolated)
        account = b.create_account(
            {
                "name": "Isolated basket backtest",
                "dataset_id": config["dataset_id"],
                "initial_capital": config["initial_capital"],
                "risk": config["risk"],
                "costs": config["costs"],
            }
        )
        total = isolated.rows(
            "SELECT COUNT(DISTINCT timestamp) AS n FROM candles WHERE dataset_id=?",
            (config["dataset_id"],),
        )[0]["n"]
        if not 2 <= total <= 2000:
            raise DomainError(
                "Basket runs require 2–2000 replay timestamps; split larger datasets"
            )
        scheduled = {}
        equity = []
        for index in range(total):
            if cancelled():
                return {"cancelled": True}
            a = b.snapshot(account)
            if a["clock"]:
                for j, ticket in enumerate(config["tickets"]):
                    if j not in scheduled and timestamp(a["clock"]) >= timestamp(
                        ticket["enter_after"]
                    ):
                        scheduled[j] = d.submit(
                            account,
                            ticket["strategy"],
                            ticket["legs"],
                            f"backtest-ticket:{j}",
                            (
                                round(ticket["stop_loss"] * 100)
                                if ticket["stop_loss"]
                                else None
                            ),
                        )["id"]
                    if j in scheduled and timestamp(a["clock"]) >= timestamp(
                        ticket["exit_after"]
                    ):
                        row = isolated.rows(
                            "SELECT status FROM baskets WHERE id=?", (scheduled[j],)
                        )[0]
                        if row["status"] == "OPEN":
                            d.request_close(account, scheduled[j])
                        elif row["status"] == "PENDING":
                            with isolated.connect(write=True) as c:
                                c.execute(
                                    "UPDATE baskets SET status='CANCELLED',reason='Exit horizon reached before synchronized entry' WHERE id=?",
                                    (scheduled[j],),
                                )
            b.advance_replay(account)
            if config.get("apply_delivery"):
                deliver(isolated, account)
            if config.get("apply_cash_settlement"):
                d.settle(account, skip_physical_final=True)
            s = b.snapshot(account)
            equity.append(
                {
                    "timestamp": s["clock"],
                    "equity_paise": s["equity_paise"],
                    "cash_paise": s["cash_balance"],
                }
            )
            if s["halted"]:
                raise DomainError("Backtest accounting halted")
            if index % 10 == 0:
                progress(int((index + 1) * 100 / total))
        final = b.snapshot(account)
        return {
            "cancelled": False,
            "version": "0.3.0",
            "dataset": isolated.rows(
                "SELECT id,sha256,source,timeframe FROM datasets WHERE id=?",
                (config["dataset_id"],),
            )[0],
            "initial_capital_paise": final["initial_capital"],
            "final_equity_paise": final["equity_paise"],
            "net_pnl_paise": (
                final["equity_paise"] - final["initial_capital"]
                if final["equity_paise"] is not None
                else None
            ),
            "equity": equity,
            "baskets": isolated.rows(
                "SELECT * FROM baskets WHERE account_id=?", (account,)
            ),
            "trades": isolated.rows(
                "SELECT t.* FROM basket_trades t JOIN baskets b ON b.id=t.basket_id WHERE b.account_id=?",
                (account,),
            ),
            "ledger": isolated.rows(
                "SELECT * FROM account_ledger WHERE account_id=?", (account,)
            ),
            "open_positions": final["basket_positions"],
            "reconciliation": final["reconciliation"],
            "assumptions": "Observed synchronized opens; no forced final close or invented missing prices. Unclosed exposure is retained.",
        }


class BasketJobs:
    def __init__(self, db):
        from concurrent.futures import ThreadPoolExecutor

        self.db = db
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="basket-research"
        )
        with db.connect(write=True) as c:
            c.execute(
                "UPDATE basket_backtests SET status='INTERRUPTED',error='Process restarted; submit a new run' WHERE status IN ('QUEUED','RUNNING','CANCEL_REQUESTED')"
            )

    def submit(self, config):
        with self.db.connect(write=True) as c:
            if (
                c.execute(
                    "SELECT COUNT(*) FROM basket_backtests WHERE status IN ('QUEUED','RUNNING')"
                ).fetchone()[0]
                >= 3
            ):
                raise DomainError("Backtest queue full")
            cur = c.execute(
                "INSERT INTO basket_backtests(created_at,status,parameters) VALUES(?,'QUEUED',?)",
                (now().isoformat(), json.dumps(config)),
            )
            id = cur.lastrowid
        record(self.db, "backtest", "QUEUED", {"job_id": id})
        self.executor.submit(self._run, id, config)
        return {"id": id, "status": "QUEUED"}

    def _run(self, id, config):
        def cancelled():
            return (
                self.db.rows("SELECT status FROM basket_backtests WHERE id=?", (id,))[
                    0
                ]["status"]
                == "CANCEL_REQUESTED"
            )

        def progress(n):
            with self.db.connect(write=True) as c:
                c.execute("UPDATE basket_backtests SET progress=? WHERE id=?", (n, id))

        try:
            if cancelled():
                result = {"cancelled": True}
            else:
                with self.db.connect(write=True) as c:
                    changed = c.execute(
                        "UPDATE basket_backtests SET status='RUNNING' WHERE id=? AND status='QUEUED'",
                        (id,),
                    ).rowcount
                result = (
                    run_baskets(self.db, config, progress, cancelled)
                    if changed
                    else {"cancelled": True}
                )
            status = "CANCELLED" if result["cancelled"] else "COMPLETED"
            with self.db.connect(write=True) as c:
                c.execute(
                    "UPDATE basket_backtests SET status=?,progress=?,result=? WHERE id=?",
                    (status, 100, json.dumps(result), id),
                )
            record(self.db, "backtest", status, {"job_id": id})
        except Exception as e:
            message = str(e) if isinstance(e, DomainError) else type(e).__name__
            with self.db.connect(write=True) as c:
                c.execute(
                    "UPDATE basket_backtests SET status='FAILED',error=? WHERE id=?",
                    (message, id),
                )
            record(self.db, "backtest", "FAILED", {"job_id": id, "reason": message})

    def shutdown(self):
        with self.db.connect(write=True) as c:
            c.execute(
                "UPDATE basket_backtests SET status='CANCEL_REQUESTED' WHERE status IN ('QUEUED','RUNNING')"
            )
        self.executor.shutdown(wait=True, cancel_futures=True)
