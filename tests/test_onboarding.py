"""Set-up flow: wallet sign-in, user-signed Hyperliquid actions built for the browser, and agent keys at rest."""

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from hyperliquid.utils.signing import sign_agent, sign_approve_builder_fee, sign_usd_class_transfer_action, sign_withdraw_from_bridge_action

from app.auth import Auth
from app.hl.account import build_action, signer_of, split_signature, typed_data
from app.users import UserStore

WALLET = Account.from_key("0x" + "11" * 32)


def wallet_sign(typed: dict) -> str:
    """What MetaMask's eth_signTypedData_v4 returns for the payload we hand it."""
    return "0x" + Account.sign_typed_data(WALLET.key, full_message=typed).signature.hex().removeprefix("0x")


@pytest.mark.parametrize("kind, fields, sdk_sign", [
    ("approveAgent", {"agentAddress": "0x" + "ab" * 20, "agentName": "cathena"}, sign_agent),
    ("approveBuilderFee", {"maxFeeRate": "0.5%", "builder": "0x9a3ce1c68a9798d812adf47206799b5f6d98a3cc"}, sign_approve_builder_fee),
    ("usdClassTransfer", {"amount": "10.00", "toPerp": False}, sign_usd_class_transfer_action),
    ("withdraw3", {"destination": "0x" + "ab" * 20, "amount": "12.50"}, sign_withdraw_from_bridge_action),
])
def test_browser_signature_matches_the_sdk_and_recovers_the_user(kind, fields, sdk_sign):
    action = build_action(kind, fields, mainnet=True, signature_chain_id="0x66eee")
    sig = split_signature(wallet_sign(typed_data(action)))
    sdk = sdk_sign(WALLET, {k: v for k, v in action.items() if k not in ("signatureChainId", "hyperliquidChain")}, True)
    assert [int(sig[k], 16) for k in "rs"] + [sig["v"]] == [int(sdk[k], 16) for k in "rs"] + [sdk["v"]]   # same signature Hyperliquid verifies
    assert signer_of(action, sig, True) == WALLET.address.lower()


def test_signature_from_another_wallet_is_caught():
    action = build_action("approveAgent", {"agentAddress": "0x" + "ab" * 20, "agentName": "cathena"}, mainnet=True, signature_chain_id="0xa4b1")
    other = Account.from_key("0x" + "22" * 32)
    sig = split_signature("0x" + Account.sign_typed_data(other.key, full_message=typed_data(action)).signature.hex().removeprefix("0x"))
    assert signer_of(action, sig, True) != WALLET.address.lower()


def test_typed_data_message_has_only_signed_fields():
    action = build_action("approveBuilderFee", {"maxFeeRate": "0.5%", "builder": "0x" + "cd" * 20}, mainnet=False, signature_chain_id="0xa4b1")
    td = typed_data(action)
    assert set(td["message"]) == {"hyperliquidChain", "maxFeeRate", "builder", "nonce"}
    assert td["domain"]["chainId"] == 0xa4b1 and td["message"]["hyperliquidChain"] == "Testnet"


def test_sign_in_round_trip(tmp_path):
    auth = Auth(tmp_path)
    addr = WALLET.address.lower()
    msg = auth.challenge(addr)
    sig = Account.sign_message(encode_defunct(text=msg), WALLET.key).signature.hex()
    token, signed = auth.verify(addr, sig)
    assert auth.address(token) == addr and signed == msg
    from app.terms import ACCEPT_LINE
    assert ACCEPT_LINE in msg                                                        # the wallet signs the terms acceptance
    assert auth.address(token[:-1] + ("0" if token[-1] != "0" else "1")) is None   # tampered cookie
    with pytest.raises(ValueError):
        auth.verify(addr, sig)                                                       # nonce is single-use


def test_sign_in_rejects_someone_elses_signature(tmp_path):
    auth = Auth(tmp_path)
    msg = auth.challenge(WALLET.address)
    other = Account.from_key("0x" + "22" * 32)
    with pytest.raises(ValueError):
        auth.verify(WALLET.address, Account.sign_message(encode_defunct(text=msg), other.key).signature.hex())


def test_agent_key_encrypted_at_rest(tmp_path):
    users = UserStore(tmp_path)
    agent = users.new_agent(WALLET.address)
    users.activate_agent(WALLET.address, agent, 1)
    key = users.agent_key(WALLET.address)
    assert Account.from_key(key).address == agent
    raw = (tmp_path / "users.json").read_text()
    assert key.removeprefix("0x") not in raw and "agent_key_enc" not in users.public(WALLET.address)
    assert UserStore(tmp_path).agent_key(WALLET.address) == key                     # survives a restart via the secret file
    assert oct((tmp_path / ".agent_key_secret").stat().st_mode)[-3:] == "600"


