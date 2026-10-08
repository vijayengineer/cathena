"""The owner-only builder-revenue endpoint: refuses everyone else, and adds up builder fees from user fills."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.main as m

BUILDER = "0x9a3ce1c68a9798d812adf47206799b5f6d98a3cc"
USER = "0x1607265a81a3e35ab5058afbfa261534a72a062a"


class FakeInfo:
    async def user_fills_by_time(self, user, start_ms):
        assert user != BUILDER                                   # a builder never pays itself: not even queried
        return [{"time": 1791407374000, "coin": "BTC", "dir": "Close Long", "side": "A", "sz": "0.00134", "px": "83345.0", "builderFee": "0.055841", "hash": "0xabc"},
                {"time": 1791410900000, "coin": "#94250", "side": "A", "sz": "32.0", "px": "0.06", "builderFee": "0.0096"},
                {"time": 1791410000000, "coin": "BTC", "dir": "Open Short", "side": "A", "sz": "0.00126", "px": "83232.0"}]   # opening: no fee

    async def referral(self, user):
        assert user == BUILDER
        return {"builderRewards": "0.19775849", "unclaimedRewards": "0.19775849", "claimedRewards": "0.0"}


def test_builder_revenue_is_owner_only_and_sums_fees(monkeypatch):
    m._revenue.update(at=0.0, data=None)
    monkeypatch.setitem(m.state, "info", FakeInfo())
    monkeypatch.setitem(m.state, "users", SimpleNamespace(users={USER: {}, BUILDER: {}}))
    monkeypatch.setattr(m, "settings", SimpleNamespace(builder_address=BUILDER, owner=BUILDER))
    monkeypatch.setattr(m, "who", lambda req: USER)
    with pytest.raises(HTTPException) as e:
        asyncio.run(m.builder_revenue(None))
    assert e.value.status_code == 403
    monkeypatch.setattr(m, "who", lambda req: BUILDER)
    d = asyncio.run(m.builder_revenue(None))
    assert len(d["fills"]) == 2 and d["from_fills"] == pytest.approx(0.065441)
    assert d["earned"] == pytest.approx(0.19775849) and d["claim_min"] == 1.0
    assert d["fills"][0]["leg"] == "binary" and d["fills"][1]["leg"] == "perp"   # newest first
    assert d["by_day"] == [("2026-10-07", pytest.approx(0.065441))]
