"""Positions: open a dial quote, run each synthetic option, mark, close and settle.

A position = one HIP-4 binary leg + one synthetic option leg (a BTC perp kept at the option's delta).
The controller loop rebalances every open synthetic, flattens it when its budget is used up,
closes the perp 5 minutes before the binary settles, and records settlement afterwards.
"""

import asyncio
import logging
import math
import time
import uuid

from app.config import Settings
from app.engine.market import MarketService, PERP_TAKER_FEE, Snapshot, _levels
from app.engine.pricing import delta as bs_delta, prob_above
from app.engine.quote import ALL_IN, LEV_CAP, Quote, build_quote, min_tradable_stake, walk
from app.hl.venue import Fill, Venue
from app.store import Store

log = logging.getLogger("book")

BAND = 0.05                 # rebalance when held delta drifts more than 5% of option units
TICK_S = 3.0
CLOSE_BEFORE_MS = 5 * 60_000
PERP_SLIP = 0.001           # IOC limit 0.1% through the touch
BIN_SLIP = 0.01             # binary close: up to 1 cent under the bid
MIN_ORDER_USD = 10.0
MOONSHOT_MIN_USD = 11.0   # Moonshot is all binary, and $10 of it lands just under the $10 minimum at the mid price
MAX_QUOTE_DRIFT = 0.05      # client quote vs fresh server quote
BUY_SLIP = 0.005            # binary open: pay at most 0.5% over each ask level in a fresh book (the user's choice)
BUY_MOVE_MAX = 0.20         # refuse if the fresh book's average is this much over what the user was shown
BUY_TRIES = 3               # partial fills go again on a fresh book while what's left still clears the $10 minimum


class TradeError(Exception):
    pass


def _apply_fill(syn: dict, qty: float, px: float) -> None:
    """Average-cost accounting for the synthetic's perp leg. qty is signed (+ buy)."""
    held, avg = syn["held"], syn["avg_entry"]
    if held == 0 or (held > 0) == (qty > 0):
        syn["avg_entry"] = (abs(held) * avg + abs(qty) * px) / (abs(held) + abs(qty))
    else:
        closing = min(abs(qty), abs(held))
        syn["realized"] += closing * (px - avg) * (1 if held > 0 else -1)
        if abs(qty) > abs(held):
            syn["avg_entry"] = px
    syn["held"] = round(held + qty, 8)
    if syn["held"] == 0:
        syn["avg_entry"] = 0.0
    syn["fees"] += abs(qty) * px * PERP_TAKER_FEE


def syn_pnl(syn: dict, S: float) -> float:
    return syn["realized"] + syn["held"] * (S - syn["avg_entry"]) - syn["fees"]


