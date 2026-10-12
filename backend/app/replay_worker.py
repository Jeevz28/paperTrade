import threading
from .core import DomainError
from .automation import Automation
from .activity import record


def advance(db, broker, policy, account_id):
    a = broker.snapshot(account_id)
    if a["mode"] != "REPLAY" or a["halted"]:
        raise DomainError("Active replay account required")
    latest = db.rows(
        "SELECT MAX(available_at) AS at FROM candles WHERE dataset_id=?",
        (a["dataset_id"],),
    )[0]["at"]
    if not latest:
        raise DomainError("Empty replay dataset")
    policy.check(latest)
    before = db.rows(
        "SELECT COALESCE(MAX(event_id),0) AS id FROM account_ledger WHERE account_id=?",
        (account_id,),
    )[0]["id"]
    result = broker.advance_replay(account_id)
    if not result["finished"]:
        Automation(db).evaluate(account_id)
        for e in db.rows(
            "SELECT * FROM account_ledger WHERE account_id=? AND event_id>?",
            (account_id, before),
        ):
            record(
                db,
                "execution",
                e["event_type"],
                {
                    "ledger_id": e["event_id"],
                    "symbol": e["symbol"],
                    "quantity": e["quantity"],
                    "price_paise": e["price"],
                    "source": e["source"],
                },
                account_id,
            )
        record(
            db,
            "replay",
            "ADVANCED",
            {"clock": result["snapshot"]["clock"], "cursor": result["cursor"]},
            account_id,
        )
    return result


class ReplayWorker:
    def __init__(self, db, broker, policy, lock):
        self.db = db
        self.broker = broker
        self.policy = policy
        self.lock = lock
        self.thread = None
        self.event = threading.Event()
        self.account_id = None
        self.state = "STOPPED"
        self.error = None

    def status(self):
        with self.lock:
            snapshot = (
                self.broker.snapshot(self.account_id) if self.account_id else None
            )
            return {
                "state": self.state,
                "account_id": self.account_id,
                "error": self.error,
                "snapshot": snapshot,
            }

    def start(self, account_id, interval):
        if self.thread and self.thread.is_alive():
            raise DomainError("Stop the running replay worker first")
        a = self.broker.snapshot(account_id)
        if a["mode"] != "REPLAY" or a["halted"]:
            raise DomainError("Active replay account required")
        self.account_id = account_id
        self.event.clear()
        self.state = "RUNNING"
        self.error = None
        self.thread = threading.Thread(
            target=self._run, args=(interval,), daemon=True, name="paperlab-replay"
        )
        self.thread.start()

    def _run(self, interval):
        record(
            self.db,
            "replay",
            "WORKER_STARTED",
            {"interval_seconds": interval},
            self.account_id,
        )
        try:
            while not self.event.is_set():
                with self.lock:
                    if self.event.is_set():
                        break
                    result = advance(self.db, self.broker, self.policy, self.account_id)
                if result["finished"]:
                    self.state = "FINISHED"
                    break
                if self.event.wait(interval):
                    break
        except Exception as e:
            self.error = str(e) if isinstance(e, DomainError) else type(e).__name__
            self.state = "ERROR"
        finally:
            if self.state == "RUNNING":
                self.state = "STOPPED"
            record(
                self.db, "replay", self.state, {"error": self.error}, self.account_id
            )

    def stop(self):
        self.event.set()
        if self.thread:
            self.thread.join(timeout=20)
        if self.thread and self.thread.is_alive():
            self.state = "STOPPING"
        elif self.state == "RUNNING":
            self.state = "STOPPED"
