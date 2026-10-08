"""Cathena Convex API + web app.

Run:  uv run uvicorn app.main:app --port 8000   then open http://localhost:8000

Visitors who aren't signed in trade a shared dry-run demo book. A signed-in wallet that has finished set-up
(agent approved) gets its own book, trading live through its own agent when LIVE_TRADING is on.
"""

import asyncio
import logging
import time
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import leaderboard
from app.auth import COOKIE, SESSION_S, Auth
from app.book import Book, TradeError, syn_pnl
from app.config import load_settings
from app.engine.market import MarketService
from app.engine.quote import build_quote
from app.hl.account import AGENT_NAME, WITHDRAW_FEE, HLAccount, build_action, signer_of, split_signature, typed_data
from app.hl.info import HLInfo
from app.hl.venue import DryRunVenue, LiveVenue
from app.store import Store
from app.terms import ACCEPT_LINE, TERMS, TERMS_HASH, TERMS_VERSION
from app.users import UserStore, _day, join_check, open_block

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("app")
WEB = Path(__file__).resolve().parent.parent / "web" / "index.html"
settings = load_settings()
state: dict = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    info = HLInfo(settings.base_url)
    market = MarketService(info)
    await market.start()
    venue = DryRunVenue(lambda: market.snapshot)          # the public demo book never touches a real account
    book = Book(settings, market, venue, Store(settings.data_dir))
    book.min_notional = 0.0                               # paper money: show and simulate any stake, $20 included
    book.start()
    state.update(info=info, market=market, book=book, venue=venue, users=UserStore(settings.data_dir),
                 auth=Auth(settings.data_dir), hl=HLAccount(settings.base_url), books={}, pending={})
    if settings.agent_key:
        log.warning("HL_AGENT_PRIVATE_KEY is no longer used: sign in with your wallet and finish Set up to trade live")
    for addr, u in state["users"].users.items():
        if u.get("agent_approved"):
            await start_user_book(addr)
    log.info("Cathena Convex ready · %s · live trading %s · %d user book(s)", settings.network, "ON" if settings.live else "off", len(state["books"]))
    yield
    await market.stop()
    await info.close()
    await state["hl"].close()


# ---------- users ----------
def stake_limit(addr: str) -> float:
    return settings.max_stake if addr == settings.owner else min(settings.user_max_stake, settings.max_stake)


async def start_user_book(addr: str) -> Book:
    if addr in state["books"]:
        return state["books"][addr]
    market = state["market"]
    if settings.live:
        builder = settings.builder_address if settings.builder_address and settings.builder_address != addr else None
        venue = LiveVenue(settings.base_url, state["users"].agent_key(addr), addr, lambda: market.snapshot, builder)
    else:
        venue = DryRunVenue(lambda: market.snapshot)
    book = Book(settings, market, venue, Store(settings.data_dir / "users" / addr), account=addr, max_stake=stake_limit(addr),
                max_open_risk=None if addr == settings.owner else settings.user_max_open_risk)
    book.open_block = lambda: open_block(state["users"].get(addr), settings, owner=addr == settings.owner)
    book.start()
    state["books"][addr] = book
    await refresh_user(addr)
    return book


async def refresh_user(addr: str) -> dict:
    """Read the user's approvals and account mode from Hyperliquid and push them into their venue/book."""
    hl, users = state["hl"], state["users"]
    u = users.ensure(addr)
    out = {}
    try:
        agents = await hl.agents(addr)
        out["agent_on_chain"] = bool(u.get("agent_address")) and any(a.get("address", "").lower() == u["agent_address"].lower() for a in agents)
        if settings.builder_address and settings.builder_address != addr:
            out["builder_fee_tenths_bp"] = await hl.max_builder_fee(addr, settings.builder_address)
        out["abstraction"] = await hl.abstraction(addr)
    except Exception as e:
        out["hl_error"] = f"{type(e).__name__}: {e}"
    if "builder_fee_tenths_bp" in out:
        users.update(addr, builder_fee_tenths_bp=out["builder_fee_tenths_bp"])
    book = state["books"].get(addr)
    if book:
        if hasattr(book.venue, "builder_ok_tenths_bp"):
            book.venue.builder_ok_tenths_bp = users.get(addr).get("builder_fee_tenths_bp", 0)
        book.unified = any(k in out.get("abstraction", "").lower() for k in ("unified", "portfolio"))
    return out


