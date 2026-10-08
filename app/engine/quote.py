"""The dial → position engine. Mirrors the web page's JavaScript so the UI and the server agree.

One dial value s in [-100, 100] and a stake C produce:
  * a HIP-4 binary leg (Yes for bulls, No for bears), and
  * a synthetic option leg: a BTC perp position sized to an option's delta and resized as price moves.
Max loss = binary cost + synthetic budget. The rest of the stake is never at risk and backs the perp as margin.
"""

import math
from dataclasses import dataclass, field

from app.engine.market import Snapshot
from app.engine.pricing import bs, delta as bs_delta, inv_n

CUSHION = 1.3        # synthetic budget = fair premium x cushion; it is flattened when the budget is used up
EXP_COST = 1.2       # expected replication cost vs fair premium (fees + whipsaw), from the backtest
LEV_CAP = 20         # perp leverage cap on the never-at-risk cash, at DELTA_CAP of full size
DELTA_CAP = 0.8
STRIKE_STEP = 250
SMALL_DELTA_LO, SMALL_DELTA_HI = 0.30, 0.75   # small-stake option: target delta at the mild and far ends of the dial
SMALL_MARGIN_BUFFER = 1.15   # small-stake option sized so 20x margin plus a 10% buffer fits inside the stake
MIN_NOTIONAL = 10.0  # Hyperliquid minimum order value, both legs (outcomes: measured at the MID price, not the limit)


MOON_MAX_PX = 0.75   # Moonshot is off on a side whose binary costs this much: all in, it would pay at most +33%
ALL_IN = 95          # |dial| from here to 100 is Moonshot (all in): max loss ramps 50% → 100% of stake


def risk_pct(s: int) -> int:
    """Max loss as % of stake: 20% at the mild end of the dial, ~50% through Aggressive, then Moonshot ramps to 100%."""
    a = abs(s)
    if a == 0:
        return 0
    if a < ALL_IN:
        return int(round((20 + 30 * a / 100) / 5) * 5)
    return int(round((50 + 50 * (a - ALL_IN) / (100 - ALL_IN)) / 5) * 5)


