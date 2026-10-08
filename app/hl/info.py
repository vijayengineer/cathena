"""Async read-only client for Hyperliquid's /info endpoint."""

import httpx


class HLInfo:
    def __init__(self, base_url: str):
        self._c = httpx.AsyncClient(base_url=base_url, timeout=10)

    async def close(self) -> None:
        await self._c.aclose()

    async def _post(self, body: dict):
        r = await self._c.post("/info", json=body)
        r.raise_for_status()
        return r.json()

    async def outcome_meta(self) -> dict:
        return await self._post({"type": "outcomeMeta"})

    async def l2_book(self, coin: str) -> dict:
        return await self._post({"type": "l2Book", "coin": coin})

    async def meta_and_ctxs(self) -> list:
        return await self._post({"type": "metaAndAssetCtxs"})

    async def candles(self, coin: str, interval: str, start_ms: int, end_ms: int) -> list:
        return await self._post({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms}})

    async def clearinghouse(self, user: str) -> dict:
        return await self._post({"type": "clearinghouseState", "user": user})

    async def spot_clearinghouse(self, user: str) -> dict:
        return await self._post({"type": "spotClearinghouseState", "user": user})

    async def user_fills(self, user: str) -> list:
        return await self._post({"type": "userFills", "user": user})
