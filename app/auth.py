"""Wallet sign-in: the user signs a one-time message (no gas, moves nothing) and gets an HMAC-signed session cookie.
Privy replaces this later; everything downstream only needs `current_address(request)`."""

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_defunct

from app.terms import ACCEPT_LINE
from app.users import load_secret

COOKIE = "cathena_session"
SESSION_S = 7 * 86_400
NONCE_S = 300


class Auth:
    def __init__(self, data_dir: Path):
        self._key = load_secret("SESSION_SECRET", data_dir / ".session_secret", lambda: secrets.token_hex(32).encode())
        self._nonces: dict[str, tuple[str, float]] = {}

    def challenge(self, address: str) -> str:
        a = address.lower()
        nonce = secrets.token_hex(8)
        msg = (f"Sign in to Cathena\n\nAddress: {a}\nNonce: {nonce}\nIssued: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n\n"
               f"{ACCEPT_LINE}\n\nThis proves you own this wallet. It costs no gas and cannot move funds.")
        self._nonces[a] = (msg, time.time())
        return msg

    def verify(self, address: str, signature: str) -> tuple[str, str]:
        """Check the signature over the issued challenge. Returns (session token, the exact message that was signed)."""
        a = address.lower()
        msg, t = self._nonces.pop(a, (None, 0))
        if not msg or time.time() - t > NONCE_S:
            raise ValueError("Sign-in expired, try again.")
        signer = Account.recover_message(encode_defunct(text=msg), signature=signature).lower()
        if signer != a:
            raise ValueError("Signature does not match this wallet.")
        exp = int(time.time()) + SESSION_S
        body = f"{a}|{exp}"
        return body + "|" + hmac.new(self._key, body.encode(), hashlib.sha256).hexdigest(), msg

    def address(self, token: str | None) -> str | None:
        try:
            a, exp, mac = (token or "").split("|")
        except ValueError:
            return None
        good = hmac.new(self._key, f"{a}|{exp}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(good, mac) or int(exp) < time.time():
            return None
        return a
