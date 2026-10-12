import pandas as pd
import pytest
from backend.app.core import Policy, DomainError, now
from backend.app.data import import_data, analysis_frame, candles
from conftest import test_csv as fixture_csv


@pytest.mark.parametrize(
    "change",
    [
        {"open": 0},
        {"high": 1},
        {"volume": -1},
        {"timestamp": "2024-01-01"},
        {"timestamp": "not a timestamp"},
        {"symbol": "UNKNOWN"},
        {"close": float("inf")},
        {"volume": 1.5},
    ],
)
def test_invalid_rejected_atomically(db, instrument, change):
    import io

    f = pd.read_csv(io.BytesIO(fixture_csv())).astype(object)
    f.loc[0, list(change)] = list(change.values())
    with pytest.raises(DomainError):
        import_data(
            db,
            f.to_csv(index=False).encode(),
            "TEST.csv",
            "5m",
            "TEST DATA ONLY",
            Policy(),
        )
    assert not db.rows("SELECT * FROM candles")
    assert db.rows("SELECT * FROM data_quality")[0]["status"] == "REJECTED"


def test_duplicate_rejected(db, instrument, dataset):
    with pytest.raises(DomainError):
        import_data(db, fixture_csv(), "TEST.csv", "5m", "TEST DATA ONLY", Policy())
    assert len(db.rows("SELECT * FROM candles")) == 5


def test_lag_gate():
    with pytest.raises(DomainError):
        Policy().check(now().isoformat())
    with pytest.raises(DomainError):
        Policy().require_live()
    with pytest.raises(DomainError):
        Policy("licensed_private", "")


def test_parquet(db, instrument):
    import io

    f = pd.read_csv(io.BytesIO(fixture_csv()))
    b = io.BytesIO()
    f.to_parquet(b, index=False)
    r = import_data(db, b.getvalue(), "TEST.parquet", "5m", "TEST DATA ONLY", Policy())
    assert r["rows"] == 5


def test_as_of_excludes_future(db, instrument, dataset):
    rows = candles(db, instrument, dataset_id=dataset)
    past = candles(db, instrument, dataset_id=dataset, as_of=rows[0]["available_at"])
    assert len(past) == 1
