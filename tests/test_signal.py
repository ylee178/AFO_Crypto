"""Signal Agent 테스트 — DB 기반 시그널 계산."""

import sqlite3
import json
import numpy as np
import pandas as pd
import pytest

from agents.signal import compute
from core.config import FORMATION_DAYS, VOL_LOOKBACK


class TestSignalCompute:
    def test_insufficient_data_returns_insufficient(self, tmp_db):
        """Not enough data → INSUFFICIENT_DATA reason."""
        conn = sqlite3.connect(tmp_db)
        # Only 5 rows — not enough for FORMATION_DAYS
        for i in range(5):
            conn.execute(
                """INSERT INTO market_bars (symbol, date, open, high, low, close, volume, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'test')""",
                ("BTC/USD", f"2026-01-{i+1:02d}", 70000, 71000, 69000, 70000, 100),
            )
        conn.commit()
        conn.close()

        results = compute(["BTC/USD"])
        assert len(results) == 1
        assert results[0].reason == "INSUFFICIENT_DATA"

    def test_trending_up_gives_vol_long(self, seeded_db):
        """With uptrending data, should get VOL_LONG or CRASH_FILTER_CASH."""
        # Seed with strongly uptrending data
        conn = sqlite3.connect(seeded_db)
        price = 70000.0
        for i in range(VOL_LOOKBACK + FORMATION_DAYS + 10):
            date = f"2026-03-{(i % 28) + 1:02d}" if i < 28 else f"2026-04-{(i - 28) % 28 + 1:02d}"
            # Use sequential dates properly
            from datetime import datetime, timedelta
            base = datetime(2026, 1, 1) + timedelta(days=i)
            date = base.strftime("%Y-%m-%d")
            price *= 1.005  # consistent uptrend
            conn.execute(
                """INSERT OR REPLACE INTO market_bars
                   (symbol, date, open, high, low, close, volume, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'test')""",
                ("BTC/USD", date, price * 0.99, price * 1.01, price * 0.98, price, 1000),
            )
        conn.commit()
        conn.close()

        results = compute(["BTC/USD"])
        assert len(results) == 1
        r = results[0]
        assert r.reason in ("VOL_LONG", "CRASH_FILTER_CASH")
        assert r.vol_scalar > 0
        assert 0 <= r.final_position <= 1.5  # within MAX_VOL_SCALE

    def test_multiple_symbols(self, seeded_db):
        """Should return results for all requested symbols."""
        results = compute(["BTC/USD", "ETH/USD"])
        assert len(results) == 2
        symbols = {r.symbol for r in results}
        assert symbols == {"BTC/USD", "ETH/USD"}

    def test_vol_scalar_bounds(self, seeded_db):
        """vol_scalar should be clamped between 0.1 and MAX_VOL_SCALE."""
        results = compute(["BTC/USD"])
        if results[0].reason != "INSUFFICIENT_DATA":
            assert 0.1 <= results[0].vol_scalar <= 1.5
