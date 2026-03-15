"""Shared fixtures for Crypto AFO tests."""

import os
import sqlite3
import tempfile

import pytest
import pandas as pd
import numpy as np

from core.config import SYMBOLS


@pytest.fixture
def tmp_db(monkeypatch):
    """Create a temporary SQLite DB with full schema, monkeypatch DB_PATH."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    monkeypatch.setattr("core.config.DB_PATH", db_path)
    monkeypatch.setattr("core.db.DB_PATH", db_path)

    from core.db import init_db, migrate_v2
    init_db()
    migrate_v2()

    yield db_path

    os.unlink(db_path)


@pytest.fixture
def seeded_db(tmp_db):
    """tmp_db with 60 days of BTC and ETH price data."""
    conn = sqlite3.connect(tmp_db)

    np.random.seed(42)
    base_date = pd.Timestamp("2026-01-01")

    for sym, start_price in [("BTC/USD", 70000.0), ("ETH/USD", 2000.0)]:
        price = start_price
        for i in range(60):
            date = (base_date + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
            daily_return = np.random.normal(0.001, 0.03)
            price *= (1 + daily_return)
            conn.execute(
                """INSERT OR REPLACE INTO market_bars
                   (symbol, date, open, high, low, close, volume, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'test')""",
                (sym, date, price * 0.99, price * 1.01, price * 0.98, price, 1000.0),
            )
    conn.commit()
    conn.close()
    return tmp_db


@pytest.fixture
def sample_returns():
    """60 days of synthetic daily returns."""
    np.random.seed(42)
    return pd.Series(np.random.normal(0.001, 0.02, 60))


@pytest.fixture
def btc_returns():
    """60 days of synthetic BTC returns (higher vol)."""
    np.random.seed(99)
    return pd.Series(np.random.normal(0.0005, 0.04, 60))
