import os
import pytest
import httpx
from backend.app.providers import UpstoxProvider
from backend.app.core import Policy, DomainError
from backend.app.ai import LocalQwenProvider, NoAIProvider


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("UPSTOX_ENABLED", "true")
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "TEST_TOKEN_NOT_REAL")


@pytest.mark.parametrize(
    "path",
    [
        "/v2/order/place",
        "/v2/order/modify",
        "/v2/order/cancel",
        "/v2/user/get-funds-and-margin",
        "/v2/portfolio/long-term-holdings",
        "https://evil.example",
        "/v3/historical-candle/../../order/place",
        "/v2/market-quote/quotes/../order/place",
    ],
)
def test_deny_nondata(enabled, path):
    def fail(request):
        raise AssertionError("No network request permitted")

    p = UpstoxProvider(Policy(), httpx.MockTransport(fail))
    with pytest.raises(DomainError):
        p._get(path)


def test_history_only_get(enabled):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "candles": [
                        ["2024-01-01T09:15:00+05:30", 100, 101, 99, 100, 1000, 0]
                    ]
                },
            },
        )

    p = UpstoxProvider(Policy(), httpx.MockTransport(respond))
    assert p.get_candles("NSE_EQ|TEST", "2024-01-01", "2024-01-01")[0]["close"] == 100
    assert all(r.method == "GET" and r.url.host == "api.upstox.com" for r in requests)


def test_stale_live_quote(enabled):
    def respond(request):
        data = (
            {"status": "NORMAL_OPEN"}
            if "/market/status/" in request.url.path
            else {
                "TEST": {
                    "instrument_token": "NSE_EQ|TEST",
                    "last_price": 100,
                    "last_trade_time": "1000",
                    "timestamp": "2024-01-01T10:00:00+05:30",
                }
            }
        )
        return httpx.Response(200, json={"status": "success", "data": data})

    p = UpstoxProvider(
        Policy("licensed_private", "TEST ONLY permission fixture"),
        httpx.MockTransport(respond),
    )
    with pytest.raises(DomainError, match="stale"):
        p.get_latest_quote("NSE_EQ|TEST")


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_provider_failure_no_redirect(enabled, status):
    p = UpstoxProvider(
        Policy(),
        httpx.MockTransport(
            lambda r: httpx.Response(
                status, headers={"Location": "https://evil.example"}
            )
        ),
    )
    with pytest.raises(DomainError):
        p.get_candles("NSE_EQ|TEST", "2024-01-01", "2024-01-01")


def test_ai_is_optional(db):
    assert NoAIProvider().analyze("test", {})["status"] == "disabled"
    p = LocalQwenProvider(db, httpx.MockTransport(lambda r: httpx.Response(503)))
    assert p.analyze("TEST ONLY", {})["status"] == "unavailable"
    assert not db.rows("SELECT * FROM paper_orders")
