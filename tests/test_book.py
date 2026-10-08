"""Average-cost accounting for the synthetic's perp leg."""

import pytest

from app.book import _apply_fill, syn_pnl
from app.engine.market import PERP_TAKER_FEE


def fresh():
    return {"held": 0.0, "avg_entry": 0.0, "realized": 0.0, "fees": 0.0}


def test_scale_in_then_out_realises_pnl():
    s = fresh()
    _apply_fill(s, 0.01, 86000)
    _apply_fill(s, 0.01, 87000)
    assert s["held"] == pytest.approx(0.02) and s["avg_entry"] == pytest.approx(86500)
    _apply_fill(s, -0.015, 88000)
    assert s["realized"] == pytest.approx(0.015 * 1500)
    assert s["held"] == pytest.approx(0.005) and s["avg_entry"] == pytest.approx(86500)
    fees = (0.01 * 86000 + 0.01 * 87000 + 0.015 * 88000) * PERP_TAKER_FEE
    assert s["fees"] == pytest.approx(fees)
    assert syn_pnl(s, 88000) == pytest.approx(0.02 * 1500 - fees)


def test_flip_short_to_long_resets_entry():
    s = fresh()
    _apply_fill(s, -0.01, 86000)
    _apply_fill(s, 0.03, 85000)
    assert s["realized"] == pytest.approx(0.01 * 1000)
    assert s["held"] == pytest.approx(0.02) and s["avg_entry"] == pytest.approx(85000)


def test_flat_resets():
    s = fresh()
    _apply_fill(s, 0.01, 86000)
    _apply_fill(s, -0.01, 86100)
    assert s["held"] == 0 and s["avg_entry"] == 0 and s["realized"] == pytest.approx(1.0)


def test_cashout_quote_charges_fees_and_spread():
    from types import SimpleNamespace
    from app.book import Book
    snap = SimpleNamespace(yes_bid=0.60, no_bid=0.38, perp_bid=86000.0, perp_ask=86001.0)
    settings = SimpleNamespace(cashout_fee_binary_bps=50.0, cashout_fee_perp_bps=5.0)
    book = Book.__new__(Book)
    book.settings = settings
    book.venue = SimpleNamespace(mode="dry_run")
    syn = {"held": 0.02, "avg_entry": 85000.0, "realized": 0.0, "fees": 0.5, "budget": 30.0}
    pos = {"bull": True, "binary": {"n": 100, "cost": 45.0}, "synthetic": syn}
    q = Book.cashout_quote(book, pos, snap)
    perp_notional = 0.02 * 86000
    assert q["binary_value"] == pytest.approx(60.0)
    assert q["fee_binary"] == pytest.approx(60.0 * 0.005)
    assert q["fee_perp"] == pytest.approx(perp_notional * 0.0005)
    syn_after = 0.02 * 1000 - 0.5 - perp_notional * PERP_TAKER_FEE
    assert q["net_pnl"] == pytest.approx(60 - 45 + syn_after - q["fee"])
    assert q["you_get_back"] == pytest.approx(75 + q["net_pnl"])


def test_live_cashout_fee_only_when_builder_approval_covers_it():
    from types import SimpleNamespace
    from app.book import Book
    book = Book.__new__(Book)
    book.settings = SimpleNamespace(cashout_fee_binary_bps=50.0, cashout_fee_perp_bps=5.0)
    book.venue = SimpleNamespace(mode="live", builder="0xb", builder_ok_tenths_bp=0)
    assert book.fee_bps() == (0.0, 0.0)                       # not approved: no fee, and no builder on the order
    book.venue.builder_ok_tenths_bp = 100                     # 0.1% approved: covers the perp fee only
    assert book.fee_bps() == (0.0, 5.0)
    book.venue.builder_ok_tenths_bp = 500                     # 0.5%: covers both
    assert book.fee_bps() == (50.0, 5.0)


def test_open_risk_cap_blocks_trades_past_the_limit():
    import asyncio, time
    from types import SimpleNamespace
    from app.book import Book, TradeError
    import app.book as bk
    book = Book.__new__(Book)
    book.max_stake, book.max_open_risk, book.open_block, book.min_notional = 20.0, 20.0, lambda: None, 10.0
    book.venue = SimpleNamespace(mode="dry_run")
    book.market = SimpleNamespace(snapshot=SimpleNamespace(ts=time.time() * 1000, hours_left=10))
    book.positions = [{"status": "open", "max_loss": 9.0}, {"status": "opening", "max_loss_quoted": 6.0}, {"status": "closed", "max_loss": 50.0}]
    real = bk.build_quote
    bk.build_quote = lambda snap, s, stake, *a: SimpleNamespace(max_loss=6.0, bin_n=20, units=0.0, min_stake=0, blocked="")       # 9 + 6 open + 6 new = 21 > 20
    try:
        with pytest.raises(TradeError, match=r"\$5.00 left"):
            asyncio.run(book.open(50, 20))
    finally:
        bk.build_quote = real


