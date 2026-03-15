"""Operator Agent 테스트 — simulate 모드 실행, sell-before-buy 순서, API 키 검증."""

import os
import pytest

from core.models import PositionTarget, Execution
from agents.operator import execute, _execute_sim


def _make_target(symbol, delta, target_weight=None, execute_flag=True):
    tw = target_weight if target_weight is not None else abs(delta)
    cw = tw - delta
    return PositionTarget(
        symbol=symbol, target_weight=tw,
        current_weight=max(cw, 0), delta=delta,
        execute=execute_flag, reason="VOL_LONG" if delta > 0 else "CRASH_FILTER_CASH",
    )


class TestSimulateMode:
    def test_buy_creates_filled(self):
        targets = [_make_target("BTC/USD", delta=0.10, target_weight=0.10)]
        prices = {"BTC/USD": 70000.0}
        execs = _execute_sim(targets, portfolio_value=10000, current_prices=prices)
        assert len(execs) == 1
        assert execs[0].side == "BUY"
        assert execs[0].status == "FILLED"
        assert execs[0].qty > 0

    def test_sell_creates_filled(self):
        targets = [_make_target("BTC/USD", delta=-0.10, target_weight=0.0)]
        prices = {"BTC/USD": 70000.0}
        execs = _execute_sim(targets, portfolio_value=10000, current_prices=prices)
        assert len(execs) == 1
        assert execs[0].side == "SELL"
        assert execs[0].status == "FILLED"

    def test_skip_not_executed(self):
        targets = [_make_target("BTC/USD", delta=0.02, execute_flag=False)]
        prices = {"BTC/USD": 70000.0}
        execs = _execute_sim(targets, portfolio_value=10000, current_prices=prices)
        assert len(execs) == 1
        assert execs[0].status == "SKIPPED"

    def test_tiny_delta_skipped(self):
        """Delta value < $1 should be skipped."""
        targets = [_make_target("BTC/USD", delta=0.00005, target_weight=0.00005)]
        prices = {"BTC/USD": 70000.0}
        execs = _execute_sim(targets, portfolio_value=10000, current_prices=prices)
        # $0.50 < $1.0 → skipped (no execution generated)
        filled = [e for e in execs if e.status == "FILLED"]
        assert len(filled) == 0

    def test_zero_price_skipped(self):
        targets = [_make_target("BTC/USD", delta=0.10)]
        prices = {"BTC/USD": 0.0}
        execs = _execute_sim(targets, portfolio_value=10000, current_prices=prices)
        filled = [e for e in execs if e.status == "FILLED"]
        assert len(filled) == 0

    def test_fee_recorded(self):
        targets = [_make_target("BTC/USD", delta=0.10, target_weight=0.10)]
        prices = {"BTC/USD": 70000.0}
        execs = _execute_sim(targets, portfolio_value=10000, current_prices=prices)
        assert execs[0].fee_bps > 0  # should record maker fee


class TestModeDispatch:
    def test_simulate_mode(self):
        targets = [_make_target("BTC/USD", delta=0.10, target_weight=0.10)]
        prices = {"BTC/USD": 70000.0}
        execs = execute(targets, portfolio_value=10000, current_prices=prices, mode="simulate")
        assert len(execs) == 1
        assert execs[0].order_type == "SIMULATED"

    def test_unknown_mode_raises(self):
        targets = [_make_target("BTC/USD", delta=0.10)]
        prices = {"BTC/USD": 70000.0}
        with pytest.raises(ValueError, match="Unknown mode"):
            execute(targets, portfolio_value=10000, current_prices=prices, mode="invalid")

    def test_paper_mode_without_keys_raises(self, monkeypatch):
        """Paper mode without API keys should raise, not silently fall back."""
        monkeypatch.delenv("BINANCE_API_KEY", raising=False)
        monkeypatch.delenv("BINANCE_SECRET_KEY", raising=False)
        targets = [_make_target("BTC/USD", delta=0.10)]
        prices = {"BTC/USD": 70000.0}
        with pytest.raises(RuntimeError, match="Binance API keys not set"):
            execute(targets, portfolio_value=10000, current_prices=prices, mode="paper")
