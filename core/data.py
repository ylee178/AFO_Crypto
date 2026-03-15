"""Binance 크립토 데이터 수집 → market_bars 테이블 저장."""

import argparse
from datetime import datetime, timedelta, timezone

import pandas as pd
from binance.client import Client

from core.config import SYMBOLS, DEFAULT_HISTORY_YEARS, to_binance_symbol
from core.db import get_connection, init_db


def _get_client() -> Client:
    """Binance client (공개 데이터는 키 불필요)."""
    return Client("", "")


def fetch_bars(symbols: list[str], start: datetime, end: datetime) -> pd.DataFrame:
    """Binance에서 일봉 데이터 수집."""
    client = _get_client()
    all_rows = []

    for symbol in symbols:
        bn_symbol = to_binance_symbol(symbol)
        start_str = start.strftime("%d %b %Y")
        end_str = end.strftime("%d %b %Y")

        klines = client.get_historical_klines(
            bn_symbol, Client.KLINE_INTERVAL_1DAY,
            start_str, end_str,
        )

        for k in klines:
            all_rows.append({
                "symbol": symbol,
                "timestamp": pd.Timestamp(k[0], unit="ms", tz="UTC"),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })

    return pd.DataFrame(all_rows)


def save_bars(df: pd.DataFrame):
    conn = get_connection()
    for _, row in df.iterrows():
        date_str = str(row["timestamp"].date()) if hasattr(row["timestamp"], "date") else str(row["timestamp"])[:10]
        conn.execute(
            """INSERT OR REPLACE INTO market_bars
               (symbol, date, open, high, low, close, volume, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'binance')""",
            (row["symbol"], date_str, row["open"], row["high"],
             row["low"], row["close"], row["volume"]),
        )
    conn.commit()
    conn.close()


def update_bars(symbols: list[str], years: int = DEFAULT_HISTORY_YEARS):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=years * 365)
    print(f"Fetching {symbols} from {start.date()} to {end.date()} (Binance)...")
    df = fetch_bars(symbols, start, end)
    print(f"Got {len(df)} bars")
    save_bars(df)
    print("Saved to DB")


def load_bars(symbols: list[str] | None = None) -> pd.DataFrame:
    conn = get_connection()
    if symbols:
        placeholders = ",".join("?" for _ in symbols)
        df = pd.read_sql(
            f"SELECT * FROM market_bars WHERE symbol IN ({placeholders}) ORDER BY date",
            conn, params=symbols,
        )
    else:
        df = pd.read_sql("SELECT * FROM market_bars ORDER BY date", conn)
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--years", type=int, default=DEFAULT_HISTORY_YEARS)
    args = parser.parse_args()

    init_db()
    update_bars(args.symbols, args.years)
