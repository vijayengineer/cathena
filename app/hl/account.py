"""Account set-up actions that only the user's own wallet may sign (Hyperliquid "user-signed actions").

The server builds the EIP-712 typed data, the browser wallet signs it, the server checks the signer is the user
and forwards the action to /exchange. The server never holds the user's main key.
"""

import time

import httpx
from hyperliquid.utils.signing import recover_user_from_user_signed_action, user_signed_payload

APPROVE_AGENT = ("HyperliquidTransaction:ApproveAgent", [
    {"name": "hyperliquidChain", "type": "string"},
    {"name": "agentAddress", "type": "address"},
    {"name": "agentName", "type": "string"},
    {"name": "nonce", "type": "uint64"},
])
APPROVE_BUILDER = ("HyperliquidTransaction:ApproveBuilderFee", [
    {"name": "hyperliquidChain", "type": "string"},
    {"name": "maxFeeRate", "type": "string"},
    {"name": "builder", "type": "address"},
    {"name": "nonce", "type": "uint64"},
])
USD_CLASS_TRANSFER = ("HyperliquidTransaction:UsdClassTransfer", [
    {"name": "hyperliquidChain", "type": "string"},
    {"name": "amount", "type": "string"},
    {"name": "toPerp", "type": "bool"},
    {"name": "nonce", "type": "uint64"},
])
WITHDRAW = ("HyperliquidTransaction:Withdraw", [
    {"name": "hyperliquidChain", "type": "string"},
    {"name": "destination", "type": "string"},
    {"name": "amount", "type": "string"},
    {"name": "time", "type": "uint64"},
])
KINDS = {"approveAgent": APPROVE_AGENT, "approveBuilderFee": APPROVE_BUILDER, "usdClassTransfer": USD_CLASS_TRANSFER, "withdraw3": WITHDRAW}
WITHDRAW_FEE = 1.0   # Hyperliquid charges $1 to withdraw to Arbitrum
AGENT_NAME = "cathena"


def build_action(kind: str, fields: dict, *, mainnet: bool, signature_chain_id: str) -> dict:
    """The full action as Hyperliquid expects it (type + fields + chain markers)."""
    nonce = int(time.time() * 1000)
    action = {"type": kind, **fields, ("time" if kind == "withdraw3" else "nonce"): nonce,
              "signatureChainId": signature_chain_id, "hyperliquidChain": "Mainnet" if mainnet else "Testnet"}
    return action


def typed_data(action: dict) -> dict:
    """EIP-712 payload for eth_signTypedData_v4. The message carries only the signed fields."""
    primary, types = KINDS[action["type"]]
    return user_signed_payload(primary, types, action) | {"message": {t["name"]: action[t["name"]] for t in types}}


def split_signature(sig_hex: str) -> dict:
    h = sig_hex[2:] if sig_hex.startswith("0x") else sig_hex
    if len(h) != 130:
        raise ValueError("signature must be 65 bytes")
    v = int(h[128:130], 16)
    return {"r": "0x" + h[:64], "s": "0x" + h[64:128], "v": v + 27 if v < 27 else v}


def signer_of(action: dict, sig: dict, mainnet: bool) -> str:
    primary, types = KINDS[action["type"]]
    return recover_user_from_user_signed_action(dict(action), sig, types, primary, mainnet).lower()


class HLAccount:
    """/exchange for user-signed actions plus the /info queries that confirm them."""

    def __init__(self, base_url: str):
        self._c = httpx.AsyncClient(base_url=base_url, timeout=15)

    async def close(self) -> None:
        await self._c.aclose()

    async def submit(self, action: dict, sig: dict) -> dict:
        nonce = action.get("nonce", action.get("time"))
        r = await self._c.post("/exchange", json={"action": action, "nonce": nonce, "signature": sig, "vaultAddress": None})
        r.raise_for_status()
        return r.json()

    async def _info(self, body: dict):
        r = await self._c.post("/info", json=body)
        r.raise_for_status()
        return r.json()

    async def agents(self, user: str) -> list[dict]:
        return await self._info({"type": "extraAgents", "user": user}) or []

    async def max_builder_fee(self, user: str, builder: str) -> int:
        """Approved builder fee in tenths of a basis point (0 = none)."""
        return int(await self._info({"type": "maxBuilderFee", "user": user, "builder": builder}) or 0)

    async def abstraction(self, user: str) -> str:
        try:
            return str(await self._info({"type": "userAbstraction", "user": user}))
        except httpx.HTTPError:
            return "unknown"
