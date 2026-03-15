"""Stage 2 → Stage 3 승격 기준 체크.

4가지 기준 전부 충족해야 Stage 3 ($500 실전) 진입 가능:
1. 페이퍼 4주 이상 운영
2. MaxDD < BTC MaxDD × 0.5
3. Fee-adjusted return ≥ 0 (실측)
4. 백테스트 vs 페이퍼 괴리 < 5%p
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# .env 로드
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from core.config import DB_PATH, STAGE_2_TO_3
from core.db import get_connection


# 백테스트 기준값 (Stage 1.5 확정 결과)
BACKTEST_REFERENCE = {
    "sharpe": 0.628,
    "max_dd": -0.12,     # -12% (vol targeting + momentum filter)
    "fee_adj_return": 0.10,  # +10%
}

# Stage 2 시작일
STAGE2_START_DATE = "2026-03-15"


def check_paper_duration() -> dict:
    """기준 1: 페이퍼 4주(28일) 이상 운영."""
    conn = get_connection()

    first = conn.execute(
        "SELECT date FROM portfolio_snapshots ORDER BY date ASC LIMIT 1"
    ).fetchone()
    last = conn.execute(
        "SELECT date FROM portfolio_snapshots ORDER BY date DESC LIMIT 1"
    ).fetchone()
    total_days_count = conn.execute(
        "SELECT COUNT(DISTINCT date) FROM portfolio_snapshots"
    ).fetchone()[0]

    conn.close()

    if not first or not last:
        return {
            "criterion": "페이퍼 4주 이상 운영",
            "status": "NOT_MET",
            "detail": "스냅샷 없음 — 아직 시작 안 됨",
            "days_elapsed": 0,
            "days_required": 28,
        }

    start = datetime.strptime(first[0], "%Y-%m-%d")
    end = datetime.strptime(last[0], "%Y-%m-%d")
    elapsed = (end - start).days

    # 캘린더 기준 28일
    required = STAGE_2_TO_3["min_paper_weeks"] * 7

    return {
        "criterion": "페이퍼 4주 이상 운영",
        "status": "MET" if elapsed >= required else "NOT_MET",
        "days_elapsed": elapsed,
        "days_with_data": total_days_count,
        "days_required": required,
        "detail": f"{elapsed}일 경과 / {required}일 필요"
                  + (f" — {required - elapsed}일 남음" if elapsed < required else " ✓"),
    }


def check_maxdd_protection() -> dict:
    """기준 2: MaxDD < BTC MaxDD × 0.5."""
    conn = get_connection()

    # 전체 기간 포트폴리오 MaxDD
    snapshots = conn.execute(
        "SELECT total_value FROM portfolio_snapshots ORDER BY date ASC"
    ).fetchall()

    # BTC MaxDD (같은 기간)
    first_date = conn.execute(
        "SELECT date FROM portfolio_snapshots ORDER BY date ASC LIMIT 1"
    ).fetchone()
    last_date = conn.execute(
        "SELECT date FROM portfolio_snapshots ORDER BY date DESC LIMIT 1"
    ).fetchone()

    if not first_date or not last_date or len(snapshots) < 2:
        conn.close()
        return {
            "criterion": "MaxDD < BTC MaxDD × 0.5",
            "status": "NOT_MET",
            "detail": "데이터 부족",
        }

    btc_prices = conn.execute(
        """SELECT close FROM market_bars
           WHERE symbol = 'BTC/USD' AND date BETWEEN ? AND ?
           ORDER BY date ASC""",
        (first_date[0], last_date[0]),
    ).fetchall()

    conn.close()

    # 포트폴리오 MaxDD 계산
    values = [r[0] for r in snapshots]
    strat_dd = _max_drawdown(values)

    # BTC MaxDD 계산
    if btc_prices:
        btc_vals = [r[0] for r in btc_prices]
        btc_dd = _max_drawdown(btc_vals)
    else:
        btc_dd = 0.0

    threshold = btc_dd * STAGE_2_TO_3["dd_protection_ratio"] if btc_dd < 0 else None

    if btc_dd >= 0:
        return {
            "criterion": "MaxDD < BTC MaxDD × 0.5",
            "status": "PENDING",
            "detail": f"BTC MaxDD={btc_dd:.2%} (아직 하락장 없음). 검증 불가.",
            "strat_dd": strat_dd,
            "btc_dd": btc_dd,
        }

    met = strat_dd > threshold  # DD is negative, so > means less drawdown
    return {
        "criterion": "MaxDD < BTC MaxDD × 0.5",
        "status": "MET" if met else "NOT_MET",
        "strat_dd": f"{strat_dd:.2%}",
        "btc_dd": f"{btc_dd:.2%}",
        "threshold": f"{threshold:.2%}",
        "detail": f"전략 DD {strat_dd:.2%} vs BTC DD {btc_dd:.2%} × 0.5 = {threshold:.2%}"
                  + (" ✓" if met else " ✗"),
    }


def check_fee_adjusted_return() -> dict:
    """기준 3: Fee-adjusted return ≥ 0 (실측)."""
    conn = get_connection()

    first = conn.execute(
        "SELECT total_value, date FROM portfolio_snapshots ORDER BY date ASC LIMIT 1"
    ).fetchone()
    last = conn.execute(
        "SELECT total_value, fee_adj_return, date FROM portfolio_snapshots ORDER BY date DESC LIMIT 1"
    ).fetchone()

    # 실제 수수료 합산
    total_fees = conn.execute(
        """SELECT SUM(ABS(qty * fill_price * fee_bps / 10000))
           FROM executions WHERE status = 'FILLED'"""
    ).fetchone()

    conn.close()

    if not first or not last:
        return {
            "criterion": "Fee-adjusted return ≥ 0",
            "status": "NOT_MET",
            "detail": "스냅샷 없음",
        }

    gross_return = (last[0] - first[0]) / first[0] if first[0] > 0 else 0.0
    fees = total_fees[0] if total_fees and total_fees[0] else 0.0
    fee_pct = fees / first[0] if first[0] > 0 else 0.0
    net_return = gross_return - fee_pct

    met = net_return >= STAGE_2_TO_3["min_fee_adj_return"]
    return {
        "criterion": "Fee-adjusted return ≥ 0",
        "status": "MET" if met else "NOT_MET",
        "gross_return": f"{gross_return:.2%}",
        "total_fees": f"${fees:,.2f}",
        "fee_drag": f"{fee_pct:.2%}",
        "net_return": f"{net_return:.2%}",
        "detail": f"순수익 {net_return:.2%} (총수익 {gross_return:.2%} - 수수료 {fee_pct:.2%})"
                  + (" ✓" if met else " ✗"),
    }


def check_backtest_gap() -> dict:
    """기준 4: 백테스트 vs 페이퍼 괴리 < 5%p."""
    conn = get_connection()

    snapshots = conn.execute(
        "SELECT total_value FROM portfolio_snapshots ORDER BY date ASC"
    ).fetchall()
    snap_count = len(snapshots)

    conn.close()

    if snap_count < 30:
        days_until_active = 30 - snap_count
        return {
            "criterion": "백테스트 vs 페이퍼 괴리 < 5%p",
            "status": "PENDING",
            "detail": f"30일 데이터 필요. 현재 {snap_count}일. {days_until_active}일 후 활성화.",
        }

    values = [r[0] for r in snapshots]
    paper_dd = _max_drawdown(values)
    bt_dd = BACKTEST_REFERENCE["max_dd"]
    gap = abs(paper_dd - bt_dd)

    met = gap < STAGE_2_TO_3["max_dd_vs_btc_gap"]
    return {
        "criterion": "백테스트 vs 페이퍼 괴리 < 5%p",
        "status": "MET" if met else "NOT_MET",
        "paper_maxdd": f"{paper_dd:.2%}",
        "backtest_maxdd": f"{bt_dd:.2%}",
        "gap": f"{gap:.2%}",
        "detail": f"괴리 {gap:.2%} (페이퍼 {paper_dd:.2%} vs 백테스트 {bt_dd:.2%})"
                  + (" ✓" if met else " ✗"),
    }


def _max_drawdown(values: list[float]) -> float:
    """가격 시리즈에서 MaxDD 계산. 음수 반환."""
    if len(values) < 2:
        return 0.0
    peak = values[0]
    max_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (v - peak) / peak if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd
    return max_dd


def run_promotion_check() -> dict:
    """승격 기준 전체 체크."""
    criteria = [
        check_paper_duration(),
        check_maxdd_protection(),
        check_fee_adjusted_return(),
        check_backtest_gap(),
    ]

    met_count = sum(1 for c in criteria if c["status"] == "MET")
    pending_count = sum(1 for c in criteria if c["status"] == "PENDING")
    total = len(criteria)

    if met_count == total:
        overall = "READY"
        summary = "모든 기준 충족 — Stage 3 진입 가능"
    elif pending_count > 0:
        overall = "PENDING"
        summary = f"{met_count}/{total} 충족, {pending_count}개 검증 대기 중"
    else:
        overall = "NOT_READY"
        summary = f"{met_count}/{total} 충족"

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stage": "Stage 2 → Stage 3",
        "overall": overall,
        "summary": summary,
        "met": f"{met_count}/{total}",
        "criteria": criteria,
    }


if __name__ == "__main__":
    results = run_promotion_check()
    print(json.dumps(results, indent=2, ensure_ascii=False))
