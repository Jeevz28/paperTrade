from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.core import Policy
from conftest import test_csv as fixture_csv


def test_local_api_workflow(tmp_path):
    app = create_app(tmp_path / "api.db", Policy())
    client = TestClient(app, headers={"x-paperlab": "local"})
    assert client.get("/api/health").json()["paper_only"]
    assert client.get("/api/candles?instrument_id=1").json()["candles"] == []
    i = client.post(
        "/api/instruments",
        json={
            "symbol": "TEST_DATA_ONLY",
            "metadata_source": "TEST DATA ONLY",
            "tick_size": 0.01,
        },
    ).json()["id"]
    d = client.post(
        "/api/import",
        files={"file": ("TEST_ONLY.csv", fixture_csv(), "text/csv")},
        data={"timeframe": "5m", "source": "TEST DATA ONLY"},
    ).json()["id"]
    a = client.post(
        "/api/accounts", json={"name": "TEST ONLY", "dataset_id": d}
    ).json()["id"]
    assert (
        client.get(f"/api/candles?instrument_id={i}&account_id={a}").json()["candles"]
        == []
    )
    assert client.post(f"/api/accounts/{a}/replay/step").status_code == 200
    assert (
        len(
            client.get(f"/api/candles?instrument_id={i}&account_id={a}").json()[
                "candles"
            ]
        )
        == 1
    )
    assert client.get(f"/api/accounts/{a}").json()["initial_capital"] == 100000
    assert client.delete(f"/api/datasets/{d}").status_code == 400
    assert (
        client.post("/api/accounts", json={"name": "LIVE", "mode": "LIVE"}).status_code
        == 400
    )


def test_security(tmp_path):
    c = TestClient(create_app(tmp_path / "security.db", Policy()))
    assert c.post("/api/accounts", json={"name": "bad"}).status_code == 403
    assert c.get("/api/health", headers={"Host": "evil.example"}).status_code == 403
    assert (
        c.get("/api/health", headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