class Book:
    def __init__(self, settings: Settings, market: MarketService, venue: Venue, store: Store,
                 account: str | None = None, max_stake: float | None = None, max_open_risk: float | None = None):
        self.settings, self.market, self.venue, self.store = settings, market, venue, store
        self.account = account or settings.account_address
        self.max_stake = max_stake if max_stake is not None else settings.max_stake
        self.max_open_risk = max_open_risk      # None = no cap (owner, demo)
        self.open_block = lambda: None          # set per user: a reason new trades are refused (paused, access ended)
        self.min_notional = MIN_ORDER_USD       # Hyperliquid's $10 minimum; 0 for the paper-money demo book
        self.unified = False   # unified account: one USDC balance backs both the binary and the perp
        self.positions: list[dict] = store.load_positions()
        # a position opened in another mode (simulated, then live trading switched on) is retired without any orders:
        # the live controller must never "rebalance" perp size that only ever existed in a simulation
        stale = [p for p in self.positions if p["status"] in ("open", "opening") and p.get("mode", venue.mode) != venue.mode]
        for p in stale:
            p.update(status="void", note=f"opened in {p.get('mode')} mode; retired when {venue.mode} trading started")
        if stale:
            store.save_positions(self.positions)
            log.warning("retired %d position(s) opened in another mode", len(stale))
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    # ---------- helpers ----------
    @property
    def snap(self) -> Snapshot:
        if not self.market.snapshot:
            raise TradeError("Market data not ready")
        return self.market.snapshot

    def _save(self) -> None:
        self.store.save_positions(self.positions)

    def _find(self, pid: str) -> dict:
        for p in self.positions:
            if p["id"] == pid:
                return p
        raise TradeError(f"Unknown position {pid}")

    # ---------- open ----------
    async def open(self, s: int, stake: float, expected_max_loss: float | None = None) -> dict:
        if (why := self.open_block()):
            raise TradeError(why)
        if self.min_notional and stake < MIN_ORDER_USD:
            raise TradeError(f"The smallest stake is ${MIN_ORDER_USD:,.0f}.")
        if self.min_notional and abs(s) >= ALL_IN and stake < MOONSHOT_MIN_USD:
            raise TradeError(f"Moonshot starts at ${MOONSHOT_MIN_USD:,.0f}.")
        if stake > self.max_stake:
            raise TradeError(f"Stake ${stake:,.0f} is above your ${self.max_stake:,.0f} limit.")
        snap = self.snap
        if time.time() * 1000 - snap.ts > 10_000:
            raise TradeError("Market data is stale; try again in a moment.")
        if snap.hours_left < 0.25:
            raise TradeError("Too close to settlement; the next daily binary opens after 06:00 UTC.")
        q: Quote | None = build_quote(snap, s, stake, self.min_notional)
        if q is None:
            raise TradeError("Pick a side first.")
        if q.blocked:
            raise TradeError(q.blocked)
        if not q.bin_n and not q.units:
            need = min_tradable_stake(snap, s)
            raise TradeError(f"Too small to trade: Hyperliquid's minimum order is $10. Stake at least ${need:,.0f} for this setting." if need
                             else "Too small to trade: Hyperliquid's minimum order is $10.")
        if expected_max_loss and abs(q.max_loss - expected_max_loss) > MAX_QUOTE_DRIFT * expected_max_loss:
            raise TradeError(f"Price moved: max loss is now ${q.max_loss:,.2f} (you saw ${expected_max_loss:,.2f}). Review and slide again.")

        if self.max_open_risk is not None:
            at_risk = sum(p.get("max_loss", p.get("max_loss_quoted", 0)) for p in self.positions if p["status"] in ("open", "opening"))
            if at_risk + q.max_loss > self.max_open_risk + 1e-9:
                left = max(0.0, self.max_open_risk - at_risk)
                raise TradeError(f"Early access caps what you can lose across open trades at ${self.max_open_risk:,.0f}. "
                                 f"${left:,.2f} left: lower the stake, pick a steadier setup, or cash out a trade.")
        if self.venue.mode == "live":
            await self._check_funds(q)

        async with self._lock:
            pid = uuid.uuid4().hex[:8]
            pos = {
                "id": pid, "mode": self.venue.mode, "status": "opening", "created": int(time.time() * 1000),
                "s": q.s, "stake": stake, "risk_pct": q.risk_pct, "bull": q.bull, "max_loss_quoted": q.max_loss,
                "outcome": snap.outcome, "target": snap.target, "expiry": snap.expiry, "open_spot": q.spot, "sigma": q.sigma,
                "binary": {"coin": q.outcome_coin, "requested": q.bin_n, "n": 0, "avg_px": 0.0, "cost": 0.0, "limit": q.bin_limit, "status": "skipped" if not q.bin_n else "pending"},
                "synthetic": {"strike": q.strike, "units": q.units, "fair_premium": q.fair_premium, "budget": q.syn_budget,
                              "held": 0.0, "avg_entry": 0.0, "realized": 0.0, "fees": 0.0, "rebalances": 0, "leverage": 0,
                              "status": "off" if not q.units else "pending", "last_target": 0.0},
                "warnings": q.warnings,
            }
            self.store.event("open_requested", id=pid, s=q.s, stake=stake, max_loss=round(q.max_loss, 2), mode=self.venue.mode)

            # 1. binary leg: priced off a fresh book with room to move, so it fills (see _buy_binary)
            if q.bin_n:
                b = pos["binary"]
                n, cost, err, sent = await self._buy_binary(pid, q)
                part = 0 < n < sent
                b.update(n=n, avg_px=cost / n if n else 0.0, cost=cost, status="partial" if part else "filled" if n else "failed")
                if part:
                    pos["warnings"] = list(pos["warnings"]) + [f"Only {n} of {sent} contracts filled: the order book moved. "
                                                               f"${q.bin_cost - cost:,.2f} of your stake wasn't used and stays in your account."]
                if n == 0:
                    f = Fill(0, 0.0, "none", error=err)
                    pos["status"] = "failed"
                    pos["error"] = f.error or "Binary did not fill at the quoted price."
                    self.positions.append(pos)
                    self._save()
                    raise TradeError(pos["error"])

            # 2. synthetic leg: isolated leverage sized to the safe cash, then the opening delta
            syn = pos["synthetic"]
            if q.units:
                safe_cash = max(1.0, stake - q.max_loss)
                lev = min(snap.max_lev, LEV_CAP, max(3, math.ceil(q.units * q.spot / safe_cash)))
                try:
                    await self.venue.set_isolated_leverage(lev)
                    syn["leverage"] = lev
                    syn["status"] = "active"
                    await self._rebalance(pos, snap, force=True)
                except Exception as e:
                    syn["status"] = "pending"   # the controller retries on its next tick
                    pos.setdefault("warnings", []).append(f"Perp leg not opened yet: {e}")
                    self.store.event("synthetic_error", id=pid, error=str(e))

            pos["max_loss"] = pos["binary"]["cost"] + syn["budget"]
            pos["status"] = "open"
            self.positions.append(pos)
            self._save()
            return pos

    async def _buy_binary(self, pid: str, q: Quote) -> tuple[int, float, str | None, int]:
        """Buy as much of the binary as the budget allows, at most BUY_SLIP over each ask level in a fresh book.
        The quote's book can be seconds old and these books are thin, so: re-read the book, size by walking it with every
        level BUY_SLIP dearer (so the order is affordable even if it all fills at the limit), send an IOC at the worst
        level + BUY_SLIP, and go again on a fresh book for whatever didn't fill, while it still clears the $10 minimum.
        → (contracts, cost, error, contracts sent first)"""
        budget = max(q.bin_cost, q.max_budget - q.syn_budget)          # the binary's share of the quoted max loss
        n_tot, cost_tot, err, first = 0, 0.0, None, 0
        for i in range(BUY_TRIES):
            asks, bid = await self._fresh_asks(q)
            left = budget - cost_tot
            dear = [[px * (1 + BUY_SLIP), sz] for px, sz in asks]
            n, _, _ = walk(dear, left)
            if not n:
                err = err or "No sellers at a fair price right now. Try again in a moment."
                break
            if i == 0 and q.bin_avg:
                _, c0, avg0 = walk(asks, left)
                if avg0 > q.bin_avg * (1 + BUY_MOVE_MAX):
                    raise TradeError(f"The price moved from {q.bin_avg * 100:.1f}c to {avg0 * 100:.1f}c. Review and slide again.")
            worst, need = asks[0][0], n
            for px, sz in asks:
                worst = px
                need -= sz
                if need <= 0:
                    break
            limit = min(0.999, round(worst * (1 + BUY_SLIP), 5))
            mid = (bid + asks[0][0]) / 2 if bid else asks[0][0]
            if self.min_notional and n * mid < MIN_ORDER_USD * 1.005:
                # Hyperliquid checks $10 at the mid: a wide spread can push a full-budget order under it. Send the minimum
                # if, filled entirely at the limit, it costs at most 2% over the budget (inside the 5% quote drift)
                min_n = math.ceil(MIN_ORDER_USD * 1.005 / mid)
                if min_n * limit > left * 1.02:
                    if not n_tot:
                        err = (f"Hyperliquid needs a $10 order at the mid price ({mid * 100:.1f}c), which costs about "
                               f"${min_n * asks[0][0]:,.2f} here: the spread is wide right now. Try a bigger stake or wait a moment.")
                    break
                n = min_n
            first = first or n
            f: Fill = await self.venue.buy_outcome(q.outcome_coin, n, limit)
            self.store.event("binary_fill", id=pid, coin=q.outcome_coin, requested=n, filled=f.filled, avg_px=f.avg_px,
                             limit=limit, status=f.status, error=f.error, attempt=i + 1)
            log.info("binary buy %s try %d: %d @ <=%.5f → filled %s @ %.5f", q.outcome_coin, i + 1, n, limit, f.filled, f.avg_px)
            n_tot += int(f.filled)
            cost_tot += f.filled * f.avg_px
            err = f.error or err
            if f.status == "error" or f.filled >= n:
                break
        return n_tot, cost_tot, err, first

    async def _fresh_asks(self, q: Quote) -> tuple[list, float]:
        """The outcome's ask levels and best bid right now; the snapshot's if the book can't be read."""
        try:
            lv = (await self.market.info.l2_book(q.outcome_coin))["levels"]
            asks = _levels(lv[1])
            if asks:
                return asks, float(lv[0][0]["px"]) if lv[0] else 0.0
        except Exception as e:
            log.warning("fresh book for %s failed: %s", q.outcome_coin, e)
        s = self.snap
        return (s.yes_asks, s.yes_bid) if q.bull else (s.no_asks, s.no_bid)

    async def _check_funds(self, q: Quote) -> None:
        """Live only: binary buys draw spot USDC; the perp needs isolated margin from the perp account."""
        addr = self.account
        spot = await self.market.info.spot_clearinghouse(addr)
        perp = await self.market.info.clearinghouse(addr)
        usdc = next((float(b["total"]) - float(b["hold"]) for b in spot.get("balances", []) if b["coin"] == "USDC"), 0.0)
        withdrawable = float(perp.get("withdrawable", 0))
        need_spot = q.bin_cost * 1.02
        lev = min(self.snap.max_lev, LEV_CAP, max(3, math.ceil(q.units * q.spot / max(1.0, q.stake - q.max_loss)))) if q.units else 1
        need_margin = q.units * q.spot / lev * 1.1 if q.units else 0.0
        problems = []
        if self.unified:
            if need_spot + need_margin > usdc:
                raise TradeError(f"Not enough USDC on Hyperliquid: ${usdc:,.2f} < ${need_spot + need_margin:,.2f} needed. Top up to trade.")
            return
        if need_spot > usdc:
            problems.append(f"spot USDC ${usdc:,.2f} < ${need_spot:,.2f} needed for the binary")
        if need_margin > withdrawable:
            problems.append(f"perp withdrawable ${withdrawable:,.2f} < ${need_margin:,.2f} margin needed")
        if problems:
            raise TradeError("Not enough funds on Hyperliquid: " + "; ".join(problems) + ". Use 'Fund both legs' in Set up, or move USDC between spot and perp on Hyperliquid.")

    # ---------- synthetic replication ----------
    def _target(self, pos: dict, S: float, now_ms: int) -> float:
        syn = pos["synthetic"]
        T = max(0.0, (pos["expiry"] - now_ms) / 3.6e6 / 8760)
        d = bs_delta(S, syn["strike"], T, pos["sigma"], pos["bull"])
        return syn["units"] * d   # signed: + long for calls, − short for puts

    async def _perp_trade(self, pos: dict, qty: float, snap: Snapshot, reason: str, reduce_only: bool = False, fee_bps: float = 0) -> Fill:
        is_buy = qty > 0
        px = snap.perp_ask * (1 + PERP_SLIP) if is_buy else snap.perp_bid * (1 - PERP_SLIP)
        f = await self.venue.perp(is_buy, abs(qty), px, reduce_only, fee_bps)
        if f.filled:
            _apply_fill(pos["synthetic"], f.filled if is_buy else -f.filled, f.avg_px)
            pos["synthetic"]["rebalances"] += 1
        self.store.event("perp_fill", id=pos["id"], reason=reason, qty=round(qty, 6), filled=f.filled, avg_px=f.avg_px, status=f.status, error=f.error)
        return f

    async def _rebalance(self, pos: dict, snap: Snapshot, force: bool = False) -> None:
        syn = pos["synthetic"]
        target = self._target(pos, snap.perp_mid, snap.ts)
        syn["last_target"] = target
        gap = target - syn["held"]
        if (force or abs(gap) > BAND * syn["units"]) and abs(gap) * snap.perp_mid >= max(self.min_notional, 1.0):
            await self._perp_trade(pos, gap, snap, "open" if force else "rebalance")

    async def _flatten(self, pos: dict, snap: Snapshot, status: str, fee_bps: float = 0) -> Fill | None:
        syn = pos["synthetic"]
        f = None
        if abs(syn["held"]) > 0:
            f = await self._perp_trade(pos, -syn["held"], snap, status, reduce_only=True, fee_bps=fee_bps)
        if abs(syn["held"]) < 1e-5:
            syn["status"] = status
            self.store.event("synthetic_closed", id=pos["id"], status=status, pnl=round(syn_pnl(syn, snap.perp_mid), 2))
        return f

    async def tick(self) -> None:
        snap = self.market.snapshot
        if not snap:
            return
        now = int(time.time() * 1000)
        async with self._lock:
            changed = False
            for pos in self.positions:
                if pos["status"] != "open":
                    continue
                syn = pos["synthetic"]
                try:
                    if syn["status"] in ("active", "pending") and syn["units"]:
                        if now >= pos["expiry"] - CLOSE_BEFORE_MS:
                            await self._flatten(pos, snap, "closed_before_settlement")
                        elif syn_pnl(syn, snap.perp_mid) <= -syn["budget"]:
                            await self._flatten(pos, snap, "budget_used")
                        else:
                            syn["status"] = "active"
                            await self._rebalance(pos, snap)
                        changed = True
                    if now >= pos["expiry"] + 120_000:
                        await self._settle(pos)
                        changed = True
                except Exception as e:
                    log.exception("tick failed for %s", pos["id"])
                    self.store.event("tick_error", id=pos["id"], error=str(e))
            if changed:
                self._save()

    async def _settle(self, pos: dict) -> None:
        """Binary pays automatically on HyperCore. The settlement price is the oracle at expiry, which Hyperliquid
        also uses as the next day's line, so take that when the next outcome is up; else the perp at that minute."""
        snap = self.market.snapshot
        if snap and snap.expiry == pos["expiry"] + 86_400_000 and snap.target:
            px = float(snap.target)
        else:
            c = await self.market.info.candles("BTC", "1m", pos["expiry"] - 60_000, pos["expiry"] + 60_000)
            px = float(next((k["o"] for k in c if int(k["t"]) == pos["expiry"]), c[-1]["c"])) if c else self.snap.spot
        win = px >= pos["target"] if pos["bull"] else px < pos["target"]
        b = pos["binary"]
        b["payout"] = float(b["n"]) if win else 0.0
        b["settle_px_estimate"] = px
        b["status"] = "settled"
        pos["status"] = "settled"
        pos["realized_pnl"] = b["payout"] - b["cost"] + syn_pnl(pos["synthetic"], px)
        self.store.event("settled", id=pos["id"], win=win, settle_px_estimate=px, payout=b["payout"], pnl=round(pos["realized_pnl"], 2))

    async def run(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                await self.tick()
            except Exception:
                log.exception("controller tick failed")

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def close_all(self) -> list[dict]:
        """Cash out every open position (owner wind-down, or the user's own 'close everything')."""
        out = []
        for p in [p for p in self.positions if p["status"] == "open"]:
            try:
                out.append({"id": p["id"], "ok": True, "pnl": (await self.close(p["id"])).get("realized_pnl")})
            except TradeError as e:
                out.append({"id": p["id"], "ok": False, "error": str(e)})
        return out

    # ---------- cash out early ----------
    def fee_bps(self) -> tuple[float, float]:
        """Cash-out fee (binary, perp) in bps. Live, it is charged only when the user's builder approval covers it."""
        fb, fp = self.settings.cashout_fee_binary_bps, self.settings.cashout_fee_perp_bps
        if self.venue.mode != "live":
            return fb, fp
        ok = getattr(self.venue, "builder_ok_tenths_bp", 0) if getattr(self.venue, "builder", None) else 0
        return (fb if fb * 10 <= ok else 0.0), (fp if fp * 10 <= ok else 0.0)

    def cashout_quote(self, pos: dict, snap: Snapshot) -> dict:
        """What closing everything right now returns: binary sold at the bid, perp closed at the touch,
        minus the venue's taker fee and Cathena's cash-out fee."""
        b, syn = pos["binary"], pos["synthetic"]
        bid = snap.yes_bid if pos["bull"] else snap.no_bid
        bin_value = b["n"] * bid
        held = syn["held"]
        close_px = snap.perp_bid if held > 0 else snap.perp_ask
        perp_notional = abs(held) * close_px
        syn_after = syn_pnl(syn, close_px) - perp_notional * PERP_TAKER_FEE
        fb, fp = self.fee_bps()
        fee_bin = bin_value * fb / 1e4
        fee_perp = perp_notional * fp / 1e4
        net = bin_value - b["cost"] + syn_after - fee_bin - fee_perp
        at_risk = b["cost"] + syn["budget"]
        return {"binary_value": bin_value, "synthetic_pnl": syn_after, "fee_binary": fee_bin, "fee_perp": fee_perp,
                "fee": fee_bin + fee_perp, "fee_bps": {"binary": fb, "perp": fp},
                "net_pnl": net, "you_get_back": max(0.0, at_risk + net), "at_risk": at_risk, "multiple": net / at_risk if at_risk else 0.0}

    async def close(self, pid: str) -> dict:
        snap = self.snap
        async with self._lock:
            pos = self._find(pid)
            if pos["status"] != "open":
                raise TradeError(f"Position is {pos['status']}.")
            fee_bin_bps, fee_perp_bps = self.fee_bps()
            b = pos["binary"]
            if b["n"]:
                bid = snap.yes_bid if pos["bull"] else snap.no_bid
                f = await self.venue.sell_outcome(b["coin"], b["n"], max(0.001, bid - BIN_SLIP), fee_bin_bps)
                b["sold"] = f.filled
                b["sold_px"] = f.avg_px
                self.store.event("binary_sell", id=pid, filled=f.filled, avg_px=f.avg_px, status=f.status, error=f.error)
            perp_fill = None
            if pos["synthetic"]["units"]:
                perp_fill = await self._flatten(pos, snap, "closed_early", fee_perp_bps)
            proceeds = b.get("sold", 0) * b.get("sold_px", 0.0)
            perp_notional = perp_fill.filled * perp_fill.avg_px if perp_fill and perp_fill.filled else 0.0
            fee = proceeds * fee_bin_bps / 1e4 + perp_notional * fee_perp_bps / 1e4
            pos["cashout_fee"] = fee
            pos["realized_pnl"] = proceeds - b["cost"] + syn_pnl(pos["synthetic"], snap.perp_mid) - fee
            pos["status"] = "closed"
            self.store.event("cashed_out", id=pid, pnl=round(pos["realized_pnl"], 2), fee=round(fee, 2), builder=fee > 0 and self.venue.mode == "live")
            self._save()
            return pos

    # ---------- views ----------
    def marked(self, pos: dict) -> dict:
        snap = self.market.snapshot
        out = dict(pos)
        if pos["status"] != "open" or not snap:
            return out
        b, syn = pos["binary"], pos["synthetic"]
        T = max(0.0, (pos["expiry"] - snap.ts) / 3.6e6 / 8760)
        p_yes = prob_above(snap.spot, pos["target"], T, snap.sigma)
        p_side = p_yes if pos["bull"] else 1 - p_yes
        bid = snap.yes_bid if pos["bull"] else snap.no_bid
        s_pnl = syn_pnl(syn, snap.perp_mid)
        out["mark"] = {
            "spot": snap.spot,
            "binary_close_value": b["n"] * bid,
            "binary_fair_value": b["n"] * p_side,
            "synthetic_pnl": s_pnl,
            "synthetic_budget_used": (max(0.0, -s_pnl) / syn["budget"]) if syn["budget"] else 0.0,
            "target_delta": self._target(pos, snap.perp_mid, snap.ts) if syn["units"] else 0.0,
            "pnl_close_now": b["n"] * bid - b["cost"] + s_pnl,
            "pnl_fair": b["n"] * p_side - b["cost"] + s_pnl,
            "cashout": self.cashout_quote(pos, snap),
        }
        return out

    def list(self) -> list[dict]:
        return [self.marked(p) for p in sorted(self.positions, key=lambda p: -p["created"])]
