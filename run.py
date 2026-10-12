"""One local process, one worker; no public binding or auto-funded accounts."""

import argparse
import socket
import uvicorn
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
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
