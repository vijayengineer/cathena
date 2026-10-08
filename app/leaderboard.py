"""Leaderboard: P&L ranked over a window. Today the only real trader is this account ("you"); once Privy users
exist each gets a row from their own book. In demo mode a fixed, seeded set of sample traders fills the board."""

import random
import time

WINDOWS = {"24h": 86_400_000, "7d": 7 * 86_400_000, "30d": 30 * 86_400_000, "all": None}
STYLES = ["Aggressive", "Bold", "Conservative", "Moonshot"]

_DEMO_NAMES = [
    ("satsmaxi", "Sats Maxi"), ("thetagang", "theta gang"), ("deltaneutral", "Delta Neutral"), ("moonshotmia", "Mia"),
    ("gammaqueen", "gamma queen"), ("06utc", "Six AM Club"), ("perpetua", "Perpetua"), ("vegavibes", "vega vibes"),
    ("hypurrcat", "hypurr"), ("stoploss", "no stop loss"), ("convexkid", "convex kid"), ("dialgod", "dial god"),
    ("bearbrr", "bear brr"), ("calmcarry", "calm carry"), ("bluechipbae", "bluechip"), ("wickhunter", "wick hunter"),
    ("sigmaburn", "sigma burn"), ("onedial", "one dial"), ("strikezone", "strike zone"), ("liquidlad", "liquid lad"),
]


def _demo_rows(window: str) -> list[dict]:
    scale = {"24h": 1.0, "7d": 4.2, "30d": 13.0, "all": 31.0}[window]
    rows = []
    for i, (handle, name) in enumerate(_DEMO_NAMES):
        r = random.Random(f"{handle}:{window}")            # stable per window, so the board doesn't reshuffle on refresh
        base = 4200 * (0.82 ** i) * scale * r.uniform(0.75, 1.25)
        pnl = base if i < 17 else -base * 0.15
        trades = int(r.uniform(6, 30) * scale ** 0.8)
        side = 1 if r.random() < 0.6 else -1
        style = STYLES[r.randrange(3)]
        rows.append({"handle": handle, "name": name, "pnl": round(pnl, 2), "trades": trades,
                     "win_rate": round(r.uniform(0.42, 0.74), 2), "best_multiple": round(r.uniform(1.6, 9.5), 1),
                     "style": style, "side": side, "seed": handle})
    return rows


def _summary(positions: list[dict], since: float | None) -> dict:
    mine = [p for p in positions if p["status"] in ("open", "closed", "settled") and (since is None or p["created"] >= since)]
    pnl = 0.0
    for p in mine:
        pnl += p.get("realized_pnl", 0) if p["status"] != "open" else p.get("mark", {}).get("cashout", {}).get("net_pnl", 0)
    done = [p for p in mine if p["status"] != "open"]
    wins = [p for p in done if p.get("realized_pnl", 0) > 0]
    best = max((1 + p.get("realized_pnl", 0) / p["stake"] for p in done if p.get("stake")), default=None)
    fav = max(STYLES, key=lambda z: sum(1 for p in mine if _style(p.get("s", 0)) == z)) if mine else None
    return {"pnl": round(pnl, 2), "trades": len(mine), "win_rate": round(len(wins) / len(done), 2) if done else None,
            "best_multiple": round(best, 1) if best else None, "style": fav, "side": 1 if sum(p.get("s", 0) for p in mine) >= 0 else -1}


def build(traders: dict[str, list[dict]], me: str, window: str, demo: bool) -> dict:
    """traders: address → that trader's positions. `me` is the viewer's address (or "demo")."""
    window = window if window in WINDOWS else "24h"
    since = None if WINDOWS[window] is None else time.time() * 1000 - WINDOWS[window]
    rows = _demo_rows(window) if demo else []
    for addr, positions in traders.items():
        you = addr == me
        short = addr if not addr.startswith("0x") else addr[:6] + "…" + addr[-4:]
        rows.append({"handle": "you" if you else short, "name": "You" if you else short, "seed": addr, "you": you, **_summary(positions, since)})
    rows.sort(key=lambda x: -x["pnl"])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return {"window": window, "demo": demo, "you": next(r for r in rows if r.get("you")), "rows": rows[:25]}


def _style(s: int) -> str:
    a = abs(s)
    return "Conservative" if a <= 33 else "Bold" if a <= 66 else "Aggressive" if a < 95 else "Moonshot"
