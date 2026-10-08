"""The server engine must match the web page: these numbers come from the page's JS on the same snapshot."""

import pytest

from app.engine.market import Snapshot
from app.engine.quote import build_quote, min_tradable_stake, risk_pct

SNAP = Snapshot(
    ts=1791225560691, outcome=8596, target=85749.0, expiry=1791266400000,
    yes_asks=[[0.40735, 21.0], [0.40958, 13.0], [0.414, 1000.0], [0.42, 61.0], [0.4225, 35.0], [0.42686, 5000.0], [0.42687, 4526.0], [0.43, 37.0]],
    yes_bid=0.39568,
    no_asks=[[0.60432, 19.0], [0.60433, 19.0], [0.60434, 5000.0], [0.609, 1000.0], [0.61, 70.0], [0.61043, 13.0], [0.61425, 4637.0], [0.615, 26.0]],
    no_bid=0.59265, perp_bid=85604.0, perp_ask=85605.0, perp_mark=85601.0, funding=1.25e-05, max_lev=40, sz_decimals=5,
    sigma1d=0.01919)


def test_risk_curve():
    assert [risk_pct(s) for s in (0, 20, 70, 94, -80)] == [0, 25, 40, 50, 45]
    assert [risk_pct(s) for s in (95, 96, 98, -99, 100, -100)] == [50, 60, 80, 90, 100, 100]   # All in


def test_all_in_risks_the_whole_stake_on_the_binary():
    q = build_quote(SNAP, 100, 200)
    assert q.risk_pct == 100 and q.units == 0 and q.syn_budget == 0
    assert q.max_loss == pytest.approx(200, abs=1)                   # everything on the line
    assert q.payoff(q.target * 0.98) == pytest.approx(-q.bin_cost)  # wrong side: lose it all
    assert q.payoff(q.target * 1.02) == pytest.approx(q.bin_n - q.bin_cost)
    bear = build_quote(SNAP, -100, 200)
    assert not bear.bull and bear.max_loss == pytest.approx(200, abs=1)


def test_default_dial_matches_page():
    q = build_quote(SNAP, 70, 1000)
    assert q.bin_n == 847 and q.bin_cost == pytest.approx(350.46, abs=0.01)
    assert q.strike == 86000 and q.units == pytest.approx(0.1346, abs=1e-4)
    assert q.syn_budget == pytest.approx(49.19, abs=0.01)
    assert q.max_loss == pytest.approx(399.65, abs=0.01)
    assert (round(q.lev_now, 1), round(q.lev_full, 1)) == (4.2, 11.5)
    assert q.payoff(q.spot * 1.03) == pytest.approx(744, abs=1)


def test_bear_and_edges():
    q = build_quote(SNAP, -80, 500)
    assert not q.bull and q.max_loss == pytest.approx(225, abs=1) and round(q.lev_full, 1) == 12.5
    assert build_quote(SNAP, 0, 1000) is None
    small = build_quote(SNAP, 20, 100)          # 25% of $100 → a $25 binary
    assert small.bin_n == 60 and small.units < 0.001   # page shows 0.2x: a token synthetic
    tiny = build_quote(SNAP, 5, 40)             # 20% of $40 = $8 < $10 minimum
    assert tiny.bin_n == 0 and tiny.units > 0 and any("runs as the option" in w for w in tiny.warnings)   # $8 binary: the option takes over


def test_max_loss_never_exceeds_risk_budget():
    for s in range(-100, 101, 5):
        for stake in (100, 250, 1000, 2500):
            q = build_quote(SNAP, s, stake)
            if q:
                assert q.max_loss <= stake * q.risk_pct / 100 + 1e-6


def test_binary_minimum_is_checked_at_mid_and_too_small_trades_are_flagged():
    mid = (SNAP.yes_bid + SNAP.yes_asks[0][0]) / 2
    q = build_quote(SNAP, 25, 100)                 # $30 max loss: a binary that clears $10 at mid
    assert q.bin_n * mid >= 10
    small = build_quote(SNAP, 25, 30)              # $9 max loss: under the minimum
    assert small.bin_n == 0 and small.units > 0 and any("runs as the option" in w for w in small.warnings)
    assert build_quote(SNAP, 25, small.min_stake).bin_n * mid >= 10   # a big enough stake buys the binary again


def test_min_tradable_stake_is_exact():
    for s in (10, 25, 55, 88, 97, 100, -40, -100):
        m = min_tradable_stake(SNAP, s)
        q, under = build_quote(SNAP, s, m), build_quote(SNAP, s, m - 1)
        assert q.bin_n > 0 or q.units > 0                  # at the minimum, something tradable is bought
        assert under.bin_n == 0 and under.units == 0       # a dollar less and nothing is


def test_ten_dollars_trades_every_setting_but_all_in():
    for s in (10, 25, 55, 88, 94, -25, -88):
        q = build_quote(SNAP, s, 10)
        assert q.units > 0 and q.units * abs(q.delta0) * q.spot >= 10   # the option alone clears the perp minimum
        assert q.max_loss <= 10 * q.risk_pct / 100 + 0.01              # and never risks more than the dial says
    assert build_quote(SNAP, 100, 10).units == 0                       # All in: no spare cash for margin


def test_small_stakes_risk_and_payoff_rise_together():
    for side in (1, -1):
        qs = [build_quote(SNAP, side * s, 10) for s in (10, 25, 55, 70, 88, 94)]
        move = lambda q: q.spot * (1 + side * 0.03)
        losses, wins = [q.max_loss for q in qs], [q.payoff(move(q)) for q in qs]
        assert all(q.bin_n == 0 and q.units > 0 for q in qs)
        assert losses == sorted(losses) and wins == sorted(wins)      # bolder never risks less or pays less
        assert losses[-1] > losses[0] and wins[-1] > wins[0]


def test_moonshot_is_off_when_the_line_is_already_on_your_side():
    from dataclasses import replace
    rich_no = replace(SNAP, no_asks=[[0.94, 5000.0]], no_bid=0.93)          # BTC already well below the line
    q = build_quote(rich_no, -100, 20)
    assert q.blocked and "at most +6%" in q.blocked and q.bin_n == 0 and q.units == 0 and q.max_loss == 0
    assert not build_quote(rich_no, -94, 20).blocked                     # Aggressive is still on
    assert not build_quote(SNAP, -100, 20).blocked and not build_quote(SNAP, 100, 20).blocked   # 0.60 / 0.41: Moonshot on
