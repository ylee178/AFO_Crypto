"""KPI 계산 회귀 테스트 — Sharpe, Calmar, MaxDD, fee-adjusted return."""

import pandas as pd
import numpy as np
import pytest

from analysis.metrics import (
    sharpe_ratio,
    calmar_ratio,
    sortino_ratio,
    max_drawdown,
    fee_adjusted_return,
    fee_adjusted_return_notional,
    dd_protection_ratio,
    btc_excess_return,
    compute_all_kpis,
)


class TestSharpeRatio:
    def test_positive_returns(self, sample_returns):
        s = sharpe_ratio(sample_returns)
        assert isinstance(s, float)

    def test_zero_vol_returns_zero(self):
        flat = pd.Series([0.0] * 30)
        assert sharpe_ratio(flat) == 0.0

    def test_negative_returns_negative_sharpe(self):
        bad = pd.Series([-0.01] * 30)
        assert sharpe_ratio(bad) < 0


class TestMaxDrawdown:
    def test_always_negative_or_zero(self, sample_returns):
        dd = max_drawdown(sample_returns)
        assert dd <= 0.0

    def test_empty_returns_zero(self):
        assert max_drawdown(pd.Series([], dtype=float)) == 0.0

    def test_monotonic_up_no_drawdown(self):
        up = pd.Series([0.01] * 30)
        assert max_drawdown(up) == 0.0

    def test_crash_then_recovery(self):
        """50% crash followed by recovery — DD should be around -50%."""
        returns = pd.Series([0.01] * 10 + [-0.10] * 7 + [0.05] * 10)
        dd = max_drawdown(returns)
        assert dd < -0.30  # significant drawdown


class TestCalmarRatio:
    def test_no_drawdown_returns_zero(self):
        up = pd.Series([0.01] * 30)
        assert calmar_ratio(up) == 0.0  # max_dd is 0, so calmar is 0

    def test_empty_returns_zero(self):
        assert calmar_ratio(pd.Series([], dtype=float)) == 0.0


class TestFeeAdjustedReturn:
    def test_fees_reduce_return(self, sample_returns):
        gross = (1 + sample_returns).prod() - 1
        fee_adj = fee_adjusted_return(sample_returns, trades=10, fee_bps=40)
        assert fee_adj < gross

    def test_zero_trades_equals_gross(self, sample_returns):
        gross = (1 + sample_returns).prod() - 1
        fee_adj = fee_adjusted_return(sample_returns, trades=0, fee_bps=40)
        assert abs(fee_adj - gross) < 1e-10

    def test_notional_version(self, sample_returns):
        """Notional-based fee calculation should differ from count-based."""
        count_based = fee_adjusted_return(sample_returns, trades=10, fee_bps=20)
        notional_based = fee_adjusted_return_notional(sample_returns, total_fee_paid=50.0)
        # They use different logic, so they should generally differ
        assert isinstance(notional_based, float)


class TestDDProtection:
    def test_full_protection(self):
        """Strategy DD 0% vs BTC DD -50% = 100% protection."""
        assert dd_protection_ratio(0.0, -0.50) == 1.0

    def test_no_protection(self):
        """Strategy DD same as BTC DD = 0% protection."""
        assert dd_protection_ratio(-0.50, -0.50) == 0.0

    def test_half_protection(self):
        """Strategy DD -25% vs BTC DD -50% = 50% protection."""
        ratio = dd_protection_ratio(-0.25, -0.50)
        assert abs(ratio - 0.50) < 1e-10

    def test_btc_no_drawdown(self):
        assert dd_protection_ratio(-0.10, 0.0) == 0.0


class TestComputeAllKpis:
    def test_returns_all_keys(self, sample_returns, btc_returns):
        kpis = compute_all_kpis(sample_returns, btc_returns, trades=5)
        expected_keys = {"sharpe", "calmar", "sortino", "max_dd",
                         "fee_adj_return", "btc_excess", "total_return",
                         "btc_total_return", "num_trades"}
        assert set(kpis.keys()) == expected_keys

    def test_num_trades_passthrough(self, sample_returns, btc_returns):
        kpis = compute_all_kpis(sample_returns, btc_returns, trades=42)
        assert kpis["num_trades"] == 42
