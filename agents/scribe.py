"""Scribe Agent — 의사결정 기록 + 포트폴리오 스냅샷 + attribution.

구조화 기록은 Python, 분석은 Claude Code CLI로 별도 호출.
"""

import json
from datetime import datetime, timezone

from core.db import get_connection
from core.models import SignalResult, PositionTarget, Execution


def record_decisions(
    signals: list[SignalResult],
    targets: list[PositionTarget],
    executions: list[Execution],
    market_regime: str = "UNKNOWN",
):
    """일일 의사결정을 decisions 테이블에 기록.

    [FIX #9] market_regime을 BTC vol 기반으로 실제 분류해서 저장.
    """
    conn = get_connection()
    target_map = {t.symbol: t for t in targets}

    for sig in signals:
        target = target_map.get(sig.symbol)

        # confidence: vol_scalar가 extreme일수록 높은 확신
        confidence = min(abs(sig.vol_scalar - 1.0) / 0.5, 1.0) if sig.vol_scalar > 0 else 0.0

        conn.execute(
            """INSERT INTO decisions
               (date, symbol, action, reason, momentum_value, vol_scalar,
                final_position, position_change, market_regime, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sig.date,
                sig.symbol,
                "BUY" if sig.final_position > 0 else "CASH",
                target.reason if target else sig.reason,
                sig.momentum_score,
                sig.vol_scalar,
                sig.final_position,
                target.delta if target else 0.0,
                market_regime,
                round(confidence, 3),
            ),
        )

    conn.commit()
    conn.close()


def snapshot_portfolio(
    date: str,
    total_value: float,
    cash_value: float,
    positions: dict[str, float],
    drawdown_pct: float,
    btc_drawdown_pct: float = 0.0,
    sharpe_30d: float | None = None,
    calmar: float | None = None,
    sortino: float | None = None,
    fee_adj_return: float | None = None,
):
    """일일 포트폴리오 스냅샷 저장."""
    conn = get_connection()
    cash_pct = cash_value / total_value if total_value > 0 else 1.0

    dd_protection = None
    if btc_drawdown_pct < 0 and drawdown_pct < 0:
        dd_protection = 1 - (drawdown_pct / btc_drawdown_pct)

    conn.execute(
        """INSERT OR REPLACE INTO portfolio_snapshots
           (date, total_value, cash_value, cash_pct, positions_json,
            drawdown_pct, btc_drawdown_pct, dd_protection,
            sharpe_30d, calmar, sortino, fee_adj_return)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            date, total_value, cash_value, cash_pct,
            json.dumps(positions),
            drawdown_pct, btc_drawdown_pct, dd_protection,
            sharpe_30d, calmar, sortino, fee_adj_return,
        ),
    )
    conn.commit()
    conn.close()


def record_experiment(
    hypothesis: str,
    params: dict,
    results: dict,
    status: str = "PENDING",
    notes: str | None = None,
) -> int:
    """실험 결과를 experiments 테이블에 기록."""
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO experiments
           (created_at, hypothesis, params_json, backtest_sharpe, backtest_calmar,
            backtest_sortino, backtest_max_dd, backtest_fee_adj_return,
            walkforward_sharpe, status, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(timezone.utc).isoformat(),
            hypothesis,
            json.dumps(params),
            results.get("sharpe"),
            results.get("calmar"),
            results.get("sortino"),
            results.get("max_dd"),
            results.get("fee_adj_return"),
            results.get("walkforward_sharpe"),
            status,
            notes,
        ),
    )
    exp_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return exp_id


def get_daily_summary() -> str:
    """오늘 결과를 JSON 문자열로 반환 (Claude Code CLI 호출용)."""
    conn = get_connection()

    # 최근 결정
    decisions = conn.execute(
        """SELECT date, symbol, action, reason, momentum_value, vol_scalar, final_position
           FROM decisions ORDER BY id DESC LIMIT 10"""
    ).fetchall()

    # 최근 스냅샷
    snapshot = conn.execute(
        "SELECT * FROM portfolio_snapshots ORDER BY date DESC LIMIT 1"
    ).fetchone()

    conn.close()

    result = {
        "decisions": [
            {"date": d[0], "symbol": d[1], "action": d[2], "reason": d[3],
             "momentum": d[4], "vol_scalar": d[5], "position": d[6]}
            for d in decisions
        ],
        "snapshot": {
            "date": snapshot[0], "total_value": snapshot[1],
            "cash_pct": snapshot[3], "drawdown": snapshot[5],
        } if snapshot else None,
    }

    return json.dumps(result, indent=2, ensure_ascii=False)


