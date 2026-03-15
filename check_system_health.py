"""시스템 건강 상태 체크 — AI 인사이트용 JSON 출력.

매일 run_daily.sh에서 호출. claude -p에 트레이딩 데이터와 함께 전달.

체크 항목:
- Drawdown 계산 정상 작동 여부
- Kill switch 각각 활성화 여부
- DB 데이터 정상 적재 여부
- Guardian 마지막 실행 시간
- 로그 파일 크기
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

from core.config import (
    SYMBOLS, DB_PATH, MAX_PORTFOLIO_DRAWDOWN, MAX_SINGLE_DAY_LOSS,
    POSITION_THRESHOLD, STAGE_2_TO_3,
)
from core.db import get_connection


def check_drawdown_calculation() -> dict:
    """Drawdown 계산이 정상 작동하는지 검증."""
    conn = get_connection()

    row = conn.execute(
        "SELECT total_value, drawdown_pct FROM portfolio_snapshots ORDER BY date DESC LIMIT 1"
    ).fetchone()

    peak_row = conn.execute(
        "SELECT MAX(total_value) FROM portfolio_snapshots"
    ).fetchone()

    conn.close()

    if not row:
        return {"status": "FAIL", "detail": "포트폴리오 스냅샷 없음 — 아직 한 번도 실행 안 됨"}

    total_value = row[0]
    stored_dd = row[1]
    peak = peak_row[0] if peak_row and peak_row[0] else total_value

    # 실제 DD를 직접 계산해서 저장값과 비교
    computed_dd = (total_value - peak) / peak if peak > 0 else 0.0

    if stored_dd is None:
        return {"status": "WARN", "detail": f"drawdown_pct가 NULL. 계산값: {computed_dd:.4f}"}

    if stored_dd == 0.0 and total_value < peak * 0.999:
        return {
            "status": "FAIL",
            "detail": f"drawdown_pct=0.0인데 실제 값({total_value:.0f})이 고점({peak:.0f})보다 낮음. "
                      f"계산값: {computed_dd:.4f}. kill switch가 사실상 비활성.",
        }

    gap = abs(stored_dd - computed_dd)
    if gap > 0.001:
        return {
            "status": "WARN",
            "detail": f"저장된 DD({stored_dd:.4f})와 계산된 DD({computed_dd:.4f}) 차이: {gap:.4f}",
        }

    return {"status": "OK", "detail": f"drawdown={stored_dd:.4f}, peak=${peak:,.0f}, current=${total_value:,.0f}"}


def check_kill_switches() -> dict:
    """각 kill switch가 정상 활성화 상태인지 확인."""
    checks = []

    # 1. Portfolio DD kill switch
    conn = get_connection()
    row = conn.execute(
        "SELECT total_value FROM portfolio_snapshots ORDER BY date DESC LIMIT 1"
    ).fetchone()
    peak_row = conn.execute(
        "SELECT MAX(total_value) FROM portfolio_snapshots"
    ).fetchone()

    if row and peak_row and peak_row[0]:
        dd = (row[0] - peak_row[0]) / peak_row[0]
        checks.append({
            "name": "Portfolio DD (-20%)",
            "active": True,
            "current": f"{dd:.2%}",
            "threshold": f"{MAX_PORTFOLIO_DRAWDOWN:.0%}",
            "triggered": dd < MAX_PORTFOLIO_DRAWDOWN,
        })
    else:
        checks.append({"name": "Portfolio DD (-20%)", "active": False, "detail": "스냅샷 없음"})

    # 2. Single asset crash kill switch
    for sym in SYMBOLS:
        rows = conn.execute(
            "SELECT close FROM market_bars WHERE symbol = ? ORDER BY date DESC LIMIT 2",
            (sym,),
        ).fetchall()
        if len(rows) >= 2:
            ret = (rows[0][0] - rows[1][0]) / rows[1][0]
            checks.append({
                "name": f"{sym} Daily Loss (-15%)",
                "active": True,
                "current": f"{ret:.2%}",
                "threshold": f"{MAX_SINGLE_DAY_LOSS:.0%}",
                "triggered": ret < MAX_SINGLE_DAY_LOSS,
            })
        else:
            checks.append({"name": f"{sym} Daily Loss (-15%)", "active": False, "detail": "데이터 부족"})

    # 3. Turnover kill switch
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    turnover_row = conn.execute(
        """SELECT SUM(ABS(qty * fill_price)) FROM executions
           WHERE timestamp LIKE ? AND status = 'FILLED'""",
        (f"{today}%",),
    ).fetchone()
    total_traded = turnover_row[0] if turnover_row and turnover_row[0] else 0.0
    portfolio_value = row[0] if row else 10000.0
    checks.append({
        "name": "Daily Turnover (30%)",
        "active": True,
        "current": f"{total_traded / portfolio_value:.1%}" if portfolio_value > 0 else "0%",
        "threshold": "30%",
        "triggered": total_traded > portfolio_value * 0.30,
    })

    conn.close()
    return {"status": "OK", "checks": checks}


def check_data_integrity() -> dict:
    """DB 데이터 적재 상태 확인."""
    conn = get_connection()
    issues = []

    for sym in SYMBOLS:
        # 최신 데이터 날짜
        row = conn.execute(
            "SELECT date FROM market_bars WHERE symbol = ? ORDER BY date DESC LIMIT 1",
            (sym,),
        ).fetchone()

        if not row:
            issues.append(f"{sym}: market_bars 데이터 없음")
            continue

        latest = datetime.strptime(row[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - latest

        if age.days > 2:
            issues.append(f"{sym}: {age.days}일 지연 (최신: {row[0]})")
        elif age.days > 1:
            issues.append(f"{sym}: {age.days}일 전 데이터 (주말일 수 있음)")

        # 총 데이터 건수
        count = conn.execute(
            "SELECT COUNT(*) FROM market_bars WHERE symbol = ?", (sym,)
        ).fetchone()[0]
        if count < 30:
            issues.append(f"{sym}: 데이터 {count}건뿐 (최소 30일 필요)")

    # 스냅샷 적재 확인
    snap_count = conn.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0]
    latest_snap = conn.execute(
        "SELECT date FROM portfolio_snapshots ORDER BY date DESC LIMIT 1"
    ).fetchone()

    # decisions 적재 확인
    dec_count = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]

    conn.close()

    return {
        "status": "FAIL" if issues else "OK",
        "issues": issues,
        "stats": {
            "snapshot_count": snap_count,
            "latest_snapshot": latest_snap[0] if latest_snap else None,
            "decision_count": dec_count,
        },
    }


def check_guardian_last_run() -> dict:
    """Guardian 마지막 실행이 4시간 이내인지 확인."""
    log_path = Path(__file__).parent / "logs" / "guardian.log"

    if not log_path.exists():
        return {"status": "WARN", "detail": "guardian.log 없음 — 한 번도 실행 안 됨"}

    # 로그 파일 마지막 수정 시간
    mtime = datetime.fromtimestamp(log_path.stat().st_mtime, tz=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - mtime).total_seconds() / 3600

    if age_hours > 6:
        return {
            "status": "FAIL",
            "detail": f"Guardian 마지막 실행: {age_hours:.1f}시간 전. cron 확인 필요.",
            "last_run": mtime.isoformat(),
        }
    elif age_hours > 4:
        return {
            "status": "WARN",
            "detail": f"Guardian 마지막 실행: {age_hours:.1f}시간 전.",
            "last_run": mtime.isoformat(),
        }

    return {
        "status": "OK",
        "detail": f"Guardian 마지막 실행: {age_hours:.1f}시간 전",
        "last_run": mtime.isoformat(),
    }


def check_log_sizes() -> dict:
    """로그 파일 크기 체크."""
    logs_dir = Path(__file__).parent / "logs"
    results = {}

    for log_file in ["daily.log", "guardian.log"]:
        path = logs_dir / log_file
        if path.exists():
            size_mb = path.stat().st_size / (1024 * 1024)
            results[log_file] = {
                "size_mb": round(size_mb, 1),
                "warning": size_mb > 50,
            }
        else:
            results[log_file] = {"size_mb": 0, "warning": False}

    return results


def run_all_checks() -> dict:
    """모든 체크 실행, JSON 출력."""
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "drawdown_calculation": check_drawdown_calculation(),
            "kill_switches": check_kill_switches(),
            "data_integrity": check_data_integrity(),
            "guardian_last_run": check_guardian_last_run(),
            "log_sizes": check_log_sizes(),
        },
    }

    # 전체 상태 요약
    statuses = []
    for name, check in results["checks"].items():
        if isinstance(check, dict) and "status" in check:
            statuses.append(check["status"])

    if "FAIL" in statuses:
        results["overall"] = "FAIL"
    elif "WARN" in statuses:
        results["overall"] = "WARN"
    else:
        results["overall"] = "OK"

    return results


if __name__ == "__main__":
    results = run_all_checks()
    print(json.dumps(results, indent=2, ensure_ascii=False))
