import json
from datetime import date
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form
from pydantic import Field
from .schemas import Strict
from .bhavcopy import import_bhavcopy
from .core import DomainError
from .learning import profile


class StudyInput(Strict):
    account_id: int
    dataset_id: int
    instrument_id: int
    max_quantity: int = Field(default=1, ge=1, le=1000000)
    train_until: date | None = None


def register_learning(app, db, broker, policy, lock, stream, replay, jobs):
    router = APIRouter(prefix="/api")

    @router.post("/import/nse")
    def upload(file: UploadFile = File(...), source: str = Form(...)):
        if not (file.filename or "").lower().endswith(".csv"):
            raise DomainError("Upload the combined uncompressed NSE CSV")
        with lock:
            if (stream.accounts and stream.state != "STOPPED") or replay.state == "RUNNING":
                raise DomainError(
                    "Stop live paper streaming and background replay before a large import"
                )
            return import_bhavcopy(
                db, file.file, Path(file.filename).name, source, policy
            )

    @router.get("/learning/securities")
    def securities(dataset_id: int):
        return db.rows(
            "SELECT b.*,COUNT(c.id) AS bars FROM bhav_securities b JOIN candles c ON c.instrument_id=b.instrument_id AND c.dataset_id=b.dataset_id WHERE b.dataset_id=? GROUP BY b.instrument_id ORDER BY b.ticker",
            (dataset_id,),
        )

    @router.post("/learning")
    def learn(v: StudyInput):
        a = broker.snapshot(v.account_id)
        config = {
            "dataset_id": v.dataset_id,
            "instrument_id": v.instrument_id,
            "initial_capital_paise": a["initial_capital"],
            "risk": json.loads(a["risk_json"]),
            "costs": json.loads(a["cost_json"]),
            "max_quantity": v.max_quantity,
            "train_until": v.train_until.isoformat() if v.train_until else None,
        }
        return jobs.submit(config)

    @router.get("/learning")
    def status():
        profiles = []
        for row in db.rows(
            "SELECT * FROM learning_profiles ORDER BY id DESC LIMIT 100"
        ):
            p = json.loads(row.pop("payload"))
            profiles.append(
                {
                    **row,
                    "ticker": p["ticker"],
                    "isin": p["isin"],
                    "approved": p["approved"],
                    "winners": p["winners"],
                    "windows": p["windows"],
                }
            )
        return {
            "jobs": db.rows("SELECT * FROM learning_jobs ORDER BY id DESC LIMIT 100"),
            "profiles": profiles,
        }

    @router.get("/learning/profiles/{id}")
    def detail(id: int):
        return profile(db, id)

    app.include_router(router)
