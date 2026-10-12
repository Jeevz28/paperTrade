from datetime import datetime, timezone
import httpx
import pytest
from backend.app.core import Policy, DomainError
from backend.app.providers import UpstoxProvider


def test_previous_indian_day_is_allowed_before_utc_midnight(monkeypatch):
    monkeypatch.setattr('backend.app.core.now', lambda: datetime(2026, 10, 12, 22, 8, tzinfo=timezone.utc))
    monkeypatch.setenv('UPSTOX_ENABLED', 'true')
    monkeypatch.setenv('UPSTOX_ACCESS_TOKEN', 'TEST_ONLY')
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'status': 'success', 'data': {'candles': []}})
    provider = UpstoxProvider(Policy('licensed_private', 'test permission'), httpx.MockTransport(respond))
    assert provider.get_candles('NSE_INDEX|Nifty 50', '2026-10-06', '2026-10-12', '1m') == []
    assert len(calls) == 1
    with pytest.raises(DomainError, match='cutoff'):
        provider.get_candles('NSE_INDEX|Nifty 50', '2026-10-13', '2026-10-13', '1m')
    assert len(calls) == 1
    provider.policy = Policy('education')
    with pytest.raises(DomainError, match='cutoff'):
        provider.get_candles('NSE_INDEX|Nifty 50', '2026-10-12', '2026-10-12', '1m')
