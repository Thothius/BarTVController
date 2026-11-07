import asyncio

from bartv.tv import discover_tvs


def test_discover_tvs_returns_list_when_multicast_blocked():
    assert asyncio.run(discover_tvs(timeout=0.1)) == []