def who(req: Request) -> str | None:
    return state["auth"].address(req.cookies.get(COOKIE))


def book_for(req: Request) -> Book:
    addr = who(req)
    if not addr:
        return state["book"]
    if addr not in state["books"]:
        raise HTTPException(409, "Finish Set up (deposit and approve your trading key) before trading.")
    return state["books"][addr]


def may_join(addr: str, invite: str | None = None) -> tuple[str | None, str | None]:
    return join_check(addr, state["users"].users, settings, invite)


app = FastAPI(title="Cathena Convex", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)   # /docs is the product docs page
app.mount("/assets", StaticFiles(directory=WEB.parent / "assets"), name="assets")
MEDIA = WEB.parent.parent / "media"           # the videos, for /watch and for linking from a submission
if MEDIA.is_dir():
    app.mount("/media", StaticFiles(directory=MEDIA), name="media")


@app.api_route("/docs", methods=["GET", "HEAD"])
def docs():
    return FileResponse(WEB.parent / "docs.html", headers={"Cache-Control": "no-cache"})


@app.api_route("/docs.md", methods=["GET", "HEAD"])
def docs_md():
    return FileResponse(WEB.parent / "docs.md", media_type="text/markdown; charset=utf-8", headers={"Cache-Control": "no-cache"})


@app.api_route("/watch", methods=["GET", "HEAD"])
def watch(req: Request):
    """Video page. Link previews (X, Telegram) need absolute URLs, so the public origin is filled in per request."""
    origin = str(req.base_url).rstrip("/")
    html = (WEB.parent / "watch.html").read_text().replace("__ORIGIN__", origin)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.api_route("/buildercode", methods=["GET", "HEAD"])
def buildercode():
    """Builder-fee revenue page. Unlisted and not indexed; its data comes from an owner-only endpoint."""
    return FileResponse(WEB.parent / "buildercode.html", headers={"Cache-Control": "no-cache", "X-Robots-Tag": "noindex, nofollow"})


@app.api_route("/", methods=["GET", "HEAD"])
def index():
    return FileResponse(WEB, headers={"Cache-Control": "no-store"})


@app.api_route("/v1/health", methods=["GET", "HEAD"])
def health():
    m = state["market"]
    return {"ok": m.snapshot is not None and m.last_error is None, "live_trading": settings.live, "network": settings.network,
            "users": len(state["users"].users), "books": len(state["books"]), "market_error": m.last_error}


@app.get("/v1/market")
def market(req: Request):
    snap = state["market"].snapshot
    if not snap:
        raise HTTPException(503, "Market data not ready")
    addr = who(req)
    book = state["books"].get(addr) if addr else state["book"]
    mode = book.venue.mode if book else "setup"
    return {**snap.to_ui(), "mode": mode, "maxStake": book.max_stake if book else stake_limit(addr)}


_cache: dict = {}


async def _cached(key: str, ttl: float, fn):
    import time as _t
    hit = _cache.get(key)
    if hit and _t.time() - hit[0] < ttl:
        return hit[1]
    val = await fn()
    _cache[key] = (_t.time(), val)
    return val


