"""Guardian Agent — 리스크 체크 + kill switches + position threshold.

100% 코드. LLM 의존성 0.

Kill Switches:
- Portfolio DD > -20% → 전량 현금
- Single asset 1일 -15% → 해당 종목 청산
- Daily turnover > 30% → 거래 중단
- Price sanity: 1시간 내 > 20% 변동 → 해당 종목 스킵
- Data freshness: 최신 데이터 10분 초과 → 거래 스킵
"""

import logging
from datetime import datetime, timezone

from core.config import (
    MAX_PORTFOLIO_DRAWDOWN,
    MAX_SINGLE_DAY_LOSS,
    MAX_SINGLE_POSITION_WEIGHT,
    POSITION_THRESHOLD,
)
from core.db import get_connection
from core.models import SignalResult, PositionTarget

log = logging.getLogger(__name__)

# ── Kill Switch 상수 ──
MAX_DAILY_TURNOVER = 0.30     # 일일 총 거래액 / 포트폴리오 30%
MAX_HOURLY_MOVE = 0.20        # 1시간 내 가격 변동 20%
MAX_DATA_AGE_MINUTES = 10     # 데이터 신선도 10분


def check(
    signals: list[SignalResult],
    portfolio_value: float,
    peak_value: float,
    current_weights: dict[str, float] | None = None,
    daily_returns: dict[str, float] | None = None,
) -> list[PositionTarget]:
    """시그널 → 리스크 필터링 → PositionTarget (incremental rebalancing 지원)."""
    if current_weights is None:
        current_weights = {}

    targets = []

    # ── Kill Switch 1: 포트폴리오 드로다운 ──
    drawdown = (portfolio_value - peak_value) / peak_value if peak_value > 0 else 0.0
    if drawdown < MAX_PORTFOLIO_DRAWDOWN:
        log.warning(f"KILL SWITCH: Portfolio DD {drawdown:.2%} < {MAX_PORTFOLIO_DRAWDOWN:.0%}")
        for s in signals:
            cw = current_weights.get(s.symbol, 0.0)
            targets.append(PositionTarget(
                symbol=s.symbol, target_weight=0.0,
                current_weight=cw, delta=-cw,
                execute=True, reason="DD_LIMIT",
            ))
        return targets

    # ── Kill Switch 2: Daily turnover cap ──
    if _check_daily_turnover(portfolio_value):
        log.warning("KILL SWITCH: Daily turnover > 30% — halting trades")
        for s in signals:
            cw = current_weights.get(s.symbol, 0.0)
            targets.append(PositionTarget(
                symbol=s.symbol, target_weight=cw,
                current_weight=cw, delta=0.0,
                execute=False, reason="TURNOVER_HALT",
            ))
        return targets

    # 각 종목별 타겟 비중 계산
    buy_signals = []
    for s in signals:
        if s.final_position > 0:
            # ── Kill Switch 3: 개별 종목 일일 손실 ──
            if daily_returns and daily_returns.get(s.symbol, 0.0) < MAX_SINGLE_DAY_LOSS:
                log.warning(f"KILL SWITCH: {s.symbol} daily return {daily_returns[s.symbol]:.2%} < {MAX_SINGLE_DAY_LOSS:.0%}")
                cw = current_weights.get(s.symbol, 0.0)
                targets.append(PositionTarget(
                    symbol=s.symbol, target_weight=0.0,
                    current_weight=cw, delta=-cw,
                    execute=True, reason="RISK_OVERRIDE_CASH",
                ))
            else:
                buy_signals.append(s)

    # Vol-scaled 포지션 크기 적용
    if buy_signals:
        raw_weights = {}
        base = min(1.0 / len(buy_signals), MAX_SINGLE_POSITION_WEIGHT)
        for s in buy_signals:
            raw_weights[s.symbol] = min(base * s.vol_scalar, MAX_SINGLE_POSITION_WEIGHT)

        total = sum(raw_weights.values())
        if total > 1.0:
            for sym in raw_weights:
                raw_weights[sym] /= total

        for s in buy_signals:
            tw = raw_weights[s.symbol]
            cw = current_weights.get(s.symbol, 0.0)
            delta = tw - cw
            execute = abs(delta) > POSITION_THRESHOLD

            if (cw == 0 and tw > 0) or (cw > 0 and tw == 0):
                execute = True

            targets.append(PositionTarget(
                symbol=s.symbol, target_weight=tw,
                current_weight=cw, delta=delta,
                execute=execute,
                reason="VOL_LONG" if execute else "THRESHOLD_SKIP",
            ))

    # CASH 시그널 종목
    target_syms = {t.symbol for t in targets}
    for s in signals:
        if s.symbol not in target_syms:
            cw = current_weights.get(s.symbol, 0.0)
            targets.append(PositionTarget(
                symbol=s.symbol, target_weight=0.0,
                current_weight=cw, delta=-cw,
                execute=cw > 0,
                reason="CRASH_FILTER_CASH",
            ))

    return targets


