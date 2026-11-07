import asyncio
import logging

from aiowebostv import WebOsClient
from aiowebostv.exceptions import WebOsTvPairError

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

    async def get_inputs(self):
        return [{"id": "HDMI_1"}, {"id": "HDMI_2"}]


class TVController:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = None
        self._lock = asyncio.Lock()
        self.last_inputs: list[str] = []

    def _new_client(self, client_key):
        cls = MockClient if self.cfg.mock_tv else WebOsClient
        return cls(self.cfg.tv_ip, client_key=client_key)

    async def _discover_inputs(self):
        """Best-effort fetch of the TV's input list for status/logging."""
        try:
            raw = await self.client.get_inputs()
            ids = []
            for item in raw or []:
                if isinstance(item, dict):
                    ids.append(item.get("id") or item.get("inputId") or "")
                elif isinstance(item, str):
                    ids.append(item)
            self.last_inputs = [i for i in ids if i]
        except Exception:
            log.debug("get_inputs failed", exc_info=True)

    async def _ensure(self):
        """Returns False when no client key is stored yet (not an error)."""
        if self.client and self.client.is_connected():
            return True
        if not self.cfg.client_key:
            return False
        self.client = self._new_client(self.cfg.client_key)
        try:
            await self.client.connect()
        except WebOsTvPairError:
            # TV rejected the stored key (e.g. after a firmware update).
            self.client = None
            self.cfg.client_key = None
            save_config(self.cfg)
            log.warning("Saved client key rejected by TV - pairing required again")
            raise RuntimeError("TV rejected the saved key - press 'Pair TV'")
        await self._discover_inputs()
        return True

    async def _call(self, method: str, *args):
        try:
            if not await self._ensure():
                raise RuntimeError("Pair the TV first")
            client = self.client
            return await getattr(client, method)(*args)
        except RuntimeError:
            raise
        except Exception:
            log.warning("TV call %s failed, reconnecting once", method, exc_info=True)
            await self.disconnect()
            if not await self._ensure():
                raise RuntimeError("Pair the TV first")
            return await getattr(self.client, method)(*args)

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
            await self._discover_inputs()
            missing = [i for i in (self.cfg.normal_input, self.cfg.signage_input)
                       if self.last_inputs and i not in self.last_inputs]
            if missing:
                log.warning("Configured inputs %s not in TV inputs %s - check config.json",
                            missing, self.last_inputs)
            return client.client_key

    async def disconnect(self):
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                log.debug("disconnect failed", exc_info=True)
        self.client = None
