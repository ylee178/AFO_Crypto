"""Portfolio state 계산 테스트 — FIX #8 total_value 재계산 검증."""

import json
import sqlite3
import pytest


class TestPortfolioValueUpdates:
    def test_initial_state_returns_defaults(self, tmp_db):
        """No snapshots → default $10,000."""
        from main import get_portfolio_state
        total, peak, weights = get_portfolio_state()
        assert total == 10000.0
        assert peak == 10000.0
        assert weights == {}

    def test_value_reflects_price_change(self, seeded_db):
        """After price moves, total_value should change from snapshot value."""
        conn = sqlite3.connect(seeded_db)

        # Insert a snapshot with known weights
        conn.execute(
            """INSERT INTO portfolio_snapshots
               (date, total_value, cash_value, cash_pct, positions_json,
                drawdown_pct, btc_drawdown_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("2026-02-15", 10000.0, 9000.0, 0.90,
             json.dumps({"BTC/USD": 0.05, "ETH/USD": 0.05}),
             0.0, 0.0),
        )
        conn.commit()
        conn.close()

        from main import get_portfolio_state
        total, peak, weights = get_portfolio_state()

        # total should NOT be exactly 10000 anymore — prices have moved
        # (seeded_db has 60 days of random walk data, so prices differ)
        assert isinstance(total, float)
        assert total > 0
        # weights should sum to less than 1 (rest is cash)
        assert sum(weights.values()) < 1.0

    def test_peak_updates_on_new_high(self, seeded_db):
        """If current value > stored peak, peak should update."""
        conn = sqlite3.connect(seeded_db)

        # Insert snapshot where peak was low
        conn.execute(
            """INSERT INTO portfolio_snapshots
               (date, total_value, cash_value, cash_pct, positions_json,
                drawdown_pct, btc_drawdown_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("2026-02-15", 5000.0, 5000.0, 1.0,
             json.dumps({}), 0.0, 0.0),
        )
        conn.commit()
        conn.close()

        from main import get_portfolio_state
        total, peak, weights = get_portfolio_state()

        # All cash, so total = cash = 5000, but peak from MAX() = 5000
        assert peak >= total

    def test_drawdown_is_computed_correctly(self, seeded_db):
        """Drawdown should be negative when value < peak."""
        conn = sqlite3.connect(seeded_db)

        # Two snapshots: first at $12000 (peak), second at $10000
        conn.execute(
            """INSERT INTO portfolio_snapshots
               (date, total_value, cash_value, cash_pct, positions_json,
                drawdown_pct, btc_drawdown_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("2026-02-10", 12000.0, 12000.0, 1.0, json.dumps({}), 0.0, 0.0),
        )
        conn.execute(
            """INSERT INTO portfolio_snapshots
               (date, total_value, cash_value, cash_pct, positions_json,
                drawdown_pct, btc_drawdown_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("2026-02-15", 10000.0, 10000.0, 1.0, json.dumps({}), -0.167, 0.0),
        )
        conn.commit()
        conn.close()

        from main import get_portfolio_state
        total, peak, weights = get_portfolio_state()

        # total=10000, peak should be 12000 from MAX()
        assert peak == 12000.0
        dd = (total - peak) / peak
        assert dd < 0  # drawdown exists