@app.get("/v1/candles")
async def candles(interval: str = "15m", hours: int = 24):
    """BTC OHLC for the desktop chart."""
    if interval not in ("5m", "15m", "1h"):
        raise HTTPException(400, "interval must be 5m, 15m or 1h")
    hours = max(1, min(hours, 72))

    async def fetch():
        import time as _t
        end = int(_t.time() * 1000)
        rows = await state["info"].candles("BTC", interval, end - hours * 3_600_000, end)
        return [{"t": int(c["t"]), "o": float(c["o"]), "h": float(c["h"]), "l": float(c["l"]), "c": float(c["c"])} for c in rows]
    return await _cached(f"c:{interval}:{hours}", 20, fetch)


@app.get("/v1/ticker")
async def ticker():
    """Mid price and 24h change for the bottom ticker."""
    async def fetch():
        meta, ctxs = await state["info"].meta_and_ctxs()
        out = []
        for name in ("BTC", "ETH", "SOL", "HYPE"):
            i = next((k for k, a in enumerate(meta["universe"]) if a["name"] == name), None)
            if i is None:
                continue
            px, prev = float(ctxs[i]["markPx"]), float(ctxs[i]["prevDayPx"])
            out.append({"coin": name, "px": px, "chg": (px / prev - 1) if prev else 0.0})
        return out
    return await _cached("ticker", 5, fetch)


@app.get("/v1/quote")
def quote(s: int, stake: float, req: Request):
    book = state["books"].get(who(req) or "") or state["book"]
    q = build_quote(state["market"].snapshot, s, stake, book.min_notional)
    return q.to_dict() if q else {"s": 0}


class TradeIn(BaseModel):
    s: int = Field(ge=-100, le=100)
    stake: float = Field(gt=0)
    expected_max_loss: float | None = None


@app.post("/v1/trades")
async def trade(body: TradeIn, req: Request):
    book = book_for(req)
    try:
        return book.marked(await book.open(body.s, body.stake, body.expected_max_loss))
    except TradeError as e:
        raise HTTPException(409, str(e))


@app.get("/v1/positions")
def positions(req: Request):
    addr = who(req)
    if addr and addr not in state["books"]:
        return []
    return book_for(req).list()


@app.get("/v1/positions/{pid}/cashout")
def cashout_quote(pid: str, req: Request):
    book = book_for(req)
    try:
        pos = book._find(pid)
    except TradeError as e:
        raise HTTPException(404, str(e))
    if pos["status"] != "open":
        raise HTTPException(409, f"Position is {pos['status']}.")
    return book.cashout_quote(pos, book.snap)


@app.post("/v1/positions/{pid}/close")
async def close(pid: str, req: Request):
    book = book_for(req)
    try:
        return await book.close(pid)
    except TradeError as e:
        raise HTTPException(409, str(e))


@app.get("/v1/leaderboard")
def board(req: Request, window: str = "24h", demo: bool = False):
    demo = demo or os.getenv("DEMO_ACCOUNT", "").lower() in ("1", "true", "yes")
    me = who(req)
    traders = {a: b.list() for a, b in state["books"].items()}
    if not me or me not in traders:
        traders[me or "demo"] = state["book"].list() if not me else []
    return leaderboard.build(traders, me or "demo", window, demo)


DEMO_HL = {  # sample balances for recordings: real ones never leave the server in demo mode
    "perp_account_value": 1840.52, "perp_margin_used": 212.40, "perp_withdrawable": 1415.08,
    "perp_positions": [], "spot_balances": [{"coin": "USDC", "total": 1250.00, "hold": 0.0}],
}