def test_new_agent_stays_pending_until_approved(tmp_path):
    users = UserStore(tmp_path)
    first = users.new_agent(WALLET.address)
    users.activate_agent(WALLET.address, first, 1)
    key = users.agent_key(WALLET.address)
    second = users.new_agent(WALLET.address)                   # user started a re-approval but never signed
    assert users.agent_key(WALLET.address) == key and users.get(WALLET.address)["agent_approved"]
    with pytest.raises(KeyError):
        users.activate_agent(WALLET.address, first, 2)         # only the pending agent can be activated
    users.activate_agent(WALLET.address, second, 3)
    assert Account.from_key(users.agent_key(WALLET.address)).address == second


def test_join_rules_with_invite_codes():
    from types import SimpleNamespace
    from app.users import join_check
    s = SimpleNamespace(owner="0xowner", allowed_addresses=("0xfriend",), max_users=1, invite_codes=(("colosseum", 2, None),))
    users = {}
    assert join_check("0xstranger", users, s)[0].startswith("Cathena is invite-only")
    assert join_check("0xstranger", users, s, "nope")[0] == "That invite code isn't valid."
    assert join_check("0xJudge1", users, s, "Colosseum") == (None, "colosseum")        # case-insensitive
    users["0xjudge1"] = {"invite": "colosseum"}
    users["0xjudge2"] = {"invite": "colosseum"}
    assert "fully used" in join_check("0xjudge3", users, s, "colosseum")[0]
    assert join_check("0xjudge1", users, s) == (None, None)                           # existing users sign back in without the code
    assert join_check("0xfriend", users, s) == (None, None)                           # invitees don't use up the address-list cap
    users["0xfriend"] = {}
    open_beta = SimpleNamespace(**{**vars(s), "allowed_addresses": (), "open_beta": True})
    assert "spots are taken" in join_check("0xother", users, open_beta)[0]           # 1 list user = MAX_USERS reached
    assert join_check("0xowner", users, s) == (None, None)


def test_invite_codes_parse_with_end_dates():
    from app.config import _invites
    (a, b) = _invites("Colosseum-K7:30:2026-11-30, judges")
    assert a[:2] == ("colosseum-k7", 30) and b == ("judges", 25, None)
    from datetime import datetime, timezone
    assert datetime.fromtimestamp(a[2] / 1000, timezone.utc).isoformat() == "2026-12-01T00:00:00+00:00"   # valid through 30 Nov


def test_expired_invite_blocks_new_accounts_and_new_trades_but_not_existing_sign_in():
    import time
    from types import SimpleNamespace
    from app.users import join_check, open_block
    past = int(time.time() * 1000) - 1000
    s = SimpleNamespace(owner="0xowner", allowed_addresses=(), max_users=10, invite_codes=(("colosseum", 25, past), ("live", 25, None)))
    users = {"0xjudge": {"invite": "colosseum"}}
    assert "ended on" in join_check("0xnew", users, s, "colosseum")[0]
    assert join_check("0xjudge", users, s) == (None, None)                     # can still sign in to cash out and withdraw
    assert "access ended" in open_block(users["0xjudge"], s)
    assert open_block({"invite": "live"}, s) is None
    assert "paused" in open_block({"invite": "live", "disabled": True}, s)
    assert open_block({"disabled": True}, s, owner=True) is None


def test_empty_allow_list_means_owner_and_invites_only():
    from types import SimpleNamespace
    from app.users import join_check
    from app.config import load_settings
    import os
    s = SimpleNamespace(owner="0xowner", allowed_addresses=(), open_beta=False, max_users=10, invite_codes=(("k7", 5, None),))
    assert "invite-only" in join_check("0xanyone", {}, s)[0]
    assert join_check("0xowner", {}, s) == (None, None)
    assert join_check("0xanyone", {}, s, "k7") == (None, "k7")
    old = os.environ.get("ALLOWED_ADDRESSES")
    try:
        os.environ["ALLOWED_ADDRESSES"] = ""                      # the blank line in .env.example
        assert load_settings().open_beta is False
        os.environ["ALLOWED_ADDRESSES"] = "*"
        assert load_settings().open_beta is True and load_settings().allowed_addresses == ()
    finally:
        if old is None: os.environ.pop("ALLOWED_ADDRESSES", None)
        else: os.environ["ALLOWED_ADDRESSES"] = old
