"""Standalone ad-break test: switch to signage input, wait, switch back.

Usage: python scripts/tv_control.py [seconds]
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bartv.config import load_config  # noqa: E402
from bartv.tv import TVController  # noqa: E402


async def main(seconds: float):
    cfg = load_config()
    tv = TVController(cfg)
    if not cfg.client_key:
        await tv.pair()
    await tv.set_input(cfg.signage_input)
    await asyncio.sleep(seconds)
    await tv.set_input(cfg.normal_input)
    await tv.disconnect()


if __name__ == "__main__":
    asyncio.run(main(float(sys.argv[1]) if len(sys.argv) > 1 else 15))
