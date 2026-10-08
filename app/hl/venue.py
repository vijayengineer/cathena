"""Order execution on Hyperliquid: live (SDK + agent key) or dry run (filled against the live book).

Both venues expose the same three calls, so the trade flow and the controller never know which one runs.
"""

import asyncio
import logging
import math
from dataclasses import dataclass
from typing import Callable, Protocol

from app.config import Settings
from app.engine.market import OUTCOME_ASSET_OFFSET, Snapshot

log = logging.getLogger("venue")


@dataclass
class Fill:
    filled: float      # size actually filled (contracts or BTC)
    avg_px: float
    status: str        # "filled" | "partial" | "none" | "error"
    error: str | None = None
    oid: int | None = None


def round_sig(px: float, sig: int = 5) -> float:
    return float(f"{px:.{sig}g}")


def perp_px(px: float, sz_decimals: int) -> float:
    """Hyperliquid perps: at most 5 significant figures and (6 - szDecimals) decimals."""
    return round(round_sig(px), 6 - sz_decimals)


def floor_to(x: float, decimals: int) -> float:
    f = 10 ** decimals
    return math.floor(abs(x) * f + 1e-9) / f


class Venue(Protocol):
    mode: str
    async def buy_outcome(self, coin: str, size: int, limit_px: float) -> Fill: ...
    async def sell_outcome(self, coin: str, size: int, limit_px: float, fee_bps: float = 0) -> Fill: ...
    async def perp(self, is_buy: bool, size: float, limit_px: float, reduce_only: bool = False, fee_bps: float = 0) -> Fill: ...
    async def set_isolated_leverage(self, leverage: int) -> None: ...


class DryRunVenue:
    """Simulates IOC fills against the latest snapshot. Nothing leaves this process."""
    mode = "dry_run"

    def __init__(self, snap: Callable[[], Snapshot]):
        self._snap = snap

    async def buy_outcome(self, coin: str, size: int, limit_px: float) -> Fill:
        s = self._snap()
        asks = s.yes_asks if coin == f"#{10 * s.outcome}" else s.no_asks
        n, cost = 0, 0.0
        for px, sz in asks:
            if px > limit_px or n >= size:
                break
            take = min(sz, size - n)
            n += int(take)
            cost += take * px
        return Fill(n, cost / n if n else 0.0, "filled" if n == size else ("partial" if n else "none"))

    async def sell_outcome(self, coin: str, size: int, limit_px: float, fee_bps: float = 0) -> Fill:
        s = self._snap()
        bid = s.yes_bid if coin == f"#{10 * s.outcome}" else s.no_bid
        return Fill(size, bid, "filled") if bid >= limit_px else Fill(0, 0.0, "none")

    async def perp(self, is_buy: bool, size: float, limit_px: float, reduce_only: bool = False, fee_bps: float = 0) -> Fill:
        s = self._snap()
        px = s.perp_ask if is_buy else s.perp_bid
        ok = px <= limit_px if is_buy else px >= limit_px
        return Fill(size, px, "filled") if ok else Fill(0, 0.0, "none")

    async def set_isolated_leverage(self, leverage: int) -> None:
        return None


class LiveVenue:
    """Signs with a user's approved agent key on their behalf. The agent can trade but cannot withdraw.

    The builder (cash-out fee) is attached only once that user's approval covers the fee; otherwise
    Hyperliquid would reject the order, and a cash-out must never fail for that reason."""
    mode = "live"

    def __init__(self, base_url: str, agent_key: str, account: str, snap: Callable[[], Snapshot], builder: str | None = None):
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        self._snap = snap
        self.builder = builder
        self.builder_ok_tenths_bp = 0     # set from the user's approved max builder fee
        wallet = Account.from_key(agent_key)
        self.ex = Exchange(wallet, base_url, account_address=account)
        log.info("live venue: agent %s trading for %s", wallet.address, account)

    def _register_outcome(self, coin: str) -> None:
        """SDK 0.24 has no HIP-4 mapping; outcome coins are '#<enc>' with asset id 100_000_000 + enc."""
        info = self.ex.info
        if coin not in info.coin_to_asset:
            info.coin_to_asset[coin] = OUTCOME_ASSET_OFFSET + int(coin[1:])
            info.name_to_coin[coin] = coin

    @staticmethod
    def _parse(resp: dict) -> Fill:
        try:
            if resp.get("status") != "ok":
                return Fill(0, 0.0, "error", str(resp.get("response", resp)))
            st = resp["response"]["data"]["statuses"][0]
            if "filled" in st:
                f = st["filled"]
                return Fill(float(f["totalSz"]), float(f["avgPx"]), "filled", oid=f.get("oid"))
            if "error" in st:
                return Fill(0, 0.0, "error", st["error"])
            return Fill(0, 0.0, "none", str(st))
        except Exception as e:  # unexpected shape: surface it rather than guess
            return Fill(0, 0.0, "error", f"unparsed response: {resp!r} ({e})")

    async def _order(self, coin: str, is_buy: bool, size: float, px: float, reduce_only: bool, fee_bps: float = 0) -> Fill:
        # cash-out fee as a Hyperliquid builder fee: "f" is in tenths of a basis point; needs a one-time approval by the main wallet
        f = int(round(fee_bps * 10))
        builder = {"b": self.builder, "f": f} if self.builder and 0 < f <= self.builder_ok_tenths_bp else None
        resp = await asyncio.to_thread(self.ex.order, coin, is_buy, size, px, {"limit": {"tif": "Ioc"}}, reduce_only, None, builder)
        log.info("order %s %s %s @ %s → %s", coin, "buy" if is_buy else "sell", size, px, resp)
        return self._parse(resp)

    async def buy_outcome(self, coin: str, size: int, limit_px: float) -> Fill:
        self._register_outcome(coin)
        return await self._order(coin, True, int(size), round_sig(limit_px), False)

    async def sell_outcome(self, coin: str, size: int, limit_px: float, fee_bps: float = 0) -> Fill:
        self._register_outcome(coin)
        return await self._order(coin, False, int(size), round_sig(limit_px), False, fee_bps)

    async def perp(self, is_buy: bool, size: float, limit_px: float, reduce_only: bool = False, fee_bps: float = 0) -> Fill:
        s = self._snap()
        return await self._order("BTC", is_buy, floor_to(size, s.sz_decimals), perp_px(limit_px, s.sz_decimals), reduce_only, fee_bps)

    async def set_isolated_leverage(self, leverage: int) -> None:
        resp = await asyncio.to_thread(self.ex.update_leverage, int(leverage), "BTC", False)
        log.info("update_leverage %sx isolated → %s", leverage, resp)
        if resp.get("status") != "ok":
            raise RuntimeError(f"update_leverage failed: {resp}")
