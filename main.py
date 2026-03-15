"""Crypto AFO — Stage 2 일일 실행 진입점.

전략: Volatility Targeting + Momentum Crash Filter + Incremental Rebalancing
파라미터: F=16d, VLB=45d, VT=8%, Threshold=5%

사용법:
  python main.py --mode paper              # 일일 전략 실행 (Binance Testnet)
  python main.py --mode paper --dry-run    # 시그널만 확인, 주문 안 넣음
  python main.py --mode simulate           # 시뮬레이션 (API 호출 없음)
  python main.py --mode guardian           # 리스크 체크만 (4시간마다 cron)
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# .env 로드
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())

from core.config import (
    SYMBOLS, DB_PATH, POSITION_THRESHOLD, FORMATION_DAYS,
    VOL_LOOKBACK, VOL_TARGET, MAX_SINGLE_DAY_LOSS,
)
from core.db import init_db, migrate_v2, get_connection
from core.data import update_bars
from agents import signal, guardian, operator, scribe
from analysis.metrics import (
    sharpe_ratio, calmar_ratio, sortino_ratio, max_drawdown, dd_protection_ratio,
)

# ── Slack 알림 (severity 구분) ──

def send_slack(message: str, severity: str = "INFO"):
    """Slack 웹훅으로 알림 전송. severity: INFO / WARNING / CRITICAL"""
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")

    prefix = {"INFO": "", "WARNING": ":warning: ", "CRITICAL": ":rotating_light: "}
    formatted = f"{prefix.get(severity, '')}{message}"

    log.info(f"[Slack {severity}] {message}")

    if not webhook_url:
        return

    import requests
    try:
        payload = {
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": formatted},
                }
            ]
        }
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code != 200:
            log.warning(f"Slack send failed: {resp.status_code} {resp.text}")
    except Exception as e:
        log.warning(f"Slack send failed: {e}")


# ── 포트폴리오 상태 ──

def get_portfolio_state() -> tuple[float, float, dict[str, float]]:
    """포트폴리오 상태를 DB에서 조회.

    [FIX #1] positions_json은 비중(weight)을 저장하므로 그대로 비중으로 반환.
    이전 버그: 비중을 수량으로 해석해서 get_current_weights()에서 뻥튀기됨.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT total_value, positions_json FROM portfolio_snapshots ORDER BY date DESC LIMIT 1"
    ).fetchone()

    if row is None:
        conn.close()
        return 10000.0, 10000.0, {}

    total = row[0]
    # positions_json은 {symbol: weight} 형태로 저장됨
    positions_weights = json.loads(row[1]) if row[1] else {}

    peak_row = conn.execute(
        "SELECT MAX(total_value) FROM portfolio_snapshots"
    ).fetchone()
    peak = peak_row[0] if peak_row and peak_row[0] else total

    conn.close()
    # [FIX #1] 비중을 그대로 반환 (수량이 아님)
    return total, peak, positions_weights


def get_current_prices(symbols: list[str]) -> dict[str, float]:
    conn = get_connection()
    prices = {}
    for sym in symbols:
        row = conn.execute(
            "SELECT close FROM market_bars WHERE symbol = ? ORDER BY date DESC LIMIT 1",
            (sym,),
        ).fetchone()
        if row:
            prices[sym] = row[0]
    conn.close()
    return prices


def get_daily_returns(symbols: list[str]) -> dict[str, float]:
    conn = get_connection()
    returns = {}
    for sym in symbols:
        rows = conn.execute(
            "SELECT close FROM market_bars WHERE symbol = ? ORDER BY date DESC LIMIT 2",
            (sym,),
        ).fetchall()
        if len(rows) >= 2:
            returns[sym] = (rows[0][0] - rows[1][0]) / rows[1][0]
    conn.close()
    return returns


