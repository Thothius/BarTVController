import asyncio
import logging
from typing import Optional

from aiowebostv import WebOsClient

from .config import Config, save_config

log = logging.getLogger("bartv.tv")

PAIR_TIMEOUT_SECONDS = 60


class MockClient:
    """Stand-in for WebOsClient when no TV is available (mock_tv: true)."""

    def __init__(self, host, client_key=None):
        self.host = host
        self.client_key = client_key or "mock-client-key"
        self.calls: list[tuple] = []
        self._connected = False

    async def connect(self):
        self._connected = True

    async def disconnect(self):
        self._connected = False

    def is_connected(self):
        return self._connected

    async def set_input(self, input_id):
        self.calls.append(("set_input", input_id))

    async def set_volume(self, volume):
        self.calls.append(("set_volume", volume))

    async def set_mute(self, mute):
        self.calls.append(("set_mute", mute))


class TVController:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client: Optional[WebOsClient] = None
        self._lock = asyncio.Lock()

    def _new_client(self, client_key):
        cls = MockClient if self.cfg.mock_tv else WebOsClient
        return cls(self.cfg.tv_ip, client_key=client_key)

    async def _ensure(self):
        if self.client and self.client.is_connected():
            return self.client
        if not self.cfg.client_key:
            raise RuntimeError("TV not paired yet - use 'Pair TV' first")
        self.client = self._new_client(self.cfg.client_key)
        await self.client.connect()
        return self.client

    async def _call(self, method: str, *args):
        try:
            client = await self._ensure()
            return await getattr(client, method)(*args)
        except RuntimeError:
            raise
        except Exception:
            log.warning("TV call %s failed, reconnecting once", method, exc_info=True)
            await self.disconnect()
            client = await self._ensure()
            return await getattr(client, method)(*args)

    async def set_input(self, input_id: str):
        async with self._lock:
            await self._call("set_input", input_id)

    async def set_volume(self, level: int):
        async with self._lock:
            await self._call("set_volume", level)

    async def set_mute(self, mute: bool):
        async with self._lock:
            await self._call("set_mute", mute)

    async def pair(self) -> str:
        """Connect without a key; the TV shows an Allow prompt to accept on the remote."""
        async with self._lock:
            await self.disconnect()
            client = self._new_client(None)
            try:
                await asyncio.wait_for(client.connect(), PAIR_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                await client.disconnect()
                raise RuntimeError("Pairing timed out - accept the prompt on the TV")
            self.client = client
            self.cfg.client_key = client.client_key
            save_config(self.cfg)
            return client.client_key

    async def disconnect(self):
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                log.debug("disconnect failed", exc_info=True)
        self.client = None
