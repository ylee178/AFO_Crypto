import sqlite3
import os
from core.config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_bars (
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,
    open        REAL NOT NULL,
    high        REAL NOT NULL,
    low         REAL NOT NULL,
    close       REAL NOT NULL,
    volume      REAL NOT NULL,
    source      TEXT DEFAULT 'alpaca',
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS features (
    symbol          TEXT NOT NULL,
    date            TEXT NOT NULL,
    momentum_7d     REAL,
    momentum_14d    REAL,
    momentum_16d    REAL,
    momentum_21d    REAL,
    momentum_28d    REAL,
    realized_vol_20d REAL,
    realized_vol_45d REAL,
    vol_scalar      REAL,
    vol_ratio       REAL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    action          TEXT NOT NULL,
    reason          TEXT,
    momentum_value  REAL,
    vol_scalar      REAL,
    final_position  REAL,
    position_change REAL,
    market_regime   TEXT,
    confidence      REAL
);

CREATE TABLE IF NOT EXISTS executions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id     INTEGER REFERENCES decisions(id),
    timestamp       TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    qty             REAL NOT NULL,
    fill_price      REAL NOT NULL,
    signal_price    REAL,
    slippage_bps    REAL,
    fee_bps         REAL,
    order_type      TEXT,
    status          TEXT
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    date            TEXT PRIMARY KEY,
    total_value     REAL NOT NULL,
    cash_value      REAL NOT NULL,
    cash_pct        REAL NOT NULL,
    positions_json  TEXT,
    drawdown_pct    REAL,
    btc_drawdown_pct REAL,
    dd_protection   REAL,
    sharpe_30d      REAL,
    calmar          REAL,
    sortino         REAL,
    fee_adj_return  REAL
);

CREATE TABLE IF NOT EXISTS experiments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    hypothesis      TEXT NOT NULL,
    params_json     TEXT NOT NULL,
    backtest_sharpe REAL,
    backtest_calmar REAL,
    backtest_sortino REAL,
    backtest_max_dd REAL,
    backtest_fee_adj_return REAL,
    walkforward_sharpe REAL,
    status          TEXT DEFAULT 'PENDING',
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS attribution (
    date            TEXT PRIMARY KEY,
    total_return    REAL,
    vol_timing_return REAL,
    momentum_filter_return REAL,
    crash_savings   REAL,
    market_return   REAL,
    residual        REAL
);
"""


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_connection()
    conn.executescript(_SCHEMA)
    conn.close()


def migrate_v2():
    """v1→v2 스키마 마이그레이션. 안전하게 컬럼 추가."""
    conn = get_connection()
    migrations = [
        ("features", "momentum_16d", "REAL"),
        ("features", "realized_vol_45d", "REAL"),
        ("features", "vol_scalar", "REAL"),
        ("portfolio_snapshots", "btc_drawdown_pct", "REAL"),
        ("portfolio_snapshots", "dd_protection", "REAL"),
        ("portfolio_snapshots", "fee_adj_return", "REAL"),
    ]
    for table, col, dtype in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {dtype}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    migrate_v2()
    print(f"DB initialized/migrated at {DB_PATH}")
