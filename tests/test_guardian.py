"""Guardian Agent 회귀 테스트 — kill switches, position threshold, sell-before-buy."""

import pytest

from core.models import SignalResult, PositionTarget
from core.config import (
    MAX_PORTFOLIO_DRAWDOWN,
    MAX_SINGLE_DAY_LOSS,
    MAX_SINGLE_POSITION_WEIGHT,
    POSITION_THRESHOLD,
)
from agents.guardian import check


def _make_signal(symbol, final_position, momentum=0.05, is_trending=True):
    return SignalResult(
        symbol=symbol, date="2026-01-01",
        momentum_score=momentum, is_trending=is_trending,
        realized_vol=0.08, vol_scalar=final_position if final_position > 0 else 0.1,
        final_position=final_position,
        reason="VOL_LONG" if final_position > 0 else "CRASH_FILTER_CASH",
    )


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_db):
    """All guardian tests need a DB with executions table for turnover check."""
    pass


class TestKillSwitchDD:
    def test_dd_below_limit_forces_cash(self):
        """Portfolio DD > -20% → all positions go to 0."""
        signals = [_make_signal("BTC/USD", 0.5), _make_signal("ETH/USD", 0.5)]
        # peak=10000, value=7500 → DD = -25%
        targets = check(signals, portfolio_value=7500, peak_value=10000)
        assert all(t.target_weight == 0.0 for t in targets)
        assert all(t.reason == "DD_LIMIT" for t in targets)

    def test_dd_within_limit_passes(self):
        """Portfolio DD -10% is within limit, should not trigger."""
        signals = [_make_signal("BTC/USD", 0.5)]
        targets = check(signals, portfolio_value=9000, peak_value=10000)
        assert not any(t.reason == "DD_LIMIT" for t in targets)


class TestKillSwitchAssetCrash:
    def test_single_asset_crash_forces_cash(self):
        """Single asset -20% daily → force to cash."""
        signals = [_make_signal("BTC/USD", 0.5)]
        daily_returns = {"BTC/USD": -0.20}
        targets = check(signals, portfolio_value=10000, peak_value=10000,
                        daily_returns=daily_returns)
        btc = [t for t in targets if t.symbol == "BTC/USD"][0]
        assert btc.target_weight == 0.0
        assert btc.reason == "RISK_OVERRIDE_CASH"

    def test_normal_daily_return_passes(self):
        """Normal -3% daily return should not trigger."""
        signals = [_make_signal("BTC/USD", 0.5)]
        daily_returns = {"BTC/USD": -0.03}
        targets = check(signals, portfolio_value=10000, peak_value=10000,
                        daily_returns=daily_returns)
        btc = [t for t in targets if t.symbol == "BTC/USD"][0]
        assert btc.reason != "RISK_OVERRIDE_CASH"


class TestPositionThreshold:
    def test_small_delta_skipped(self):
        """Position change < 5% threshold → skip execution."""
        signals = [_make_signal("BTC/USD", 0.50)]
        # guardian computes target_weight = min(1/1, 0.40) * 0.50 = 0.20
        # so current must be close to 0.20 for delta < 0.05
        current_weights = {"BTC/USD": 0.19}  # delta = 0.01 < 0.05
        targets = check(signals, portfolio_value=10000, peak_value=10000,
                        current_weights=current_weights)
        btc = [t for t in targets if t.symbol == "BTC/USD"][0]
        assert btc.execute is False
        assert btc.reason == "THRESHOLD_SKIP"

    def test_large_delta_executes(self):
        """Position change > 5% threshold → execute."""
        signals = [_make_signal("BTC/USD", 0.50)]
        current_weights = {"BTC/USD": 0.30}  # delta = 0.20 > 0.05
        targets = check(signals, portfolio_value=10000, peak_value=10000,
                        current_weights=current_weights)
        btc = [t for t in targets if t.symbol == "BTC/USD"][0]
        assert btc.execute is True

    def test_new_position_always_executes(self):
        """New position (0 → any) always executes regardless of threshold."""
        signals = [_make_signal("BTC/USD", 0.03)]  # small but new
        current_weights = {}
        targets = check(signals, portfolio_value=10000, peak_value=10000,
                        current_weights=current_weights)
        btc = [t for t in targets if t.symbol == "BTC/USD"][0]
        assert btc.execute is True

    def test_exit_position_always_executes(self):
        """Exit position (any → 0) always executes."""
        signals = [_make_signal("BTC/USD", 0.0, momentum=-0.05, is_trending=False)]
        current_weights = {"BTC/USD": 0.30}
        targets = check(signals, portfolio_value=10000, peak_value=10000,
                        current_weights=current_weights)
        btc = [t for t in targets if t.symbol == "BTC/USD"][0]
        assert btc.execute is True
        assert btc.target_weight == 0.0


class TestMaxPositionWeight:
    def test_single_position_capped(self):
        """Single asset vol_scalar=2.0 should be capped at MAX_SINGLE_POSITION_WEIGHT."""
        signals = [_make_signal("BTC/USD", 2.0)]
        targets = check(signals, portfolio_value=10000, peak_value=10000)
        btc = [t for t in targets if t.symbol == "BTC/USD"][0]
        assert btc.target_weight <= MAX_SINGLE_POSITION_WEIGHT


class TestCrashFilterCash:
    def test_non_trending_goes_to_cash(self):
        """Momentum crash filter → cash position."""
        signals = [_make_signal("BTC/USD", 0.0, momentum=-0.10, is_trending=False)]
        targets = check(signals, portfolio_value=10000, peak_value=10000)
        btc = [t for t in targets if t.symbol == "BTC/USD"][0]
        assert btc.target_weight == 0.0
        assert btc.reason == "CRASH_FILTER_CASH"