@app.get("/v1/account")
async def account(req: Request, demo: bool = False):
    """Balances and P&L. Signed in: your own Hyperliquid account. Not signed in: the dry-run demo (sample balances with ?demo)."""
    addr, info = who(req), state["info"]
    demo = not addr and (demo or os.getenv("DEMO_ACCOUNT", "").lower() in ("1", "true", "yes"))
    book = state["books"].get(addr) if addr else state["book"]
    positions = book.positions if book else []
    open_pos = [book.marked(p) for p in positions if p["status"] == "open"]
    risk = sum(p.get("max_loss", 0) for p in open_pos)
    unreal = sum(p.get("mark", {}).get("pnl_close_now", 0) for p in open_pos)
    realized = sum(p.get("realized_pnl", 0) for p in positions if p["status"] in ("closed", "settled"))
    out = {"mode": book.venue.mode if book else "setup", "network": settings.network, "address": addr, "signed_in": bool(addr),
           "open_positions": len(open_pos), "open_risk": risk, "unrealized": unreal, "realized": realized,
           "activity": book.store.events(40) if book else []}
    if demo:
        out.update(address="0xC47h…3nA0", demo=True, hyperliquid=DEMO_HL)
    elif addr:
        try:
            out["hyperliquid"] = await hl_balances(addr)
        except Exception as e:
            out["hyperliquid_error"] = f"{type(e).__name__}: {e}"
    else:
        start = float(os.getenv("DRY_RUN_BALANCE", "1000"))
        out["dry_run"] = {"starting_balance": start, "equity": start + realized + unreal}
    return out


async def hl_balances(addr: str) -> dict:
    info = state["info"]
    ch = await info.clearinghouse(addr)
    sp = await info.spot_clearinghouse(addr)
    ms = ch.get("marginSummary", {})
    return {
        "perp_account_value": float(ms.get("accountValue", 0)), "perp_margin_used": float(ms.get("totalMarginUsed", 0)),
        "perp_withdrawable": float(ch.get("withdrawable", 0)),
        "perp_positions": [p["position"] for p in ch.get("assetPositions", [])],
        "spot_balances": [{"coin": b["coin"], "total": float(b["total"]), "hold": float(b["hold"])}
                          for b in sp.get("balances", []) if float(b["total"]) > 0],   # skip settled, empty outcome tokens
    }


# ---------- sign-in ----------
class SignIn(BaseModel):
    address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    signature: str | None = None
    invite: str | None = Field(default=None, max_length=64)
    accept_terms: bool = False


@app.get("/v1/terms")
def terms():
    return {"version": TERMS_VERSION, "hash": TERMS_HASH, "accept_line": ACCEPT_LINE, "items": [{"title": t, "body": b} for t, b in TERMS]}


@app.post("/v1/auth/challenge")
def auth_challenge(body: SignIn):
    addr = body.address.lower()
    if not body.accept_terms:
        raise HTTPException(422, "Please accept the early-access terms first.")
    why, _ = may_join(addr, body.invite)
    if why:
        raise HTTPException(403, why)
    return {"message": state["auth"].challenge(addr)}


@app.post("/v1/auth/verify")
def auth_verify(body: SignIn, resp: Response):
    addr = body.address.lower()
    why, code = may_join(addr, body.invite)
    if why:
        raise HTTPException(403, why)
    try:
        token, signed_msg = state["auth"].verify(addr, body.signature or "")
    except ValueError as e:
        raise HTTPException(401, str(e))
    state["users"].ensure(addr)
    # the signed sign-in message contains the terms line: keep it with the signature as proof of acceptance
    state["users"].update(addr, terms_version=TERMS_VERSION, terms_hash=TERMS_HASH, terms_accepted_at=int(time.time() * 1000),
                          terms_proof={"message": signed_msg, "signature": body.signature})
    if code:
        state["users"].update(addr, invite=code)
        log.info("new user %s joined with invite '%s'", addr, code)
    resp.set_cookie(COOKIE, token, max_age=SESSION_S, httponly=True, samesite="lax", secure=os.getenv("COOKIE_SECURE", "") == "1")
    return {"address": addr}


@app.post("/v1/auth/logout")
def auth_logout(resp: Response):
    resp.delete_cookie(COOKIE)
    return {"ok": True}


# ---------- set up: deposit, approve the trading key and the fee, fund both legs ----------
def need_user(req: Request) -> str:
    addr = who(req)
    if not addr:
        raise HTTPException(401, "Sign in with your wallet first.")
    return addr


