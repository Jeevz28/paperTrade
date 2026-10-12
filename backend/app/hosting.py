"""Single-owner hosted access and durable-storage configuration."""
import base64
import binascii
import hmac
import os
from pathlib import Path
from urllib.parse import urlparse

from fastapi.responses import JSONResponse


class Hosting:
    def __init__(self, db_path=None):
        self.hosted = bool(os.getenv("RAILWAY_ENVIRONMENT_ID")) or os.getenv("PAPERLAB_HOSTED", "false").lower() == "true"
        self.lock = None
        self.db_path = db_path or os.getenv("PAPERLAB_DB", "data/trading.db")
        if not self.hosted:
            return
        domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
        self.origin = os.getenv("PAPERLAB_PUBLIC_URL", "").rstrip("/") or ("https://" + domain if domain else "")
        parsed = urlparse(self.origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise RuntimeError("Hosted mode requires an HTTPS PAPERLAB_PUBLIC_URL or RAILWAY_PUBLIC_DOMAIN")
        self.host = parsed.hostname
        self.username = os.getenv("PAPERLAB_USERNAME", "")
        self.password = os.getenv("PAPERLAB_PASSWORD", "")
        if not self.username or ":" in self.username or len(self.password) < 16:
            raise RuntimeError("Hosted mode requires PAPERLAB_USERNAME and PAPERLAB_PASSWORD (at least 16 characters)")
        mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "")
        if not mount or not Path(mount).is_dir():
            raise RuntimeError("Hosted mode requires an attached persistent volume")
        self.db_path = db_path or os.getenv("PAPERLAB_DB") or str(Path(mount) / "trading.db")
        if not Path(self.db_path).is_absolute() or not Path(self.db_path).resolve().is_relative_to(Path(mount).resolve()):
            raise RuntimeError("PAPERLAB_DB must be an absolute path inside the persistent volume")

    def acquire(self):
        if self.hosted:
            import fcntl
            self.lock = open(str(self.db_path) + ".owner.lock", "a")
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.lock.close()
                self.lock = None
                raise RuntimeError("Another PaperLab worker owns this database; run one instance and worker") from None

    def release(self):
        if self.lock:
            self.lock.close()
            self.lock = None

    def check(self, request):
        host = request.url.hostname
        origin = request.headers.get("origin")
        if not self.hosted:
            if host not in ("localhost", "127.0.0.1", "testserver") or (origin and urlparse(origin).hostname not in ("localhost", "127.0.0.1")):
                return JSONResponse({"detail": "Local-only application; untrusted host or origin"}, status_code=403)
            return None
        # The anonymous probe discloses no account/feed state; all other routes require credentials.
        if request.url.path == "/healthz" and request.method in ("GET", "HEAD") and host in (self.host, "healthcheck.railway.app"):
            return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})
        if host != self.host or (origin and origin != self.origin):
            return JSONResponse({"detail": "Untrusted host or origin"}, status_code=403)
        if request.url.scheme != "https":
            return JSONResponse({"detail": "HTTPS required"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
        try:
            scheme, token = request.headers.get("authorization", "").split(" ", 1)
            if scheme.lower() != "basic" or len(token) > 8192:
                raise ValueError()
            username, password = base64.b64decode(token, validate=True).decode("utf-8").split(":", 1)
            valid = hmac.compare_digest(username.encode(), self.username.encode()) & hmac.compare_digest(password.encode(), self.password.encode())
        except (ValueError, UnicodeError, binascii.Error):
            valid = False
        if not valid:
            return JSONResponse({"detail": "Sign in to PaperLab"}, status_code=401, headers={"WWW-Authenticate": 'Basic realm="PaperLab", charset="UTF-8"', "Cache-Control": "no-store"})
        return None
