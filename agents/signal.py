"""Signal Agent — Vol targeting + Momentum crash filter.

수익 엔진: vol targeting (position_size = target_vol / realized_vol)
방어 엔진: momentum crash filter (momentum ≤ 0이면 현금)
"""

import numpy as np
import pandas as pd
from core.db import get_connection
from core.config import FORMATION_DAYS, CONFIRMATION_DAYS, VOL_LOOKBACK, VOL_TARGET, MAX_VOL_SCALE
from core.models import SignalResult


def compute(symbols: list[str]) -> list[SignalResult]:
    """최신 데이터 기준으로 vol targeting + momentum filter 계산."""
    conn = get_connection()
    results = []
    lookback_needed = max(FORMATION_DAYS + CONFIRMATION_DAYS, VOL_LOOKBACK) + 5

    for symbol in symbols:
        df = pd.read_sql(
            """SELECT date, close FROM market_bars
               WHERE symbol = ? ORDER BY date DESC LIMIT ?""",
            conn,
            params=(symbol, lookback_needed),
        )

        if len(df) < FORMATION_DAYS + 1:
            results.append(SignalResult(
                symbol=symbol, date=df.iloc[0]["date"] if len(df) > 0 else "",
                momentum_score=0.0, is_trending=False, realized_vol=0.0,
                vol_scalar=0.0, final_position=0.0, reason="INSUFFICIENT_DATA",
            ))
            continue

        closes = df["close"].values  # [newest, ..., oldest]
        latest_date = df.iloc[0]["date"]

        # 1. Momentum crash filter
        momentum = (closes[0] / closes[FORMATION_DAYS]) - 1

        # 2. Confirmation: N일 연속 momentum > 0 확인
        is_trending = True
        if CONFIRMATION_DAYS > 0:
            for d in range(CONFIRMATION_DAYS):
                if d + FORMATION_DAYS + 1 >= len(closes):
                    break
                m = (closes[d] / closes[d + FORMATION_DAYS]) - 1
                if m <= 0:
                    is_trending = False
                    break
        else:
            is_trending = momentum > 0

        # 3. Vol targeting
        if len(closes) >= VOL_LOOKBACK + 1:
            # closes is newest-first, reverse for pct_change
            price_series = pd.Series(closes[:VOL_LOOKBACK + 1][::-1])
            daily_returns = price_series.pct_change().dropna()
            realized_vol = float(daily_returns.std() * np.sqrt(365))
        else:
            realized_vol = VOL_TARGET  # fallback

        if realized_vol > 0:
            vol_scalar = VOL_TARGET / realized_vol
            vol_scalar = min(vol_scalar, MAX_VOL_SCALE)
            vol_scalar = max(vol_scalar, 0.1)
        else:
            vol_scalar = 1.0

        # 4. Combined
        if is_trending:
            final_position = vol_scalar
            reason = "VOL_LONG"
        else:
            final_position = 0.0
            reason = "CRASH_FILTER_CASH"

        results.append(SignalResult(
            symbol=symbol, date=latest_date,
            momentum_score=momentum, is_trending=is_trending,
            realized_vol=realized_vol, vol_scalar=vol_scalar,
            final_position=final_position, reason=reason,
        ))

    conn.close()
    return results


# ── 백테스트용 (기존 호환) ──

def compute_backtest(
    closes: pd.DataFrame,
    symbols: list[str],
    formation_days: int,
) -> dict[str, pd.DataFrame]:
    """백테스트용: 전체 기간의 시그널을 한번에 계산."""
    result = {}
    for symbol in symbols:
        if symbol not in closes.columns:
            continue
        series = closes[symbol].dropna()
        momentum = series / series.shift(formation_days) - 1
        signals = momentum.apply(lambda x: "BUY" if x > 0 else "CASH")

        df = pd.DataFrame({
            "date": series.index,
            "close": series.values,
            "momentum": momentum.values,
            "signal": signals.values,
        }).dropna(subset=["momentum"])

        result[symbol] = df

    return result
