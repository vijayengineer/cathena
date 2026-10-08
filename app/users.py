"""Users: one Hyperliquid wallet each, with a Cathena-generated agent key (encrypted at rest) and their own book.

users.json holds public state plus the encrypted agent key. The key-encryption secret comes from AGENT_KEY_SECRET,
or a file created once in DATA_DIR with owner-only permissions. Lose that secret and the agents must be re-approved.
"""

import json
import logging
import os
import secrets
import tempfile
import time
from pathlib import Path

from cryptography.fernet import Fernet
from eth_account import Account

log = logging.getLogger("users")


def load_secret(env: str, path: Path, make) -> bytes:
    if os.getenv(env):
        return os.getenv(env).encode()
    if path.exists():
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    val = make()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(val)
    log.warning("created %s: back it up (or set %s); without it stored agent keys can't be decrypted", path, env)
    return val


def _day(end_ms: int) -> str:
    """Last valid day of an access window that ends at end_ms (midnight UTC after it)."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(end_ms / 1000 - 1, timezone.utc).strftime("%-d %b %Y")


def open_block(user: dict | None, settings, owner: bool = False) -> str | None:
    """Why this user may not open new trades right now (None = allowed). Cashing out and withdrawing always stay open."""
    if owner or not user:
        return None
    if user.get("disabled"):
        return "Your account is paused. You can still cash out and withdraw."
    code = user.get("invite")
    end = next((e for c, _, e in settings.invite_codes if c == code), None) if code else None
    if end and time.time() * 1000 >= end:
        return f"Your '{code}' access ended on {_day(end)}. You can still cash out and withdraw."
    return None


def join_check(addr: str, users: dict[str, dict], settings, invite: str | None = None) -> tuple[str | None, str | None]:
    """May this wallet sign in? Returns (refusal reason or None, invite code to record or None).

    Existing users and the owner always may. Otherwise: a valid invite code with uses left (its own cap), or
    the address list (or anyone with ALLOWED_ADDRESSES=*) within MAX_USERS. Empty list = owner and invites only."""
    a, code = addr.lower(), (invite or "").strip().lower()
    if a == settings.owner or a in users:
        return None, None
    codes = {c: (n, end) for c, n, end in settings.invite_codes}
    if code:
        if code not in codes:
            return "That invite code isn't valid.", None
        uses, end = codes[code]
        if end and time.time() * 1000 >= end:
            return f"The '{code}' invite ended on {_day(end)}.", None
        if sum(1 for u in users.values() if u.get("invite") == code) >= uses:
            return f"The '{code}' invite has been fully used.", None
        return None, code
    list_users = sum(1 for u in users.values() if not u.get("invite"))
    if not getattr(settings, "open_beta", False) and a not in settings.allowed_addresses:
        return f"Cathena is invite-only for now, and {a} isn't on the list. Use an invite link, or ask for an invite with this address.", None
    if list_users >= settings.max_users:
        return f"All {settings.max_users} early-access spots are taken.", None
    return None, None


class UserStore:
    def __init__(self, data_dir: Path):
        self.dir = data_dir
        self.path = data_dir / "users.json"
        self._box = Fernet(load_secret("AGENT_KEY_SECRET", data_dir / ".agent_key_secret", Fernet.generate_key))
        self.users: dict[str, dict] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def _save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(self.users, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def get(self, address: str) -> dict | None:
        return self.users.get(address.lower())

    def ensure(self, address: str) -> dict:
        a = address.lower()
        if a not in self.users:
            self.users[a] = {"address": a, "created": int(time.time() * 1000), "agent_address": None, "agent_key_enc": None,
                             "agent_approved": False, "builder_fee_tenths_bp": 0}
            self._save()
        return self.users[a]

    def new_agent(self, address: str) -> str:
        """Generate a candidate agent key. It stays pending (the approved agent keeps trading) until approval succeeds."""
        u = self.ensure(address)
        acct = Account.from_key("0x" + secrets.token_hex(32))
        u.update(pending_agent_address=acct.address, pending_agent_key_enc=self._box.encrypt(acct.key.hex().encode()).decode())
        self._save()
        return acct.address

    def activate_agent(self, address: str, agent_address: str, approved_at: int) -> None:
        """Hyperliquid accepted the approval: the pending agent becomes the trading agent."""
        u = self.ensure(address)
        if (u.get("pending_agent_address") or "").lower() != agent_address.lower():
            raise KeyError("approved agent doesn't match the pending one")
        u.update(agent_address=u.pop("pending_agent_address"), agent_key_enc=u.pop("pending_agent_key_enc"),
                 agent_approved=True, agent_approved_at=approved_at)
        self._save()

    def agent_key(self, address: str) -> str:
        u = self.get(address)
        if not u or not u.get("agent_key_enc"):
            raise KeyError("no agent for this user")
        return self._box.decrypt(u["agent_key_enc"].encode()).decode()

    def update(self, address: str, **fields) -> dict:
        u = self.ensure(address)
        u.update(fields)
        self._save()
        return u

    def public(self, address: str) -> dict:
        u = self.get(address) or {}
        return {k: v for k, v in u.items() if not k.endswith("_key_enc")}
