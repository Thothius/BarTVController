import json
import logging

from fastapi import WebSocket

log = logging.getLogger("bartv.hub")


class DisplayHub:
    """Tracks /display kiosk pages connected over WebSocket."""

    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def broadcast(self, event: dict):
        msg = json.dumps(event)
        for ws in list(self.clients):
            try:
                await ws.send_text(msg)
            except Exception:
                self.clients.discard(ws)