def test_demo_book_shows_real_numbers_for_small_stakes():
    from app.engine.quote import build_quote
    from tests.test_quote import SNAP
    real, demo = build_quote(SNAP, 25, 20), build_quote(SNAP, 25, 20, 0.0)
    assert real.bin_n == 0                                   # live: under Hyperliquid's $10 minimum
    assert demo.bin_n > 0 and demo.max_loss == pytest.approx(6, abs=0.5)   # demo: a real $20 Steady position


def test_simulated_positions_are_retired_when_live_starts(tmp_path):
    from types import SimpleNamespace
    from app.book import Book
    from app.store import Store
    store = Store(tmp_path)
    store.save_positions([{"id": "a", "mode": "dry_run", "status": "open", "stake": 20},
                          {"id": "b", "mode": "live", "status": "open", "stake": 20},
                          {"id": "c", "mode": "dry_run", "status": "closed", "stake": 20}])
    book = Book(SimpleNamespace(account_address=None, max_stake=100), None, SimpleNamespace(mode="live"), store)
    st = {p["id"]: p["status"] for p in book.positions}
    assert st == {"a": "void", "b": "open", "c": "closed"}          # only the simulated open one, and it's saved
    assert {p["id"]: p["status"] for p in Store(tmp_path).load_positions()}["a"] == "void"


def test_moonshot_needs_eleven_dollars():
    import asyncio
    from types import SimpleNamespace
    from app.book import Book, TradeError
    book = Book.__new__(Book)
    book.max_stake, book.open_block, book.min_notional = 20.0, lambda: None, 10.0
    book.market = SimpleNamespace(snapshot=None)
    with pytest.raises(TradeError, match=r"Moonshot starts at \$11"):
        asyncio.run(book.open(100, 10))
    with pytest.raises(TradeError, match=r"Moonshot starts at \$11"):
        asyncio.run(book.open(-97, 10.5))


def _buyer(levels, fills):
    """A Book with just enough wiring for _buy_binary: a live-looking book and a venue that returns `fills` in turn."""
    from types import SimpleNamespace
    from app.book import Book
    from app.hl.venue import Fill
    book = Book.__new__(Book)
    book.min_notional = 10.0
    sent = []
    async def l2_book(coin):
        return {"levels": [[{"px": "0.0575", "sz": "88"}], [{"px": str(p), "sz": str(s)} for p, s in levels]]}
    async def buy_outcome(coin, n, limit):
        sent.append((n, limit))
        f = fills.pop(0)
        return Fill(min(f, n), 0.071, "filled" if f else "none")
    book.market = SimpleNamespace(info=SimpleNamespace(l2_book=l2_book))
    book.venue = SimpleNamespace(buy_outcome=buy_outcome)
    book.store = SimpleNamespace(event=lambda *a, **k: None)
    return book, sent


def test_binary_buy_reprices_off_a_fresh_book_and_stays_in_budget():
    import asyncio
    from types import SimpleNamespace
    # quoted at 6.7c for $12 (179 contracts); the book has since moved to 7.1c
    q = SimpleNamespace(outcome_coin="#94250", bull=True, bin_n=179, bin_cost=11.99, bin_avg=0.067, max_budget=12.0, syn_budget=0.0)
    book, sent = _buyer([(0.071, 1000)], [10_000])
    n, cost, err, _ = asyncio.run(book._buy_binary("p1", q))
    (size, limit), = sent
    assert limit > 0.071                                # room over the fresh ask, so it fills
    assert size * limit <= q.max_budget + 1e-9          # and can never spend more than the binary budget
    assert n == size and cost == size * 0.071


def test_binary_partial_fill_gets_a_second_try_and_big_moves_are_refused():
    import asyncio
    from types import SimpleNamespace
    from app.book import TradeError
    q = SimpleNamespace(outcome_coin="#94250", bull=True, bin_n=280, bin_cost=20.0, bin_avg=0.0675, max_budget=20.0, syn_budget=0.0)
    book, sent = _buyer([(0.071, 1000)], [100, 10_000])
    n, cost, _, _ = asyncio.run(book._buy_binary("p2", q))
    assert len(sent) == 2 and n > 100 and cost <= q.bin_cost + 1e-9
    book, sent = _buyer([(0.09, 1000)], [10_000])        # 6.75c → 9c is a 33% jump: ask the user again
    with pytest.raises(TradeError, match="price moved"):
        asyncio.run(book._buy_binary("p3", q))
    assert not sent


def test_binary_buy_uses_half_a_percent_and_fills_a_thin_book_level_by_level():
    import asyncio
    from types import SimpleNamespace
    # today's real shape: a few contracts per level near the top, a wide spread (bid 4.8c)
    q = SimpleNamespace(outcome_coin="#94250", bull=True, bin_n=200, bin_cost=11.99, bin_avg=0.06, max_budget=12.0, syn_budget=0.0)
    book, sent = _buyer([(0.05733, 13), (0.0575, 84), (0.06267, 32), (0.06399, 2599)], [10_000])
    n, cost, err, _ = asyncio.run(book._buy_binary("p4", q))
    (size, limit), = sent
    assert err is None and n == size and size >= 180                     # most of the budget, not a timid slice
    assert limit == round(0.06399 * 1.005, 5)                             # 0.5% over the worst level it needs