def check_data_freshness(symbols: list[str]) -> list[str]:
    """[FIX #7] 데이터 신선도 체크. daily run에서도 호출."""
    stale = []
    conn = get_connection()
    for sym in symbols:
        row = conn.execute(
            "SELECT date FROM market_bars WHERE symbol = ? ORDER BY date DESC LIMIT 1",
            (sym,),
        ).fetchone()
        if row:
            latest = datetime.strptime(row[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - latest
            if age.days > 2:
                stale.append(f"{sym}: {age.days}일 지연")
    conn.close()
    return stale


def compute_live_kpis() -> dict:
    """[FIX #5] 최근 30일 포트폴리오 KPI 계산."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT date, total_value FROM portfolio_snapshots ORDER BY date DESC LIMIT 31"
    ).fetchall()

    btc_rows = conn.execute(
        "SELECT date, close FROM market_bars WHERE symbol = 'BTC/USD' ORDER BY date DESC LIMIT 31"
    ).fetchall()
    conn.close()

    if len(rows) < 2:
        return {}

    values = pd.Series([r[1] for r in reversed(rows)])
    returns = values.pct_change().dropna()

    btc_closes = pd.Series([r[1] for r in reversed(btc_rows)])
    btc_returns = btc_closes.pct_change().dropna()

    strat_dd = max_drawdown(returns) if len(returns) > 0 else 0.0
    btc_dd = max_drawdown(btc_returns) if len(btc_returns) > 0 else 0.0

    return {
        "sharpe_30d": sharpe_ratio(returns) if len(returns) >= 5 else None,
        "calmar": calmar_ratio(returns) if len(returns) >= 5 else None,
        "sortino": sortino_ratio(returns) if len(returns) >= 5 else None,
        "btc_drawdown_pct": btc_dd,
        "dd_protection": dd_protection_ratio(strat_dd, btc_dd) if btc_dd < 0 else None,
    }


# ── Guardian-only 모드 ──

def run_guardian():
    """리스크 체크만 실행 (4시간마다 cron)."""
    log.info("=== Guardian Risk Check ===")

    alerts = guardian.check_risk_only(SYMBOLS)

    if not alerts:
        log.info("All clear — no risk alerts")
        return

    for alert in alerts:
        sev = alert["severity"]
        atype = alert["type"]

        if sev == "CRITICAL" and atype == "DD_LIMIT":
            msg = (
                f":rotating_light: *긴급: Kill Switch 발동*\n\n"
                f"원인: 포트폴리오 낙폭이 -20% 한도 초과\n"
                f"조치: 전량 현금 전환 실행됨\n"
                f"내가 할 일: 로그 확인 후 전략 재검토 (`logs/guardian.log`)"
            )
        elif sev == "CRITICAL" and atype == "ASSET_CRASH":
            msg = (
                f":rotating_light: *긴급: 개별 종목 급락*\n\n"
                f"{alert['message']}\n"
                f"조치: 해당 종목 자동 청산됨\n"
                f"내가 할 일: 뉴스 확인 — 시장 전체 문제인지 개별 이슈인지 판단"
            )
        elif sev == "WARNING" and atype == "TURNOVER_HIGH":
            msg = (
                f":warning: *경고: 일일 거래량 초과*\n\n"
                f"원인: 일일 거래량이 포트폴리오의 30% 초과\n"
                f"조치: 오늘 추가 거래 자동 중단됨\n"
                f"내가 할 일: 없음 — 내일 UTC 00:00에 자동 리셋"
            )
        elif sev == "WARNING" and atype == "DATA_STALE":
            msg = (
                f":warning: *경고: 데이터 지연*\n\n"
                f"{alert['message']}\n"
                f"영향: 시그널 계산 정확도 저하 가능\n"
                f"내가 할 일: Binance API 상태 확인"
            )
        else:
            msg = f"[{sev}] {atype}: {alert['message']}"

        log.warning(msg) if sev == "WARNING" else log.error(msg)
        send_slack(msg, severity=sev)

    log.info("=== Guardian check complete ===")


# ── Daily 전략 실행 ──

def run_daily(mode: str = "paper", dry_run: bool = False):
    """매일 자동 실행되는 메인 루프."""
    symbols = SYMBOLS
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info(f"=== Crypto AFO Daily Run [{today}] mode={mode} ===")

    # [FIX #2] paper 모드에서 API 키 없으면 명시적 에러
    if mode == "paper":
        api_key = os.environ.get("BINANCE_API_KEY", "")
        if not api_key:
            log.error("BINANCE_API_KEY not set — cannot run paper mode")
            send_slack(
                ":x: *시스템 오류*\n\n"
                "문제: Binance API 키가 설정되지 않음\n"
                "영향: paper 모드 실행 불가\n"
                "내가 할 일: `.env` 파일에 BINANCE_API_KEY 설정",
                "CRITICAL",
            )
            return

    # 1. 데이터 수집
    log.info("Step 1: Updating market data...")
    try:
        update_bars(symbols, years=1)
    except Exception as e:
        log.error(f"Data fetch FAILED: {e}")
        send_slack(
            f":x: *시스템 오류*\n\n"
            f"문제: Binance 데이터 수집 실패\n"
            f"영향: 오늘 시그널 계산 안 됨 → 어제 포지션 유지\n"
            f"내가 할 일: 로그 확인 (`logs/daily.log`)\n"
            f"에러: `{e}`",
            "CRITICAL",
        )
        return

    # [FIX #7] 데이터 신선도 체크 (daily run에서도)
    stale = check_data_freshness(symbols)
    if stale:
        log.warning(f"Stale data detected: {stale}")
        send_slack(
            f":warning: *경고: 데이터 지연*\n\n"
            + "\n".join(stale) + "\n"
            f"영향: 시그널 정확도 저하 가능\n"
            f"계속 실행하지만 주의 필요",
            "WARNING",
        )

    # 2. Signal Agent
    log.info("Step 2: Computing signals...")
    try:
        signals = signal.compute(symbols)
    except Exception as e:
        log.error(f"Signal compute FAILED: {e}")
        send_slack(
            f":x: *시스템 오류*\n\n"
            f"문제: 시그널 계산 실패\n"
            f"영향: 어제 포지션 유지\n"
            f"내가 할 일: 로그 확인 (`logs/daily.log`)\n"
            f"에러: `{e}`",
            "CRITICAL",
        )
        return

    if not signals:
        log.error("No signals generated")
        send_slack(
            f":x: *시스템 오류*\n\n"
            f"문제: 시그널이 생성되지 않음\n"
            f"영향: 데이터 부족 가능성 → 어제 포지션 유지\n"
            f"내가 할 일: DB 데이터 확인",
            "CRITICAL",
        )
        return

    for s in signals:
        log.info(f"  {s.symbol}: mom={s.momentum_score:+.4f} trending={s.is_trending} "
                 f"vol={s.realized_vol:.3f} scalar={s.vol_scalar:.3f} → {s.reason} pos={s.final_position:.3f}")

    # 3. Guardian Agent
    log.info("Step 3: Risk check...")
    total_value, peak_value, current_weights = get_portfolio_state()
    # [FIX #1] current_weights는 이미 비중이므로 변환 불필요
    prices = get_current_prices(symbols)
    daily_returns = get_daily_returns(symbols)

    targets = guardian.check(signals, total_value, peak_value, current_weights, daily_returns)
    for t in targets:
        log.info(f"  {t.symbol}: target={t.target_weight:.3f} current={t.current_weight:.3f} "
                 f"delta={t.delta:+.3f} execute={t.execute} ({t.reason})")

    # Kill switch 알림
    kill_reasons = {"DD_LIMIT", "TURNOVER_HALT", "RISK_OVERRIDE_CASH"}
    triggered = [t for t in targets if t.reason in kill_reasons]
    if triggered:
        for t in triggered:
            kill_msgs = {
                "DD_LIMIT": f":rotating_light: *긴급: Kill Switch 발동*\n\n원인: 포트폴리오 낙폭 -20% 초과\n조치: 전량 현금 전환\n내가 할 일: 전략 재검토",
                "TURNOVER_HALT": f":warning: *경고: 거래량 초과*\n\n원인: 일일 거래량 30% 초과\n조치: 오늘 추가 거래 중단\n내가 할 일: 없음 — 내일 자동 리셋",
                "RISK_OVERRIDE_CASH": f":rotating_light: *긴급: {t.symbol} 급락 감지*\n\n원인: 1일 수익률 -{abs(MAX_SINGLE_DAY_LOSS):.0%} 초과\n조치: {t.symbol} 자동 청산\n내가 할 일: 뉴스 확인",
            }
            send_slack(kill_msgs.get(t.reason, f"Kill Switch: {t.reason}"), "CRITICAL")

    if dry_run:
        log.info("DRY RUN — skipping execution")
        return

    # 4. Operator Agent
    # [FIX #6] 매도를 먼저 실행하여 현금 확보
    log.info("Step 4: Executing orders...")
    exec_mode = "simulate" if mode == "simulate" else mode
    sells = [t for t in targets if t.execute and t.delta < 0]
    buys = [t for t in targets if t.execute and t.delta > 0]
    skips = [t for t in targets if not t.execute]

    try:
        executions = []
        # 매도 먼저
        if sells:
            executions += operator.execute(sells, total_value, prices, mode=exec_mode)
        # 매수 후
        if buys:
            executions += operator.execute(buys, total_value, prices, mode=exec_mode)
        # skip 기록
        for t in skips:
            from core.models import Execution
            executions.append(Execution(
                decision_id=None,
                timestamp=datetime.now(timezone.utc).isoformat(),
                symbol=t.symbol, side="HOLD", qty=0.0,
                fill_price=prices.get(t.symbol, 0.0),
                signal_price=prices.get(t.symbol, 0.0),
                slippage_bps=0.0, fee_bps=0.0,
                order_type="SIMULATED" if mode == "simulate" else "LIMIT",
                status="SKIPPED",
            ))
    except Exception as e:
        log.error(f"Execution FAILED: {e}")
        send_slack(
            f":x: *시스템 오류*\n\n"
            f"문제: 주문 실행 실패\n"
            f"영향: 시그널은 계산됐으나 주문이 안 나감 → 어제 포지션 유지\n"
            f"내가 할 일: Binance API 상태 확인, 로그 확인\n"
            f"에러: `{e}`",
            "CRITICAL",
        )
        return

    for ex in executions:
        if ex.status not in ("SKIPPED",):
            log.info(f"  {ex.symbol}: {ex.side} qty={ex.qty:.6f} @ {ex.fill_price:.2f} "
                     f"slip={ex.slippage_bps:.1f}bps status={ex.status}")

    # 실패한 주문 알림
    failed = [ex for ex in executions if ex.status == "FAILED"]
    if failed:
        for ex in failed:
            send_slack(
                f":warning: *주문 실패*\n\n"
                f"종목: {ex.symbol} {ex.side}\n"
                f"영향: 해당 종목 포지션 미변경\n"
                f"내가 할 일: 다음 실행에서 자동 재시도됨",
                "WARNING",
            )

    # 5. Scribe Agent
    log.info("Step 5: Recording...")
    scribe.record_decisions(signals, targets, executions)
    operator.save_executions(executions)

    # [FIX #5] 포트폴리오 스냅샷에 KPI 포함
    drawdown = (total_value - peak_value) / peak_value if peak_value > 0 else 0.0
    pos_weights = {t.symbol: t.target_weight for t in targets if t.target_weight > 0}
    cash_value = total_value * (1 - sum(pos_weights.values()))

    live_kpis = compute_live_kpis()

    scribe.snapshot_portfolio(
        date=today,
        total_value=total_value,
        cash_value=cash_value,
        positions=pos_weights,
        drawdown_pct=drawdown,
        btc_drawdown_pct=live_kpis.get("btc_drawdown_pct", 0.0),
        sharpe_30d=live_kpis.get("sharpe_30d"),
        calmar=live_kpis.get("calmar"),
        sortino=live_kpis.get("sortino"),
        fee_adj_return=None,  # 실측 수수료 기반 계산은 추후 추가
    )

    # 6. Slack 일일 리포트
    executed_trades = [ex for ex in executions if ex.status not in ("SKIPPED", "FAILED")]

    report = f":chart_with_upwards_trend: *크립토 AFO 일일 리포트* [{today}]\n\n"
    report += f":moneybag: *포트폴리오*: ${total_value:,.0f} (고점 대비 {drawdown:.1%})\n"

    for sym in symbols:
        w = pos_weights.get(sym, 0.0)
        val = total_value * w
        if val > 0:
            report += f"   {sym.split('/')[0]} ${val:,.0f} (비중 {w:.1%})\n"
    cash_pct = 1 - sum(pos_weights.values())
    if cash_pct > 0.01:
        report += f"   현금 ${total_value * cash_pct:,.0f} (비중 {cash_pct:.1%})\n"

    report += f"\n:chart_with_upwards_trend: *시그널*\n"
    for s in signals:
        sym_short = s.symbol.split("/")[0]
        mom_pct = s.momentum_score * 100
        vol_pct = s.realized_vol * 100

        if s.is_trending:
            mom_desc = f"모멘텀 {mom_pct:+.1f}% ({FORMATION_DAYS}일) → *롱 유지*"
        else:
            mom_desc = f"모멘텀 {mom_pct:+.1f}% ({FORMATION_DAYS}일) → *현금 전환*"

        if s.vol_scalar < 0.5:
            vol_desc = f"변동성 {vol_pct:.0f}% → 포지션 *대폭 축소* ({s.vol_scalar:.2f}x)"
        elif s.vol_scalar < 1.0:
            vol_desc = f"변동성 {vol_pct:.0f}% → 포지션 *축소* ({s.vol_scalar:.2f}x)"
        else:
            vol_desc = f"변동성 {vol_pct:.0f}% → 포지션 *확대* ({s.vol_scalar:.2f}x)"

        report += f"   {sym_short}: {mom_desc}\n"
        report += f"        {vol_desc}\n"

    report += f"\n:arrows_counterclockwise: *거래*: "
    if executed_trades:
        report += f"{len(executed_trades)}건 실행\n"
        for ex in executed_trades:
            sym_short = ex.symbol.split("/")[0]
            side_kr = "매수" if ex.side == "BUY" else "매도"
            notional = ex.qty * ex.fill_price
            report += f"   {sym_short} {side_kr} {ex.qty:.4f} (${notional:,.0f}) @ ${ex.fill_price:,.0f}\n"
            report += f"   슬리피지 {ex.slippage_bps:.1f}bps | 수수료 ~${notional * ex.fee_bps / 10000:.2f}\n"
    else:
        skipped = [t for t in targets if not t.execute and t.reason == "THRESHOLD_SKIP"]
        if skipped:
            max_delta = max(abs(t.delta) for t in skipped)
            report += f"없음\n   이유: 포지션 변경폭 {max_delta:.1%} < {POSITION_THRESHOLD:.0%} threshold → 수수료 절약 위해 skip\n"
        else:
            report += "없음\n"

    # [FIX #2] 실행 모드 명시
    if mode == "simulate":
        report += f"\n:grey_question: _시뮬레이션 모드 — 실제 주문 아님_"
    elif mode == "paper":
        report += f"\n:test_tube: _페이퍼 트레이딩 (Binance Testnet)_"

    if not triggered and not failed:
        report += f"\n:white_check_mark: 정상 작동"
    elif failed:
        report += f"\n:warning: 일부 주문 실패 — 다음 실행에서 자동 재시도"

    send_slack(report, "INFO")

    # LLM 분석용 JSON 내보내기
    try:
        export_path = scribe.export_daily_for_llm()
        log.info(f"Exported daily result to {export_path}")
    except Exception as e:
        log.warning(f"Failed to export daily result: {e}")

    log.info("=== Daily run complete ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crypto AFO Stage 2")
    parser.add_argument("--mode", choices=["paper", "live", "simulate", "guardian"], default="paper")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # 로깅 설정
    os.makedirs("logs", exist_ok=True)
    log_file = "logs/guardian.log" if args.mode == "guardian" else "logs/daily.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(log_file)
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root_logger.addHandler(fh)
    root_logger.addHandler(sh)

    log = logging.getLogger(__name__)

    init_db()
    migrate_v2()

    if args.mode == "guardian":
        run_guardian()
    else:
        run_daily(mode=args.mode, dry_run=args.dry_run)
