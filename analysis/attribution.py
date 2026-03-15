"""Attribution 분석 — Vol Timing vs Momentum Filter 기여 분해.

실험 7에서 발견된 구조를 일일 단위로 추적:
- vol_timing_return: vol scalar 적용에 의한 수익/손실
- momentum_filter_return: crash filter로 현금이었던 날의 기회비용
- crash_savings: crash filter가 막아준 하락
"""

from core.db import get_connection


def record_attribution(
    date: str,
    total_return: float,
    vol_timing_return: float | None = None,
    momentum_filter_return: float | None = None,
    crash_savings: float | None = None,
    market_return: float | None = None,
):
    """일일 수익 원천 분해를 DB에 기록."""
    conn = get_connection()
    residual = None
    if all(v is not None for v in [total_return, vol_timing_return, momentum_filter_return]):
        residual = total_return - (vol_timing_return or 0) - (momentum_filter_return or 0)

    conn.execute(
        """INSERT OR REPLACE INTO attribution
           (date, total_return, vol_timing_return, momentum_filter_return,
            crash_savings, market_return, residual)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (date, total_return, vol_timing_return, momentum_filter_return,
         crash_savings, market_return, residual),
    )
    conn.commit()
    conn.close()


def get_attribution_summary(days: int = 30) -> dict:
    """최근 N일 attribution 요약."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT total_return, vol_timing_return, momentum_filter_return,
                  crash_savings, market_return, residual
           FROM attribution ORDER BY date DESC LIMIT ?""",
        (days,),
    ).fetchall()
    conn.close()

    if not rows:
        return {}

    return {
        "total_return_sum": sum(r[0] or 0 for r in rows),
        "vol_timing_sum": sum(r[1] or 0 for r in rows),
        "momentum_filter_sum": sum(r[2] or 0 for r in rows),
        "crash_savings_sum": sum(r[3] or 0 for r in rows),
        "market_return_sum": sum(r[4] or 0 for r in rows),
        "days": len(rows),
    }
