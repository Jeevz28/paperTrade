import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.hosting import Hosting


@pytest.fixture
def hosted(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPERLAB_HOSTED", "true")
    monkeypatch.setenv("PAPERLAB_PUBLIC_URL", "https://paper.example.com")
    monkeypatch.setenv("PAPERLAB_USERNAME", "owner")
    monkeypatch.setenv("PAPERLAB_PASSWORD", "test-password-at-least-16")
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    monkeypatch.delenv("PAPERLAB_DB", raising=False)
    return tmp_path


def test_cloud_requires_auth_for_page_api_and_assets(hosted):
    app = create_app(autostart=False)
    with TestClient(app, base_url="https://paper.example.com") as c:
        for path in ("/", "/api/health", "/api/fno-auto", "/docs", "/assets/example.js"):
            assert c.get(path).status_code == 401
        assert c.get("/api/health", auth=("owner", "wrong")).status_code == 401
        assert c.get("/api/health", headers={"Authorization": "Basic invalid"}).status_code == 401
        assert c.get("/api/health", auth=("owner", "test-password-at-least-16")).status_code == 200
        assert c.get("/", auth=("owner", "test-password-at-least-16")).status_code == 200


def test_cloud_blocks_host_origin_http_and_missing_marker(hosted):
    with TestClient(create_app(autostart=False), base_url="https://paper.example.com") as c:
        c.auth = ("owner", "test-password-at-least-16")
        for headers in ({"Host": "evil.example"}, {"Origin": "https://evil.example"}, {"Origin": "https://paper.example.com:9999"}, {"Sec-Fetch-Site": "cross-site"}):
            assert c.get("/api/health", headers=headers).status_code == 403
        assert c.get("http://paper.example.com/api/health").status_code == 403
        assert c.post("/api/fno-auto/1/pause").status_code == 403
        assert c.get("/api/health", headers={"Origin": "https://paper.example.com"}).status_code == 200


def test_anonymous_healthcheck_has_no_account_details(hosted):
    with TestClient(create_app(autostart=False)) as c:
        r = c.get("/healthz", headers={"Host": "healthcheck.railway.app"})
        assert r.status_code == 200 and r.json() == {"status": "ok"}
        assert c.get("/api/fno-auto", headers={"Host": "healthcheck.railway.app"}).status_code == 403
        assert c.get("/healthz", headers={"Host": "evil.example"}).status_code == 403


@pytest.mark.parametrize("key,value", [("PAPERLAB_PASSWORD", "short"), ("PAPERLAB_USERNAME", ""), ("PAPERLAB_PUBLIC_URL", "http://paper.example.com"), ("PAPERLAB_DB", "/tmp/outside.db"), ("RAILWAY_VOLUME_MOUNT_PATH", "")])
def test_hosting_fails_closed_on_bad_configuration(hosted, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError):
        Hosting()


def test_railway_autodetection_and_default_volume_path(hosted, monkeypatch):
    monkeypatch.delenv("PAPERLAB_HOSTED")
    monkeypatch.delenv("PAPERLAB_PUBLIC_URL")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "test-environment")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "paper.up.railway.app")
    h = Hosting()
    assert h.hosted and h.origin == "https://paper.up.railway.app"
    assert h.db_path == str(hosted / "trading.db")


def test_single_worker_lock_releases_after_shutdown(hosted):
    a, b = Hosting(), Hosting()
    a.acquire()
    try:
        with pytest.raises(RuntimeError, match="Another PaperLab"):
            b.acquire()
    finally:
        a.release()
    b.acquire()
    b.release()


def test_data_survives_app_restart(hosted):
    from backend.app.database import Database
    first = create_app(autostart=False)
    with TestClient(first):
        with first.state.db.connect(write=True) as db:
            db.execute("INSERT OR REPLACE INTO runtime_preferences VALUES ('paused', 'true')")
    second = create_app(autostart=False)
    with TestClient(second):
        assert second.state.db.rows("SELECT value FROM runtime_preferences WHERE name='paused'")[0]['value'] == 'true'