@app.get("/v1/setup")
async def setup_status(req: Request):
    addr = who(req)
    if not addr:
        return {"signed_in": False, "live_trading": settings.live}
    chain = await refresh_user(addr)
    u = state["users"].public(addr)
    try:
        bal = await hl_balances(addr)
    except Exception as e:
        bal = {"error": f"{type(e).__name__}: {e}"}
    spot_usdc = next((b["total"] - b["hold"] for b in bal.get("spot_balances", []) if b["coin"] == "USDC"), 0.0)
    unified = any(k in chain.get("abstraction", "").lower() for k in ("unified", "portfolio"))
    self_builder = not settings.builder_address or settings.builder_address == addr
    fee_needed = int(max(settings.cashout_fee_binary_bps, settings.cashout_fee_perp_bps) * 10)
    funded = bal.get("perp_account_value", 0) + spot_usdc
    # deposit is a one-time set-up step: once an account has been funded (or has traded), a low balance means
    # "top up", not "finish set up"
    book = state["books"].get(addr)
    funded_once = bool(u.get("funded_once")) or bool(book and book.positions)
    if funded >= 1 and not u.get("funded_once"):
        state["users"].update(addr, funded_once=True)
        funded_once = True
    steps = {
        "deposit": funded >= 1 or funded_once,
        "agent": bool(u.get("agent_approved")) and chain.get("agent_on_chain", True),
        "builder": self_builder or u.get("builder_fee_tenths_bp", 0) >= fee_needed,
        "fund": unified or spot_usdc >= 1,
    }
    return {"signed_in": True, "address": addr, "live_trading": settings.live, "network": settings.network,
            "owner": addr == settings.owner, "steps": steps, "ready": all(steps.values()) and addr in state["books"],
            "agent_address": u.get("agent_address"), "agent_name": AGENT_NAME,
            "builder": None if self_builder else settings.builder_address, "builder_max_fee_rate": settings.builder_max_fee_rate,
            "builder_fee_tenths_bp": u.get("builder_fee_tenths_bp", 0), "fee_needed_tenths_bp": fee_needed,
            "abstraction": chain.get("abstraction"), "unified": unified, "hl_error": chain.get("hl_error"),
            "balances": {"perp_account_value": bal.get("perp_account_value", 0), "perp_withdrawable": bal.get("perp_withdrawable", 0), "spot_usdc": spot_usdc},
            "deposit": {**settings.deposit, "max": None if addr == settings.owner else settings.max_deposit},
            "max_stake": stake_limit(addr), "max_open_risk": None if addr == settings.owner else settings.user_max_open_risk,
            "blocked": open_block(state["users"].get(addr), settings, owner=addr == settings.owner),
            "invite": u.get("invite"), "access_ends": _access_end(u.get("invite")), "withdraw_fee": WITHDRAW_FEE}


def _access_end(code: str | None) -> str | None:
    end = next((e for c, _, e in settings.invite_codes if c == code), None) if code else None
    return _day(end) if end else None


class Prepare(BaseModel):
    kind: str = Field(pattern="^(approveAgent|approveBuilderFee|usdClassTransfer|withdraw3)$")
    chain_id: str = Field(pattern=r"^0x[0-9a-fA-F]+$")
    amount: float | None = Field(default=None, gt=0)
    to_perp: bool = False


