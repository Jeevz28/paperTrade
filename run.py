"""Run PaperLab on Railway or locally."""

import os
import uvicorn
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    host = "0.0.0.0" if os.environ.get("RAILWAY_ENVIRONMENT") else "127.0.0.1"

    print(f"PaperLab listening on {host}:{port}", flush=True)

    uvicorn.run(
        "backend.app.main:app",
        host=host,
        port=port,
        workers=1,
    )