"""Live market snapshot: today's HIP-4 BTC daily binary, the BTC perp, and volatility.

Refreshed in the background; every quote and every controller tick reads the latest snapshot.
"""

import asyncio
import logging
import math
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.engine.pricing import implied_vol_from_binary
from app.hl.info import HLInfo

log = logging.getLogger("market")

OUTCOME_ASSET_OFFSET = 100_000_000   # HIP-4 asset id = offset + 10 * outcome + side
PERP_TAKER_FEE = 0.00045             # base tier; the quote treats it as the cost of each perp trade
BOOK_DEPTH = 8


def outcome_coin(outcome: int, side: int) -> str:
    """side 0 = Yes, 1 = No. Coins are named '#<10*outcome+side>' on HyperCore."""
    return f"#{10 * outcome + side}"


def _parse_desc(desc: str) -> dict:
    return dict(kv.split(":", 1) for kv in desc.split("|") if ":" in kv)


def _expiry_ms(s: str) -> int:  # "20261006-0600"
    return int(datetime.strptime(s, "%Y%m%d-%H%M").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _levels(side: list) -> list[list[float]]:
    return [[float(l["px"]), float(l["sz"])] for l in side[:BOOK_DEPTH]]


@dataclass
class Snapshot:
    ts: int
    outcome: int
    target: float
    expiry: int
    yes_asks: list
    yes_bid: float
    no_asks: list
    no_bid: float
    perp_bid: float
    perp_ask: float
    perp_mark: float
    funding: float
    max_lev: int
    sz_decimals: int
    sigma1d: float
    spark: list = field(default_factory=list)

    @property
    def spot(self) -> float:
        return (self.perp_bid + self.perp_ask) / 2

    @property
    def hours_left(self) -> float:
        return max(0.0, (self.expiry - self.ts) / 3.6e6)

    @property
    def T(self) -> float:
        return self.hours_left / 8760

    @property
    def rv(self) -> float:
        return self.sigma1d * math.sqrt(365)

    @property
    def iv_binary(self) -> float:
        p = (self.yes_bid + self.yes_asks[0][0]) / 2 if self.yes_asks else float("nan")
        return implied_vol_from_binary(p, self.spot, self.target, self.T)

    @property
    def sigma(self) -> float:
        """Price with the higher of realised and HIP-4 implied (conservative)."""
        iv = self.iv_binary
        return max(self.rv, iv) if iv == iv else self.rv

    def to_ui(self) -> dict:
        """Same shape the web page's engine expects."""
        return {
            "ts": self.ts, "target": self.target, "outcome": self.outcome, "expiry": self.expiry,
            "yes": {"asks": self.yes_asks, "bid": self.yes_bid},
            "no": {"asks": self.no_asks, "bid": self.no_bid},
            "perp": {"bid": self.perp_bid, "ask": self.perp_ask, "mark": self.perp_mark, "funding": self.funding,
                     "maxLev": self.max_lev, "taker": PERP_TAKER_FEE, "szDecimals": self.sz_decimals},
            "sigma1d": self.sigma1d, "spark": self.spark,
            "derived": {"spot": self.spot, "hours": self.hours_left, "rv": self.rv, "ivBinary": self.iv_binary, "sigma": self.sigma},
        }


class MarketService:
    def __init__(self, info: HLInfo, book_every: float = 2.0):
        self.info = info
        self.book_every = book_every
        self.snapshot: Snapshot | None = None
        self._outcome: tuple[int, float, int] | None = None   # (outcome, target, expiry)
        self._meta_at = 0.0
        self._sigma: float | None = None
        self._sigma_at = 0.0
        self._spark: list = []
        self._spark_at = 0.0
        self._task: asyncio.Task | None = None
        self.last_error: str | None = None

    async def start(self) -> None:
        await self.refresh()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.book_every)
            try:
                await self.refresh()
                self.last_error = None
            except Exception as e:  # keep serving the last good snapshot
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("market refresh failed: %s", self.last_error)

    async def _find_outcome(self) -> tuple[int, float, int]:
        meta = await self.info.outcome_meta()
        now = time.time() * 1000
        best = None
        for o in meta["outcomes"]:
            d = _parse_desc(o.get("description", ""))
            if d.get("class") == "priceBinary" and d.get("underlying") == "BTC" and d.get("period") == "1d":
                exp = _expiry_ms(d["expiry"])
                if exp > now and (best is None or exp < best[2]):
                    best = (o["outcome"], float(d["targetPrice"]), exp)
        if best is None:
            raise RuntimeError("No live BTC daily priceBinary outcome in outcomeMeta")
        return best

    async def refresh(self) -> Snapshot:
        now = time.time()
        if self._outcome is None or now - self._meta_at > 30 or now * 1000 >= self._outcome[2]:
            self._outcome = await self._find_outcome()
            self._meta_at = now
        outcome, target, expiry = self._outcome
        if self._sigma is None or now - self._sigma_at > 600:
            end = int(now * 1000)
            daily = await self.info.candles("BTC", "1d", end - 32 * 86_400_000, end)
            closes = [float(c["c"]) for c in daily]
            self._sigma = statistics.pstdev([math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))])
            self._sigma_at = now
        if not self._spark or now - self._spark_at > 60:
            end = int(now * 1000)
            c15 = await self.info.candles("BTC", "15m", end - 24 * 3_600_000, end)
            self._spark = [round(float(c["c"])) for c in c15]
            self._spark_at = now

        yes, no, perp, (meta, ctxs) = await asyncio.gather(
            self.info.l2_book(outcome_coin(outcome, 0)), self.info.l2_book(outcome_coin(outcome, 1)),
            self.info.l2_book("BTC"), self.info.meta_and_ctxs())
        i = next(k for k, a in enumerate(meta["universe"]) if a["name"] == "BTC")
        spark = self._spark[:-1] + [round((float(perp["levels"][0][0]["px"]) + float(perp["levels"][1][0]["px"])) / 2)]
        self.snapshot = Snapshot(
            ts=int(now * 1000), outcome=outcome, target=target, expiry=expiry,
            yes_asks=_levels(yes["levels"][1]), yes_bid=float(yes["levels"][0][0]["px"]) if yes["levels"][0] else 0.0,
            no_asks=_levels(no["levels"][1]), no_bid=float(no["levels"][0][0]["px"]) if no["levels"][0] else 0.0,
            perp_bid=float(perp["levels"][0][0]["px"]), perp_ask=float(perp["levels"][1][0]["px"]),
            perp_mark=float(ctxs[i]["markPx"]), funding=float(ctxs[i]["funding"]),
            max_lev=int(meta["universe"][i]["maxLeverage"]), sz_decimals=int(meta["universe"][i]["szDecimals"]),
            sigma1d=self._sigma, spark=spark)
        return self.snapshot