@app.post("/v1/setup/prepare")
def setup_prepare(body: Prepare, req: Request):
    """Build the action for the user's wallet to sign. Nothing is sent until /submit."""
    addr = need_user(req)
    if body.kind == "approveAgent":
        fields = {"agentAddress": state["users"].new_agent(addr), "agentName": AGENT_NAME}
    elif body.kind == "approveBuilderFee":
        if not settings.builder_address or settings.builder_address == addr:
            raise HTTPException(409, "No builder fee to approve for this wallet.")
        fields = {"maxFeeRate": settings.builder_max_fee_rate, "builder": settings.builder_address}
    elif body.kind == "withdraw3":
        if not body.amount or body.amount <= WITHDRAW_FEE:
            raise HTTPException(422, f"Withdraw more than the ${WITHDRAW_FEE:.0f} Hyperliquid fee.")
        fields = {"destination": addr, "amount": f"{body.amount:.2f}"}     # only ever back to the signed-in wallet
    else:
        if not body.amount:
            raise HTTPException(422, "amount is required")
        fields = {"amount": f"{body.amount:.2f}", "toPerp": body.to_perp}
    action = build_action(body.kind, fields, mainnet=settings.is_mainnet, signature_chain_id=body.chain_id.lower())
    state["pending"][(addr, body.kind)] = action
    return {"kind": body.kind, "typed_data": typed_data(action)}


class Submit(BaseModel):
    kind: str = Field(pattern="^(approveAgent|approveBuilderFee|usdClassTransfer|withdraw3)$")
    signature: str = Field(pattern=r"^0x[0-9a-fA-F]{130}$")


@app.post("/v1/setup/submit")
async def setup_submit(body: Submit, req: Request):
    addr = need_user(req)
    action = state["pending"].pop((addr, body.kind), None)
    if not action:
        raise HTTPException(409, "Nothing to submit; start this step again.")
    sig = split_signature(body.signature)
    if signer_of(action, sig, settings.is_mainnet) != addr:
        raise HTTPException(401, "That signature isn't from your signed-in wallet.")
    res = await state["hl"].submit(action, sig)
    log.info("setup %s for %s → %s", body.kind, addr, res)
    if res.get("status") != "ok":
        raise HTTPException(409, f"Hyperliquid rejected it: {res.get('response', res)}")
    if body.kind == "approveAgent":
        state["users"].activate_agent(addr, action["agentAddress"], action["nonce"])
        old = state["books"].pop(addr, None)          # a new agent replaces the old one: rebuild the venue
        if old and old._task:
            old._task.cancel()
        await start_user_book(addr)
    else:
        await refresh_user(addr)
    return {"ok": True, "kind": body.kind}


@app.post("/v1/positions/close-all")
async def close_all(req: Request):
    """Cash out everything you hold (the first step of winding down and withdrawing)."""
    book = book_for(req)
    return {"closed": await book.close_all()}


# ---------- owner admin: who's in, pause/unpause, wind down an invite cohort ----------
def need_owner(req: Request) -> str:
    addr = who(req)
    if not addr or addr != settings.owner:
        raise HTTPException(403, "Owner only.")
    return addr


# builder-fee revenue: what Hyperliquid credits the builder address, and every user fill that paid it
REVENUE_SINCE_MS = 1790812800000      # 2026-10-01, before Cathena's first live trade
_revenue: dict = {"at": 0.0, "data": None}


@app.get("/v1/admin/builder-revenue")
async def builder_revenue(req: Request):
    need_owner(req)
    if _revenue["data"] and time.time() - _revenue["at"] < 60:
        return _revenue["data"]
    info, builder = state["info"], (settings.builder_address or settings.owner or "").lower()
    fills, errors = [], []
    for addr in list(state["users"].users):
        if addr.lower() == builder:
            continue                      # a builder never pays itself
        try:
            rows = await info.user_fills_by_time(addr, REVENUE_SINCE_MS)
        except Exception as e:
            errors.append(f"{addr[:6]}…{addr[-4:]}: {type(e).__name__}")
            continue
        for f in rows:
            fee = float(f.get("builderFee") or 0)
            if fee > 0:
                fills.append({"time": f["time"], "user": addr, "coin": f["coin"], "leg": "binary" if f["coin"].startswith("#") else "perp",
                              "dir": f.get("dir") or f.get("side"), "sz": float(f["sz"]), "px": float(f["px"]), "fee": fee, "hash": f.get("hash")})
    fills.sort(key=lambda x: -x["time"])
    by_day: dict[str, float] = {}
    for f in fills:
        day = time.strftime("%Y-%m-%d", time.gmtime(f["time"] / 1000))
        by_day[day] = by_day.get(day, 0.0) + f["fee"]
    try:
        ref = await info.referral(builder)
    except Exception as e:
        ref, errors = {}, errors + [f"referral: {type(e).__name__}"]
    data = {"builder": builder, "earned": float(ref.get("builderRewards") or 0), "unclaimed": float(ref.get("unclaimedRewards") or 0),
            "claimed": float(ref.get("claimedRewards") or 0), "claim_min": 1.0, "from_fills": sum(f["fee"] for f in fills),
            "by_day": sorted(by_day.items()), "fills": fills, "users": len(state["users"].users), "errors": errors, "updated": int(time.time() * 1000)}
    _revenue.update(at=time.time(), data=data)
    return data


