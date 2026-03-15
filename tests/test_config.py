"""Config & symbol conversion 테스트."""

from core.config import (
    SYMBOLS, FORMATION_DAYS, VOL_LOOKBACK, VOL_TARGET,
    MAX_VOL_SCALE, POSITION_THRESHOLD,
    MAX_PORTFOLIO_DRAWDOWN, MAX_SINGLE_DAY_LOSS,
    MAX_SINGLE_POSITION_WEIGHT,
    to_binance_symbol, from_binance_symbol,
)


class TestSymbolConversion:
    def test_btc_to_binance(self):
        assert to_binance_symbol("BTC/USD") == "BTCUSDT"

    def test_eth_to_binance(self):
        assert to_binance_symbol("ETH/USD") == "ETHUSDT"

    def test_btc_from_binance(self):
        assert from_binance_symbol("BTCUSDT") == "BTC/USD"

    def test_eth_from_binance(self):
        assert from_binance_symbol("ETHUSDT") == "ETH/USD"

    def test_roundtrip(self):
        for sym in SYMBOLS:
            assert from_binance_symbol(to_binance_symbol(sym)) == sym


class TestConfigSanity:
    """Guard against accidental config changes that would break the strategy."""

    def test_symbols_has_btc_and_eth(self):
        assert "BTC/USD" in SYMBOLS
        assert "ETH/USD" in SYMBOLS

    def test_formation_days_positive(self):
        assert FORMATION_DAYS > 0

    def test_vol_target_reasonable(self):
        assert 0.01 < VOL_TARGET < 1.0

    def test_max_vol_scale_above_one(self):
        assert MAX_VOL_SCALE >= 1.0

    def test_position_threshold_small(self):
        assert 0 < POSITION_THRESHOLD < 0.20

    def test_drawdown_limit_negative(self):
        assert MAX_PORTFOLIO_DRAWDOWN < 0

    def test_single_day_loss_negative(self):
        assert MAX_SINGLE_DAY_LOSS < 0

    def test_max_weight_below_one(self):
        assert 0 < MAX_SINGLE_POSITION_WEIGHT <= 1.0
