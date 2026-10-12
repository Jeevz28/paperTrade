"""One worker: local desktop or authenticated Railway hosting."""

import argparse
import socket
import os
import uvicorn
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    hosted = bool(os.getenv("RAILWAY_ENVIRONMENT_ID")) or os.getenv("PAPERLAB_HOSTED", "false").lower() == "true"
    if hosted:
        uvicorn.run("backend.app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), workers=1, proxy_headers=True, forwarded_allow_ips="*")
        raise SystemExit(0)
    port = args.port
    for candidate in range(port, port + 20):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            port = candidate
            break
    print(f"PaperLab: http://127.0.0.1:{port} (local only)", flush=True)
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=port, workers=1)
