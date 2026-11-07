import asyncio
import logging

from aiowebostv import WebOsClient
from aiowebostv.exceptions import WebOsTvPairError

from .config import Config, save_config

log = logging.getLogger("bartv.tv")

PAIR_TIMEOUT_SECONDS = 60

SSDP_ADDR = ("239.255.255.250", 1900)
SSDP_SEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 2\r\n"
    "ST: urn:lge-com:service:webos-second-screen:1\r\n\r\n"
).encode()


async def discover_tvs(timeout: float = 3.0) -> list[dict]:
    """Find LG webOS TVs via SSDP. Returns [{ip, name}]; empty if the network blocks multicast."""
    import socket

    found: dict[str, dict] = {}
    loop = asyncio.get_running_loop()

    class Proto(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            text = data.decode(errors="ignore")
            if "lge" not in text.lower() and "webos" not in text.lower():
                return
            name = ""
            for line in text.split("\r\n"):
                if line.lower().startswith("dlnadevicename.lge.com:"):
                    name = line.split(":", 1)[1].strip()
            found.setdefault(addr[0], {"ip": addr[0], "name": name or "LG webOS TV"})

    try:
        transport, _ = await loop.create_datagram_endpoint(
            Proto, local_addr=("0.0.0.0", 0), family=socket.AF_INET)
    except OSError:
        log.debug("SSDP socket failed", exc_info=True)
        return []
    try:
        for _ in range(3):
            transport.sendto(SSDP_SEARCH, SSDP_ADDR)
            await asyncio.sleep(timeout / 3)
    finally:
        transport.close()
    return list(found.values())


class MockClient:
    """Stand-in for WebOsClient when no TV is available (mock_tv: true)."""

    # Simulates the time a person takes to press Accept on the remote during pairing.
    PAIR_DELAY_SECONDS = 3.0

    def __init__(self, host, client_key=None):
        self.host = host
        self._pairing = client_key is None
        self.client_key = client_key or "mock-client-key"
        self.calls: list[tuple] = []
        self._connected = False

    async def connect(self):
        if self._pairing and self.PAIR_DELAY_SECONDS:
            await asyncio.sleep(self.PAIR_DELAY_SECONDS)
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
            except asyncio.CancelledError:
                try:
                    await client.disconnect()
                except Exception:
                    pass
                raise
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