def _smooth(a: float, b: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def walk(asks: list, budget: float) -> tuple[int, float, float]:
    """Integer contracts bought by walking the ask levels within budget → (n, cost, avg)."""
    n, cost = 0, 0.0
    for px, sz in asks:
        can = min(sz, math.floor((budget - cost) / px + 1e-9))
        if can <= 0:
            break
        n += int(can)
        cost += can * px
        if can < sz:
            break
    return n, cost, (cost / n if n else (asks[0][0] if asks else 0.0))


@dataclass
class Quote:
    s: int
    stake: float
    risk_pct: int
    bull: bool
    max_budget: float
    # binary leg
    outcome_coin: str
    bin_n: int
    bin_cost: float
    bin_avg: float
    bin_limit: float
    # synthetic leg
    strike: float
    units: float
    fair_premium: float      # per 1 BTC of option
    delta0: float
    syn_budget: float
    lev_now: float
    lev_full: float
    capped: bool
    # totals
    max_loss: float
    spot: float
    target: float
    expiry: int
    sigma: float
    T: float
    warnings: list = field(default_factory=list)
    min_stake: float = 0.0   # smallest stake at which this dial setting buys a binary that clears the $10 minimum
    blocked: str = ""        # why this setting can't be placed right now (Moonshot when the line is already on your side)

    def payoff(self, X: float, cost_mult: float = EXP_COST) -> float:
        win = X >= self.target if self.bull else X < self.target
        b = (self.bin_n if win else 0) - self.bin_cost
        if not self.units:
            return b
        intrinsic = max(0.0, X - self.strike) if self.bull else max(0.0, self.strike - X)
        return b + max(self.units * intrinsic - cost_mult * self.units * self.fair_premium, -self.syn_budget)

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["scenarios"] = {f"{m:+.0%}": round(self.payoff(self.spot * (1 + (m if self.bull else -m))), 2) for m in (-0.02, 0, 0.01, 0.03, 0.06)}
        return d


def min_tradable_stake(snap: Snapshot, s: int, hi: int = 20_000) -> int | None:
    """Smallest whole-dollar stake at which this dial setting can be placed: a binary over Hyperliquid's $10 minimum,
    or (small stakes) the option leg alone over the perp's $10 minimum."""
    lo, best = 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        q = build_quote(snap, s, mid)
        if q and (q.bin_n or q.units):
            best, hi = mid, mid - 1
        else:
            lo = mid + 1
    return best


def moon_off_text(bull: bool, px: float) -> str:
    side, where = ("bulls", "above") if bull else ("bears", "below")
    return (f"Moonshot is off for {side} right now: BTC is already {where} today's line, so going all in would pay "
            f"at most +{round((1 / px - 1) * 100)}%. Aggressive pays more.")


def build_quote(snap: Snapshot, s: int, stake: float, min_notional: float = MIN_NOTIONAL) -> Quote | None:
    """min_notional = Hyperliquid's $10 order minimum; the paper-money demo passes 0 so any stake shows real numbers."""
    if s == 0:
        return None
    s = max(-100, min(100, int(s)))
    bull, a = s > 0, abs(s) / 100
    rp = risk_pct(s)
    M = stake * rp / 100
    S, T, sig = snap.spot, snap.T, snap.sigma
    sT = sig * math.sqrt(T)

    w_syn = _smooth(0.15, 0.95, a)
    target_delta = 0.5 - 0.25 * min(1.0, max(0.0, (a - 0.2) / 0.8))
    d1 = inv_n(target_delta) if bull else inv_n(1 - target_delta)
    K = round(S * math.exp(-sT * d1 + 0.5 * sT * sT) / STRIKE_STEP) * STRIKE_STEP
    f = bs(S, K, T, sig, bull)
    dlt = bs_delta(S, K, T, sig, bull)

    units_cap = max(0.0, LEV_CAP * (stake - M) / (DELTA_CAP * S))
    units = units_cap * w_syn
    if CUSHION * units * f > 0.6 * M:
        units = 0.6 * M / (CUSHION * f)
    units = math.floor(units * 1e5) / 1e5
    warns: list[str] = []
    if units * abs(dlt) * S < min_notional or units <= 0:
        if w_syn > 0.05:
            warns.append("Moonshot: the whole stake goes on today's line." if rp >= 100 else "Synthetic below the $10 perp minimum; add stake to switch it on.")
        units = 0.0
    capped = units > 0 and w_syn > 0.98
    if capped:
        warns.append("Using all the margin this stake allows. Add stake to unlock more convexity.")
    syn_budget = CUSHION * units * f

    asks = snap.yes_asks if bull else snap.no_asks
    bid = snap.yes_bid if bull else snap.no_bid
    mid = (bid + asks[0][0]) / 2 if asks else 1.0
    min_n = math.ceil(min_notional * 1.005 / mid)            # Hyperliquid checks contracts x mid >= $10
    min_stake = math.ceil(min_n * (asks[0][0] if asks else 1.0) * 1.01 / (rp / 100))
    n, cost, avg = walk(asks, M - syn_budget)
    if (cost < min_notional or n < min_n) or not n:
        if n:
            warns.append("Binary leg is under Hyperliquid's $10 minimum at this stake; raise the stake to trade this setting.")
        n, cost, avg = 0, 0.0, (asks[0][0] if asks else 0.0)
        # small stake: the binary can't clear Hyperliquid's minimum, so the whole max loss runs as the option instead.
        # Its size is then set by margin on the whole stake (no binary takes cash), so it's the same for every setting,
        # and boldness picks the strike: bolder = deeper in the money = more at risk and a bigger payoff on any rise.
        # (The big-stake rule, further out of the money, would invert risk and payoff here.)
        td_fb = SMALL_DELTA_LO + (SMALL_DELTA_HI - SMALL_DELTA_LO) * a
        d1f = inv_n(td_fb) if bull else inv_n(1 - td_fb)
        Kf = round(S * math.exp(-sT * d1f + 0.5 * sT * sT) / STRIKE_STEP) * STRIKE_STEP
        ff, df = bs(S, Kf, T, sig, bull), bs_delta(S, Kf, T, sig, bull)
        cap_fb = LEV_CAP * stake / (SMALL_MARGIN_BUFFER * S)
        fb = math.floor(min(cap_fb, M / (CUSHION * ff)) * 1e5) / 1e5 if ff > 0 else 0.0
        if fb > 0 and fb * abs(df) * S >= min_notional and abs(s) < ALL_IN:   # Moonshot stays all-binary
            K, f, dlt = Kf, ff, df
            units, syn_budget = fb, CUSHION * fb * ff
            capped = fb >= cap_fb - 1e-9
            warns = [w for w in warns if "minimum" not in w] + ["Small stake: the whole position runs as the option, without the daily binary."]
    # Moonshot buys today's line with everything. When BTC is already well on your side of it the binary is expensive,
    # so all in would risk the whole stake for a small payoff: switch it off and say why (same rule as the page)
    blocked = ""
    if abs(s) >= ALL_IN and asks and asks[0][0] >= MOON_MAX_PX:
        blocked = moon_off_text(bull, asks[0][0])
        n, cost, units, syn_budget, capped, warns = 0, 0.0, 0.0, 0.0, False, [blocked]
    # worst level the walk touched, plus one tick of room: the IOC limit
    limit = 0.0
    if n:
        left = n
        for px, sz in asks:
            limit = px
            left -= sz
            if left <= 0:
                break
        limit = min(0.999, limit + 0.002)

    return Quote(
        s=s, stake=stake, risk_pct=rp, bull=bull, max_budget=M,
        outcome_coin=f"#{10 * snap.outcome + (0 if bull else 1)}", bin_n=n, bin_cost=cost, bin_avg=avg, bin_limit=limit,
        strike=K, units=units, fair_premium=f, delta0=dlt, syn_budget=syn_budget,
        lev_now=units * abs(dlt) * S / stake if units else 0.0, lev_full=units * S / stake if units else 0.0, capped=capped,
        max_loss=cost + syn_budget, spot=S, target=snap.target, expiry=snap.expiry, sigma=sig, T=T, warnings=warns, min_stake=min_stake,
        blocked=blocked)
