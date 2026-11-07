"""Entry point used by start.bat / uvicorn: python -m bartv.run [port]"""
import socket
import sys
import webbrowser

import uvicorn

from .main import cfg


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no traffic is sent for UDP connect
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    if port_in_use(port):
        print(f"BarTV is already running on port {port} - opening the dashboard.")
        print("(Close the other BarTV window first if you want to restart it.)")
        webbrowser.open(f"http://localhost:{port}/")
        sys.exit(0)
    ip = lan_ip()
    print(f"Dashboard (this PC):  http://localhost:{port}/")
    print(f"Dashboard (phone):    http://{ip}:{port}/")
    print(f"Display kiosk:        http://{ip}:{port}/display")
    print(f"PIN (also in config.json): {cfg.pin}")
    uvicorn.run("bartv.main:app", host="0.0.0.0", port=port, log_level="info")
