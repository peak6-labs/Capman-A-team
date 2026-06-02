"""Tests for the dip order/position layer (signal -> maker order intent)."""

from __future__ import annotations

from decimal import Decimal as D

from kalshi_agent_trader.dip_orders import OpenPosition, PositionBook, intent_for
from kalshi_agent_trader.reversion import Anchor, DipParams, assess

ANC = Anchor("c", "Fav", "men", D("0.5"), D("0.80"), D("0.40"))  # C=0.5, pre-match 80/40
P = DipParams(p_revert=D("0.85"))


def _sig(match_bid, match_ask, title_bid, title_ask, peak=D("0")):
    return assess(ANC, match_bid=D(match_bid), match_ask=D(match_ask),
                  title_bid=D(title_bid), title_ask=D(title_ask),
                  params=P, peak_residual=peak, title_ticker="KXFOMEN-26-FAV")


def test_enter_is_a_maker_bid_on_yes():
    s = _sig("0.79", "0.81", "0.24", "0.25")        # deep dip, match holding
    assert s.action == "BUY DIP" and s.stake > 0
    it = intent_for(s, None, P)
    assert it.kind == "enter" and it.side == "yes" and it.maker is True
    assert it.price == D("0.24")                     # rests at the bid, not the ask
    assert it.count >= 1


def test_no_entry_when_not_a_dip():
    s = _sig("0.79", "0.81", "0.39", "0.40")         # title ~ fair -> WATCH
    assert s.action != "BUY DIP"
    assert intent_for(s, None, P) is None


def test_no_double_entry_while_holding():
    s = _sig("0.79", "0.81", "0.24", "0.25")
    held = OpenPosition("KXFOMEN-26-FAV", D("0.24"), D("100"), D("0.40"))
    assert intent_for(s, held, P) is None            # still dipping -> hold, don't add


def test_take_profit_buys_no_as_maker():
    s = _sig("0.79", "0.81", "0.39", "0.40", peak=D("0.15"))   # reverted to fair
    assert s.action == "REVERTED"
    held = OpenPosition("KXFOMEN-26-FAV", D("0.24"), D("100"), D("0.40"))
    it = intent_for(s, held, P)
    assert it.kind == "exit" and it.side == "no" and it.maker is True
    assert it.price == D("0.60")                     # 1 - fair_target, rests the take-profit
    assert it.count == 100


def test_stop_buys_no_as_taker():
    s = _sig("0.19", "0.21", "0.10", "0.12", peak=D("0.15"))   # match cratered -> STOP
    assert s.action == "STOP"
    held = OpenPosition("KXFOMEN-26-FAV", D("0.24"), D("100"), D("0.40"))
    it = intent_for(s, held, P)
    assert it.kind == "exit" and it.side == "no" and it.maker is False  # cross out


def test_position_book_roundtrip():
    book = PositionBook()
    s = _sig("0.79", "0.81", "0.24", "0.25")
    assert book.get(s.title_ticker) is None
    book.on_enter(s)
    p = book.get(s.title_ticker)
    assert p is not None and p.contracts == s.contracts and p.entry_price == D("0.24")
    book.on_exit(s.title_ticker)
    assert book.get(s.title_ticker) is None
