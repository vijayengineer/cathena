"""Runtime settings, read once from the environment (.env)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from hyperliquid.utils import constants

load_dotenv()


def _flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    network: str                 # "mainnet" | "testnet"
    account_address: str | None  # your main Hyperliquid address (read-only use)
    agent_key: str | None        # approved API (agent) wallet key: can trade, cannot withdraw
    live: bool                   # False = dry run: orders are simulated against the live book
    max_stake: float             # hard cap per trade, USDC
    data_dir: Path
    cashout_fee_binary_bps: float = 50.0   # Cathena fee on an early cash-out: binary sale (HL allows up to 100 bps on spot/outcomes)
    cashout_fee_perp_bps: float = 5.0      # ... and on closing the perp leg (HL allows up to 10 bps on perps)
    builder_address: str | None = None     # set to collect the fee on-chain as a Hyperliquid builder fee
    builder_max_fee_rate: str = "0.5%"     # what each user approves for the builder once (covers the 50 bps binary fee)
    allowed_addresses: tuple[str, ...] = ()  # extra wallets allowed to sign in (lowercase), besides the owner and invite links
    open_beta: bool = False                  # ALLOWED_ADDRESSES=* : any wallet may join, up to max_users
    max_users: int = 10
    invite_codes: tuple[tuple[str, int, int | None], ...] = ()   # (code, max uses, ends ms): any wallet with the code may join, outside max_users
    user_max_stake: float = 20.0           # per-trade cap for everyone except the owner
    user_max_open_risk: float = 20.0       # cap on the summed max loss of a user's open positions (owner exempt)
    max_deposit: float = 20.0              # deposit cap shown and enforced in the app, except for the owner
    secret_dir: Path = Path("data")        # holds the key-encryption and session secrets if not given in the environment

    @property
    def owner(self) -> str | None:
        return self.account_address.lower() if self.account_address else None

    @property
    def is_mainnet(self) -> bool:
        return self.network != "testnet"

    # Hyperliquid deposits: USDC sent to the Bridge2 contract on Arbitrum credits the sender's HL account (min 5 USDC; less is lost)
    @property
    def deposit(self) -> dict:
        if self.is_mainnet:
            return {"chain_id": "0xa4b1", "chain_name": "Arbitrum One", "usdc": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
                    "bridge": os.getenv("HL_BRIDGE_ADDRESS", "0x2Df1c51E09aECF9cacB7bc98cB1742757f163dF7"), "min": 5.0, "trade_min": 10.0}
        return {"chain_id": "0x66eee", "chain_name": "Arbitrum Sepolia", "usdc": os.getenv("HL_TESTNET_USDC", ""),
                "bridge": os.getenv("HL_BRIDGE_ADDRESS", ""), "min": 5.0, "trade_min": 10.0}

    @property
    def base_url(self) -> str:
        return constants.TESTNET_API_URL if self.network == "testnet" else constants.MAINNET_API_URL

    @property
    def can_trade_live(self) -> bool:
        return self.live and bool(self.agent_key) and bool(self.account_address)


def _invites(raw: str) -> tuple[tuple[str, int, int | None], ...]:
    """"colosseum:25:2026-11-30,judges:10" → (code, uses, end of that UTC day in ms or None). Default 25 uses, no end."""
    from datetime import datetime, timedelta, timezone
    out = []
    for part in raw.split(","):
        code, n, ends = (part.strip().split(":") + ["", ""])[:3]
        if code:
            end_ms = None
            if ends:
                d = datetime.strptime(ends, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
                end_ms = int(d.timestamp() * 1000)
            out.append((code.lower(), int(n or 25), end_ms))
    return tuple(out)


def load_settings() -> Settings:
    return Settings(
        network=os.getenv("HL_NETWORK", "mainnet"),
        account_address=os.getenv("HL_ACCOUNT_ADDRESS") or None,
        agent_key=os.getenv("HL_AGENT_PRIVATE_KEY") or None,
        live=_flag("LIVE_TRADING"),
        max_stake=float(os.getenv("MAX_STAKE_USDC", "100")),
        data_dir=Path(os.getenv("DATA_DIR", "data")),
        cashout_fee_binary_bps=float(os.getenv("CASHOUT_FEE_BINARY_BPS", "50")),
        cashout_fee_perp_bps=float(os.getenv("CASHOUT_FEE_PERP_BPS", "5")),
        builder_address=(os.getenv("BUILDER_ADDRESS") or "").lower() or None,
        builder_max_fee_rate=os.getenv("BUILDER_MAX_FEE_RATE", "0.5%"),
        allowed_addresses=tuple(a.strip().lower() for a in os.getenv("ALLOWED_ADDRESSES", "").split(",") if a.strip() and a.strip() != "*"),
        open_beta=os.getenv("ALLOWED_ADDRESSES", "").strip() == "*",
        max_users=int(os.getenv("MAX_USERS", "10")),
        invite_codes=_invites(os.getenv("INVITE_CODES", "")),
        user_max_stake=float(os.getenv("USER_MAX_STAKE_USDC", "20")),
        user_max_open_risk=float(os.getenv("USER_MAX_OPEN_RISK_USDC", "20")),
        max_deposit=float(os.getenv("MAX_DEPOSIT_USDC", "20")),
        secret_dir=Path(os.getenv("DATA_DIR", "data")),
    )
