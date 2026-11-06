"""CLI pairing: python -m bartv.pair  (accept the prompt on the TV with the remote)."""
import asyncio

from .config import load_config
from .tv import TVController


async def main():
    cfg = load_config()
    print(f"Pairing with TV at {cfg.tv_ip} - accept the prompt on the TV...")
    key = await TVController(cfg).pair()
    print(f"Paired. client_key saved to config.json ({key[:6]}...)")


if __name__ == "__main__":
    asyncio.run(main())