def export_daily_for_llm(path: str = "/tmp/afo_daily_result.json"):
    """일일 결과를 JSON 파일로 내보내기 (Claude Code CLI 호출용)."""
    conn = get_connection()

    # 오늘 결정
    decisions = conn.execute(
        """SELECT date, symbol, action, reason, momentum_value, vol_scalar, final_position
           FROM decisions ORDER BY id DESC LIMIT 10"""
    ).fetchall()

    # 최근 스냅샷
    snapshot = conn.execute(
        """SELECT date, total_value, cash_value, cash_pct, positions_json, drawdown_pct,
                  btc_drawdown_pct, dd_protection, sharpe_30d, calmar, sortino
           FROM portfolio_snapshots ORDER BY date DESC LIMIT 1"""
    ).fetchone()

    # 오늘 체결
    executions = conn.execute(
        """SELECT symbol, side, qty, fill_price, slippage_bps, fee_bps, order_type, status
           FROM executions ORDER BY id DESC LIMIT 10"""
    ).fetchall()

    # 최근 7일 포트폴리오 가치 추이
    history = conn.execute(
        "SELECT date, total_value, drawdown_pct FROM portfolio_snapshots ORDER BY date DESC LIMIT 7"
    ).fetchall()

    conn.close()

    result = {
        "decisions": [
            {"date": d[0], "symbol": d[1], "action": d[2], "reason": d[3],
             "momentum": d[4], "vol_scalar": d[5], "position": d[6]}
            for d in decisions
        ],
        "executions": [
            {"symbol": e[0], "side": e[1], "qty": e[2], "price": e[3],
             "slippage_bps": e[4], "fee_bps": e[5], "type": e[6], "status": e[7]}
            for e in executions
        ],
        "snapshot": {
            "date": snapshot[0], "total_value": snapshot[1],
            "cash_value": snapshot[2], "cash_pct": snapshot[3],
            "positions": json.loads(snapshot[4]) if snapshot[4] else {},
            "drawdown": snapshot[5],
            "btc_drawdown": snapshot[6], "dd_protection": snapshot[7],
            "sharpe_30d": snapshot[8], "calmar": snapshot[9], "sortino": snapshot[10],
        } if snapshot else None,
        "history_7d": [
            {"date": h[0], "value": h[1], "drawdown": h[2]}
            for h in history
        ],
        "strategy": {
            "name": "Vol Targeting + Momentum Crash Filter",
            "formation": 16, "vol_lookback": 45, "vol_target": "8%",
            "threshold": "5%", "exchange": "Binance Testnet",
        },
    }

    with open(path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    return path


def get_weekly_summary() -> str:
    """최근 7일 성과 요약 (주간 리뷰용)."""
    conn = get_connection()

    # 7일간 스냅샷
    snapshots = conn.execute(
        """SELECT date, total_value, drawdown_pct, btc_drawdown_pct, dd_protection,
                  sharpe_30d, calmar, positions_json
           FROM portfolio_snapshots ORDER BY date DESC LIMIT 7"""
    ).fetchall()

    # 7일간 거래
    trades = conn.execute(
        """SELECT date, symbol, action, reason, momentum_value, vol_scalar, final_position
           FROM decisions ORDER BY id DESC LIMIT 70"""
    ).fetchall()

    # 7일간 체결
    executions = conn.execute(
        """SELECT symbol, side, qty, fill_price, fee_bps, status
           FROM executions WHERE status = 'FILLED'
           ORDER BY id DESC LIMIT 50"""
    ).fetchall()

    conn.close()

    if not snapshots:
        return json.dumps({"error": "스냅샷 없음"}, ensure_ascii=False)

    # 주간 수익률
    latest = snapshots[0]
    oldest = snapshots[-1]
    weekly_return = (latest[1] - oldest[1]) / oldest[1] if oldest[1] > 0 else 0.0

    # 거래 집계
    total_trades = sum(1 for e in executions if e[5] == "FILLED")
    total_fees = sum(e[2] * e[3] * e[4] / 10000 for e in executions if e[5] == "FILLED")

    result = {
        "period": f"{oldest[0]} ~ {latest[0]}",
        "weekly_return": f"{weekly_return:.2%}",
        "start_value": oldest[1],
        "end_value": latest[1],
        "max_drawdown": min((s[2] or 0) for s in snapshots),
        "btc_max_drawdown": min((s[3] or 0) for s in snapshots),
        "dd_protection": latest[4],
        "sharpe_30d": latest[5],
        "calmar": latest[6],
        "total_trades": total_trades,
        "total_fees": round(total_fees, 2),
        "daily_snapshots": [
            {"date": s[0], "value": s[1], "drawdown": s[2]}
            for s in reversed(snapshots)
        ],
    }

    return json.dumps(result, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["daily", "weekly"], required=True)
    args = parser.parse_args()

    if args.mode == "daily":
        print(get_daily_summary())
    elif args.mode == "weekly":
        print(get_weekly_summary())