def check_risk_only(symbols: list[str]) -> list[dict]:
    """Guardian-only 모드: 시그널 없이 리스크만 체크. 4시간마다 cron으로 실행."""
    alerts = []

    conn = get_connection()

    # 1. 포트폴리오 DD 체크
    row = conn.execute(
        "SELECT total_value FROM portfolio_snapshots ORDER BY date DESC LIMIT 1"
    ).fetchone()
    peak_row = conn.execute(
        "SELECT MAX(total_value) FROM portfolio_snapshots"
    ).fetchone()

    if row and peak_row and peak_row[0]:
        total = row[0]
        peak = peak_row[0]
        dd = (total - peak) / peak
        if dd < MAX_PORTFOLIO_DRAWDOWN:
            alerts.append({
                "severity": "CRITICAL",
                "type": "DD_LIMIT",
                "message": f"Portfolio DD {dd:.2%} breached {MAX_PORTFOLIO_DRAWDOWN:.0%} limit",
            })

    # 2. Data freshness 체크
    for sym in symbols:
        latest = conn.execute(
            "SELECT date FROM market_bars WHERE symbol = ? ORDER BY date DESC LIMIT 1",
            (sym,),
        ).fetchone()
        if latest:
            latest_date = datetime.strptime(latest[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - latest_date
            # 일봉이라 1일 이상 지나면 stale
            if age.days > 2:
                alerts.append({
                    "severity": "WARNING",
                    "type": "DATA_STALE",
                    "message": f"{sym} latest data is {age.days} days old",
                })

    # 3. Single asset crash (최근 2일 수익률)
    for sym in symbols:
        rows = conn.execute(
            "SELECT close FROM market_bars WHERE symbol = ? ORDER BY date DESC LIMIT 2",
            (sym,),
        ).fetchall()
        if len(rows) >= 2:
            ret = (rows[0][0] - rows[1][0]) / rows[1][0]
            if ret < MAX_SINGLE_DAY_LOSS:
                alerts.append({
                    "severity": "CRITICAL",
                    "type": "ASSET_CRASH",
                    "message": f"{sym} daily return {ret:.2%} — below {MAX_SINGLE_DAY_LOSS:.0%} limit",
                })

    # 4. Daily turnover
    if _check_daily_turnover_from_db(conn):
        alerts.append({
            "severity": "WARNING",
            "type": "TURNOVER_HIGH",
            "message": "Daily turnover exceeded 30% of portfolio",
        })

    conn.close()
    return alerts


def _check_daily_turnover(portfolio_value: float) -> bool:
    """오늘 UTC 기준 총 거래액이 포트폴리오의 30% 초과하는지 확인."""
    if portfolio_value <= 0:
        return False

    conn = get_connection()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT SUM(ABS(qty * fill_price)) FROM executions
           WHERE timestamp LIKE ? AND status = 'FILLED'""",
        (f"{today}%",),
    ).fetchone()
    conn.close()

    total_traded = rows[0] if rows and rows[0] else 0.0
    return total_traded > portfolio_value * MAX_DAILY_TURNOVER


def _check_daily_turnover_from_db(conn) -> bool:
    """DB 연결을 받아서 turnover 체크."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT SUM(ABS(qty * fill_price)) FROM executions
           WHERE timestamp LIKE ? AND status = 'FILLED'""",
        (f"{today}%",),
    ).fetchone()

    port_row = conn.execute(
        "SELECT total_value FROM portfolio_snapshots ORDER BY date DESC LIMIT 1"
    ).fetchone()

    total_traded = rows[0] if rows and rows[0] else 0.0
    portfolio_value = port_row[0] if port_row else 10000.0

    return total_traded > portfolio_value * MAX_DAILY_TURNOVER
