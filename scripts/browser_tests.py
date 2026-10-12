"""Run browser tests against an isolated temporary database and local server."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="paperlab-ui-") as directory:
    env = {**os.environ, "PAPERLAB_DB": str(Path(directory) / "test.sqlite")}
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        cwd=root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for attempt in range(100):
            if server.poll() is not None:
                raise RuntimeError("Test server failed; port 8000 must be free")
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:8000/api/health", timeout=0.5
                ):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Test server not ready")
        sys.exit(
            subprocess.run(
                [shutil.which("npm") or "npm", "--prefix", "frontend", "test"],
                cwd=root,
                env=env,
            ).returncode
        )
    finally:
        server.terminate()
        server.wait(timeout=10)