@app.get("/v1/admin/users")
async def admin_users(req: Request):
    need_owner(req)
    users = state["users"]

    async def row(addr: str, u: dict) -> dict:
        book = state["books"].get(addr)
        positions = book.list() if book else []
        open_pos = [p for p in positions if p["status"] == "open"]
        try:
            bal = await hl_balances(addr)
            spot = next((b["total"] for b in bal["spot_balances"] if b["coin"] == "USDC"), 0.0)
            balance = round(bal["perp_account_value"] + spot, 2)
        except Exception:
            balance = None
        return {"address": addr, "invite": u.get("invite"), "created": u.get("created"), "disabled": bool(u.get("disabled")),
                "agent_approved": bool(u.get("agent_approved")), "terms_version": u.get("terms_version"), "balance": balance,
                "open_positions": len(open_pos), "at_risk": round(sum(p.get("max_loss", 0) for p in open_pos), 2),
                "pnl": round(sum(p.get("realized_pnl", 0) for p in positions if p["status"] in ("closed", "settled"))
                             + sum(p.get("mark", {}).get("cashout", {}).get("net_pnl", 0) for p in open_pos), 2),
                "blocked": open_block(u, settings, owner=addr == settings.owner)}

    rows = await asyncio.gather(*(row(a, u) for a, u in users.users.items()))
    codes = [{"code": c, "uses": sum(1 for u in users.users.values() if u.get("invite") == c), "max": n, "ends": _day(e) if e else None}
             for c, n, e in settings.invite_codes]
    return {"users": sorted(rows, key=lambda r: -(r["created"] or 0)), "invites": codes}


class AdminUser(BaseModel):
    disabled: bool


@app.post("/v1/admin/users/{addr}")
def admin_set_user(addr: str, body: AdminUser, req: Request):
    need_owner(req)
    addr = addr.lower()
    if not state["users"].get(addr):
        raise HTTPException(404, "No such user.")
    state["users"].update(addr, disabled=body.disabled)
    log.info("admin: %s %s", "paused" if body.disabled else "unpaused", addr)
    return state["users"].public(addr)


class WindDown(BaseModel):
    address: str | None = None
    invite: str | None = None
    disable: bool = True


@app.post("/v1/admin/wind-down")
async def admin_wind_down(body: WindDown, req: Request):
    """Close every open position for one user, or for everyone who joined with an invite code, and (by default) pause
    them. Their USDC stays in their own Hyperliquid account for them to withdraw: Cathena can't move it, by design."""
    need_owner(req)
    users = state["users"].users
    targets = [a for a, u in users.items()
               if (body.address and a == body.address.lower()) or (body.invite and u.get("invite") == body.invite.lower())]
    if not targets:
        raise HTTPException(404, "No matching users.")
    out = []
    for a in targets:
        if body.disable:
            state["users"].update(a, disabled=True)
        book = state["books"].get(a)
        out.append({"address": a, "closed": await book.close_all() if book else []})
    log.info("admin: wound down %d user(s) %s", len(out), body.invite or body.address)
    return {"users": out}
