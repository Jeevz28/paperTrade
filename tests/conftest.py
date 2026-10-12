import io
import pandas as pd
import pytest
from backend.app.core import Policy
from backend.app.database import Database
from backend.app.data import create_instrument, import_data


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture
def instrument(db):
    return create_instrument(
        db,
        {
            "symbol": "TEST_DATA_ONLY",
            "metadata_source": "Explicit synthetic unit-test fixture",
            "tick_size": 0.01,
        },
    )


def test_csv(prices=(100, 100, 99, 100, 102), count=None):
    # Synthetic fixtures are exclusively for software tests, never installed as app data.
    values = list(prices)
    if count:
        values = [100 + ((i % 40) - 20) * 0.1 for i in range(count)]
    times = pd.date_range(
        "2024-01-02 09:15", periods=len(values), freq="5min", tz="Asia/Kolkata"
    )
    return (
        pd.DataFrame(
            [
                {
                    "timestamp": t.isoformat(),
                    "symbol": "TEST_DATA_ONLY",
                    "open": p,
                    "high": p + 1,
                    "low": p - 1,
                    "close": p,
                    "volume": 1000,
                }
                for t, p in zip(times, values)
            ]
        )
        .to_csv(index=False)
        .encode()
    )


@pytest.fixture
def dataset(db, instrument):
    return import_data(
        db, test_csv(), "TEST_DATA_ONLY.csv", "5m", "TEST DATA ONLY", Policy()
    )["id"]
