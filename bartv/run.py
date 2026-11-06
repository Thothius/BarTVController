"""Entry point used by start.bat / uvicorn: python -m bartv.run [port]"""
import sys

import uvicorn

from .main import cfg

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Dashboard:  http://<this-pc-ip>:{port}/")
    print(f"TV pairing PIN (config.json): {cfg.pin}")
    uvicorn.run("bartv.main:app", host="0.0.0.0", port=port, log_level="info")
