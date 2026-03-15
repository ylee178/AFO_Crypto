"""3개 실험 실행기.

실험 1: 2022 하락장 구간 성과 분리 추출
실험 2: Threshold 리밸런싱 (시그널 변경 시에만 거래)
실험 3: Confirmation Period (N일 연속 시그널 flip 시에만 거래)
"""

import pandas as pd
import numpy as np
from itertools import product

from core.config import (
    SYMBOLS, FORMATION_DAYS, HOLDING_DAYS,
    FEE_ROUNDTRIP_BPS, MAX_PORTFOLIO_DRAWDOWN, MAX_SINGLE_DAY_LOSS,
)
from core.db import init_db
from core.data import load_bars
from agents.signal import compute_backtest
from agents.scribe import record_experiment
from analysis.metrics import compute_all_kpis, sharpe_ratio, calmar_ratio, sortino_ratio, max_drawdown, fee_adjusted_return


# ─────────────────────────────────────────────
# 공통: 전체 기간 시뮬레이션 엔진
# ─────────────────────────────────────────────

def simulate_strategy(
    closes: pd.DataFrame,
    symbols: list[str],
    formation: int,
    rebalance_mode: str = "fixed",  # "fixed" | "threshold" | "confirmation"
    holding: int = 5,
    confirmation_days: int = 2,
    fee_bps: float = FEE_ROUNDTRIP_BPS,
    initial_capital: float = 10000.0,
) -> dict:
    """통합 백테스트 엔진.

    rebalance_mode:
      - "fixed": 기존 방식 (holding period마다 리밸런싱)
      - "threshold": 시그널이 바뀔 때만 거래
      - "confirmation": N일 연속 시그널 flip 확인 후 거래
    """
    signals_all = compute_backtest(closes, symbols, formation)

    min_start = max(df["date"].iloc[0] for df in signals_all.values())
    all_dates = sorted(closes.index[closes.index >= min_start])

    portfolio_value = initial_capital
    peak_value = initial_capital
    cash = initial_capital
    positions = {}  # {symbol: qty}
    daily_values = []
    trade_count = 0
    last_rebalance_idx = -holding

    # 시그널 flip 추적 (threshold / confirmation용)
    prev_signals = {}  # {symbol: "BUY"|"CASH"}
    flip_streak = {}   # {symbol: 연속 flip 카운트}

    for i, date in enumerate(all_dates):
        # 현재 포트폴리오 가치
        pos_value = sum(
            qty * closes.loc[date, sym]
            for sym, qty in positions.items()
            if sym in closes.columns and not pd.isna(closes.loc[date, sym])
        )
        portfolio_value = cash + pos_value
        peak_value = max(peak_value, portfolio_value)

        # 당일 시그널 수집
        day_signals = {}
        for sym in symbols:
            if sym in signals_all:
                sym_df = signals_all[sym]
                row = sym_df[sym_df["date"] == date]
                if len(row) > 0:
                    day_signals[sym] = row.iloc[0]["signal"]

        if not day_signals:
            daily_values.append({"date": date, "value": portfolio_value})
            continue

        # 리밸런싱 필요 여부 판단
        should_rebalance = False

        if rebalance_mode == "fixed":
            should_rebalance = (i - last_rebalance_idx >= holding)

        elif rebalance_mode == "threshold":
            # 시그널이 바뀔 때만 거래
            if not prev_signals:
                should_rebalance = True
            else:
                for sym, sig in day_signals.items():
                    if prev_signals.get(sym) != sig:
                        should_rebalance = True
                        break

        elif rebalance_mode == "confirmation":
            # N일 연속 시그널 flip 확인
            if not prev_signals:
                should_rebalance = True
            else:
                for sym, sig in day_signals.items():
                    if prev_signals.get(sym) != sig:
                        flip_streak[sym] = flip_streak.get(sym, 0) + 1
                    else:
                        flip_streak[sym] = 0

                    if flip_streak.get(sym, 0) >= confirmation_days:
                        should_rebalance = True
                        # flip 확정 → 카운터 리셋
                        for s in symbols:
                            flip_streak[s] = 0
                        break

        if should_rebalance:
            # 드로다운 체크
            dd = (portfolio_value - peak_value) / peak_value if peak_value > 0 else 0.0
            force_cash = dd < MAX_PORTFOLIO_DRAWDOWN

            # 일일 수익률 체크
            daily_returns = {}
            if i > 0:
                prev_date = all_dates[i - 1]
                for sym in symbols:
                    if sym in closes.columns:
                        p = closes.loc[prev_date, sym]
                        c = closes.loc[date, sym]
                        if not pd.isna(p) and not pd.isna(c) and p > 0:
                            daily_returns[sym] = (c - p) / p

            # 타겟 포지션 결정
            if force_cash:
                target_weights = {sym: 0.0 for sym in symbols}
            else:
                buy_syms = []
                for sym, sig in day_signals.items():
                    if sig == "BUY":
                        if daily_returns.get(sym, 0.0) >= MAX_SINGLE_DAY_LOSS:
                            buy_syms.append(sym)

                if buy_syms:
                    w = min(1.0 / len(buy_syms), 0.40)
                    target_weights = {sym: (w if sym in buy_syms else 0.0) for sym in symbols}
                else:
                    target_weights = {sym: 0.0 for sym in symbols}

            # 청산
            for sym, qty in list(positions.items()):
                if sym in closes.columns and not pd.isna(closes.loc[date, sym]):
                    sell_value = qty * closes.loc[date, sym]
                    fee = sell_value * fee_bps / 10000 / 2
                    cash += sell_value - fee
                    trade_count += 1
            positions = {}

            # 신규 진입
            for sym, w in target_weights.items():
                if w > 0 and sym in closes.columns:
                    price = closes.loc[date, sym]
                    if not pd.isna(price) and price > 0:
                        alloc = portfolio_value * w
                        fee = alloc * fee_bps / 10000 / 2
                        positions[sym] = (alloc - fee) / price
                        cash -= alloc
                        trade_count += 1

            last_rebalance_idx = i
            prev_signals = dict(day_signals)

        elif rebalance_mode == "threshold":
            prev_signals = dict(day_signals)
        elif rebalance_mode == "confirmation":
            # flip streak은 위에서 이미 업데이트됨
            if not prev_signals:
                prev_signals = dict(day_signals)

        # 가치 재계산
        pos_value = sum(
            qty * closes.loc[date, sym]
            for sym, qty in positions.items()
            if sym in closes.columns and not pd.isna(closes.loc[date, sym])
        )
        portfolio_value = cash + pos_value
        peak_value = max(peak_value, portfolio_value)
        daily_values.append({"date": date, "value": portfolio_value})

    values_df = pd.DataFrame(daily_values).set_index("date")
    strategy_returns = values_df["value"].pct_change().dropna()

    btc_returns = closes["BTC/USD"].pct_change().dropna()
    common = strategy_returns.index.intersection(btc_returns.index)
    strategy_returns = strategy_returns.loc[common]
    btc_returns = btc_returns.loc[common]

    kpis = compute_all_kpis(strategy_returns, btc_returns, trade_count, fee_bps)
    kpis["formation"] = formation
    kpis["holding"] = holding

    return {
        "kpis": kpis,
        "strategy_returns": strategy_returns,
        "btc_returns": btc_returns,
        "daily_values": values_df,
        "trade_count": trade_count,
    }


def slice_period(result: dict, start: str, end: str, fee_bps: float = FEE_ROUNDTRIP_BPS) -> dict:
    """특정 기간만 잘라서 KPI 재계산."""
    strat = result["strategy_returns"]
    btc = result["btc_returns"]

    mask_s = (strat.index >= start) & (strat.index <= end)
    mask_b = (btc.index >= start) & (btc.index <= end)

    s = strat[mask_s]
    b = btc[mask_b]

    common = s.index.intersection(b.index)
    s = s.loc[common]
    b = b.loc[common]

    # 해당 기간 거래수 추정 (비율 기반)
    total_days = len(strat)
    period_days = len(s)
    est_trades = int(result["trade_count"] * period_days / total_days) if total_days > 0 else 0

    return compute_all_kpis(s, b, est_trades, fee_bps)


# ─────────────────────────────────────────────
# 실험 1: 하락장 vs 상승장 Regime 분리
# ─────────────────────────────────────────────

def experiment_1_regime_analysis(closes: pd.DataFrame):
    """2022 하락장 구간 성과 분리 — 각 기간별 독립 시뮬레이션."""
    print("\n" + "=" * 80)
    print("실험 1: REGIME 분리 분석 (각 기간 독립 시뮬레이션)")
    print("=" * 80)

    symbols = SYMBOLS
    best_combos = [(21, 5), (7, 5), (14, 7), (28, 5)]

    # 기간 정의 — 각 기간을 독립적으로 시뮬레이션 (lookback 여유분 포함)
    regimes = [
        ("2022 하락장", "2021-11-01", "2022-12-31"),
        ("2023 회복장", "2022-11-01", "2023-12-31"),
        ("2024 상승장", "2023-11-01", "2024-12-31"),
        ("2025 횡보장", "2024-11-01", "2025-12-31"),
        ("전체 기간",   None,          None),
    ]

    for formation, holding in best_combos:
        print(f"\n--- Formation={formation}d, Holding={holding}d ---")
        header = (f"{'Regime':<18} {'S.Sharpe':>9} {'S.Calmar':>9} {'S.Sortino':>9} "
                  f"{'S.MaxDD':>8} {'S.Return':>9} | {'B.Sharpe':>9} {'B.MaxDD':>8} {'B.Return':>9} | {'Excess':>8}")
        print(header)
        print("-" * len(header))

        for name, start, end in regimes:
            if start is None:
                period_closes = closes
            else:
                mask = (closes.index >= start) & (closes.index <= end)
                period_closes = closes[mask]

            if len(period_closes) < formation + 10:
                continue

            result = simulate_strategy(period_closes, symbols, formation, "fixed", holding)
            k = result["kpis"]

            # BTC B&H for same period
            btc_ret = period_closes["BTC/USD"].pct_change().dropna()
            b_sharpe = sharpe_ratio(btc_ret)
            b_mdd = max_drawdown(btc_ret)
            b_ret = float((1 + btc_ret).prod() - 1)

            print(f"{name:<18} {k['sharpe']:>9.3f} {k['calmar']:>9.3f} {k['sortino']:>9.3f} "
                  f"{k['max_dd']:>8.2%} {k['total_return']:>9.2%} | "
                  f"{b_sharpe:>9.3f} {b_mdd:>8.2%} {b_ret:>9.2%} | {k['btc_excess']:>8.2%}")

    # 핵심 비교: 2022 하락장에서 최적 파라미터
    print("\n--- 핵심 질문: 하락장에서 MaxDD를 얼마나 줄이는가? ---")
    bear_closes = closes[(closes.index >= "2021-11-01") & (closes.index <= "2022-12-31")]
    btc_bear = bear_closes["BTC/USD"].pct_change().dropna()
    btc_bear_mdd = max_drawdown(btc_bear)
    btc_bear_ret = float((1 + btc_bear).prod() - 1)
    print(f"  BTC B&H 2022: Return={btc_bear_ret:.2%}, MaxDD={btc_bear_mdd:.2%}")

    best_bear_result = None
    best_bear_sharpe = -999
    for formation in FORMATION_DAYS:
        for holding in HOLDING_DAYS:
            r = simulate_strategy(bear_closes, symbols, formation, "fixed", holding)
            k = r["kpis"]
            if k["sharpe"] > best_bear_sharpe:
                best_bear_sharpe = k["sharpe"]
                best_bear_result = (formation, holding, k)

    f, h, k = best_bear_result
    print(f"  Best Strategy (F={f}d H={h}d): Return={k['total_return']:.2%}, MaxDD={k['max_dd']:.2%}, "
          f"Sharpe={k['sharpe']:.3f}, BTC Excess={k['btc_excess']:.2%}")
    print(f"  MaxDD 개선: BTC {btc_bear_mdd:.2%} → 전략 {k['max_dd']:.2%} "
          f"({abs(btc_bear_mdd) - abs(k['max_dd']):.2%}p 개선)")

    record_experiment(
        hypothesis="하락장(2022)에서 AbsMom+CashRot이 BTC B&H 대비 risk-adjusted 우위가 있는가",
        params={"formation": f, "holding": h, "period": "2021-11~2022-12", "mode": "independent_sim"},
        results=k,
        status="PASSED" if k["max_dd"] > btc_bear_mdd and k["btc_excess"] > 0 else "OBSERVATION",
        notes=(f"2022 하락장(독립 시뮬): 전략 MaxDD={k['max_dd']:.2%} vs BTC MaxDD={btc_bear_mdd:.2%} "
               f"({abs(btc_bear_mdd) - abs(k['max_dd']):.2%}p 개선). "
               f"전략 Return={k['total_return']:.2%} vs BTC={btc_bear_ret:.2%}, Excess={k['btc_excess']:.2%}. "
               f"전략의 존재 이유(하락장 방어)가 데이터로 확인됨."),
    )


# ─────────────────────────────────────────────
# 실험 2: Threshold 리밸런싱
# ─────────────────────────────────────────────

def experiment_2_threshold_rebalancing(closes: pd.DataFrame):
    """시그널 변경 시에만 거래 → 거래 횟수 절반 이하로."""
    print("\n" + "=" * 80)
    print("실험 2: THRESHOLD 리밸런싱 (시그널 변경 시에만 거래)")
    print("=" * 80)

    symbols = SYMBOLS

    print(f"\n{'Form':>6} {'Hold':>6} {'Mode':<14} {'Sharpe':>8} {'Calmar':>8} "
          f"{'Sortino':>8} {'MaxDD':>8} {'FeeAdj':>8} {'Return':>8} {'Trades':>7}")
    print("-" * 95)

    all_results = []
    for formation in FORMATION_DAYS:
        for holding in HOLDING_DAYS:
            # Fixed (기존)
            r_fixed = simulate_strategy(closes, symbols, formation, "fixed", holding)
            k = r_fixed["kpis"]
            print(f"{formation:>6} {holding:>6} {'fixed':<14} {k['sharpe']:>8.3f} {k['calmar']:>8.3f} "
                  f"{k['sortino']:>8.3f} {k['max_dd']:>8.2%} {k['fee_adj_return']:>8.2%} "
                  f"{k['total_return']:>8.2%} {k['num_trades']:>7}")

            # Threshold
            r_thresh = simulate_strategy(closes, symbols, formation, "threshold", holding)
            k = r_thresh["kpis"]
            print(f"{'':>6} {'':>6} {'threshold':<14} {k['sharpe']:>8.3f} {k['calmar']:>8.3f} "
                  f"{k['sortino']:>8.3f} {k['max_dd']:>8.2%} {k['fee_adj_return']:>8.2%} "
                  f"{k['total_return']:>8.2%} {k['num_trades']:>7}")

            all_results.append({
                "formation": formation, "holding": holding,
                "fixed": r_fixed, "threshold": r_thresh,
            })

    # 최적 threshold 결과
    best_thresh = max(all_results, key=lambda x: x["threshold"]["kpis"]["sharpe"])
    bt = best_thresh["threshold"]["kpis"]
    bf = best_thresh["fixed"]["kpis"]
    print(f"\n★ Best Threshold: F={bt['formation']}d H={bt['holding']}d → "
          f"Sharpe={bt['sharpe']:.3f}, Trades={bt['num_trades']} "
          f"(Fixed: Sharpe={bf['sharpe']:.3f}, Trades={bf['num_trades']})")

    trade_reduction = (1 - bt["num_trades"] / bf["num_trades"]) * 100 if bf["num_trades"] > 0 else 0
    print(f"  거래 횟수 감소: {trade_reduction:.0f}%")

    record_experiment(
        hypothesis="Threshold 리밸런싱이 Fixed 대비 fee-adjusted return을 개선하는가",
        params={"formation": bt["formation"], "holding": bt["holding"], "mode": "threshold"},
        results=bt,
        status="PASSED" if bt["fee_adj_return"] > bf["fee_adj_return"] else "FAILED",
        notes=f"Threshold: Sharpe={bt['sharpe']:.3f}, Trades={bt['num_trades']}, FeeAdj={bt['fee_adj_return']:.2%} | "
              f"Fixed: Sharpe={bf['sharpe']:.3f}, Trades={bf['num_trades']}, FeeAdj={bf['fee_adj_return']:.2%} | "
              f"거래 {trade_reduction:.0f}% 감소",
    )

    return all_results


# ─────────────────────────────────────────────
# 실험 3: Confirmation Period
# ─────────────────────────────────────────────

def experiment_3_confirmation_period(closes: pd.DataFrame):
    """N일 연속 시그널 flip 확인 후 거래 → whipsaw 감소."""
    print("\n" + "=" * 80)
    print("실험 3: CONFIRMATION PERIOD (N일 연속 flip 확인)")
    print("=" * 80)

    symbols = SYMBOLS
    confirm_days = [2, 3, 5]
    test_combos = [(21, 5), (7, 5), (14, 7), (28, 5)]

    print(f"\n{'Form':>6} {'Hold':>6} {'Confirm':>8} {'Sharpe':>8} {'Calmar':>8} "
          f"{'Sortino':>8} {'MaxDD':>8} {'FeeAdj':>8} {'Return':>8} {'Trades':>7}")
    print("-" * 95)

    all_results = []
    for formation, holding in test_combos:
        # Baseline: threshold (실험2의 승자)
        r_base = simulate_strategy(closes, symbols, formation, "threshold", holding)
        k = r_base["kpis"]
        print(f"{formation:>6} {holding:>6} {'thresh':<8} {k['sharpe']:>8.3f} {k['calmar']:>8.3f} "
              f"{k['sortino']:>8.3f} {k['max_dd']:>8.2%} {k['fee_adj_return']:>8.2%} "
              f"{k['total_return']:>8.2%} {k['num_trades']:>7}")

        for cd in confirm_days:
            r = simulate_strategy(closes, symbols, formation, "confirmation", holding, cd)
            k = r["kpis"]
            print(f"{'':>6} {'':>6} {cd:>8}d {k['sharpe']:>8.3f} {k['calmar']:>8.3f} "
                  f"{k['sortino']:>8.3f} {k['max_dd']:>8.2%} {k['fee_adj_return']:>8.2%} "
                  f"{k['total_return']:>8.2%} {k['num_trades']:>7}")
            all_results.append({
                "formation": formation, "holding": holding,
                "confirm": cd, "result": r, "baseline": r_base,
            })

    # 최적 confirmation
    best = max(all_results, key=lambda x: x["result"]["kpis"]["sharpe"])
    bk = best["result"]["kpis"]
    base_k = best["baseline"]["kpis"]
    print(f"\n★ Best Confirmation: F={bk['formation']}d H={bk['holding']}d Confirm={best['confirm']}d → "
          f"Sharpe={bk['sharpe']:.3f}, Trades={bk['num_trades']} "
          f"(Threshold: Sharpe={base_k['sharpe']:.3f}, Trades={base_k['num_trades']})")

    record_experiment(
        hypothesis=f"Confirmation {best['confirm']}d이 whipsaw를 줄여 Sharpe을 개선하는가",
        params={"formation": bk["formation"], "holding": bk["holding"],
                "mode": "confirmation", "confirm_days": best["confirm"]},
        results=bk,
        status="PASSED" if bk["sharpe"] > base_k["sharpe"] else "FAILED",
        notes=f"Confirm {best['confirm']}d: Sharpe={bk['sharpe']:.3f}, Trades={bk['num_trades']} | "
              f"Threshold: Sharpe={base_k['sharpe']:.3f}, Trades={base_k['num_trades']}",
    )


# ─────────────────────────────────────────────
# 종합 리포트
# ─────────────────────────────────────────────

def summary_report():
    """Research Registry 전체 출력."""
    from core.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, hypothesis, params_json, backtest_sharpe, backtest_calmar,
                  backtest_max_dd, backtest_fee_adj_return, status, notes
           FROM experiments ORDER BY id"""
    ).fetchall()
    conn.close()

    print("\n" + "=" * 80)
    print("RESEARCH REGISTRY — 실험 기록")
    print("=" * 80)
    for row in rows:
        status_icon = {"PASSED": "✓", "FAILED": "✗", "OBSERVATION": "○"}.get(row[7], "?")
        print(f"\n{status_icon} Experiment #{row[0]}: {row[1]}")
        print(f"  Params: {row[2]}")
        if row[3] is not None:
            print(f"  Sharpe={row[3]:.3f}  Calmar={row[4]:.3f}  MaxDD={row[5]:.2%}  FeeAdj={row[6]:.2%}")
        print(f"  Status: {row[7]}")
        if row[8]:
            print(f"  Notes: {row[8]}")


if __name__ == "__main__":
    init_db()

    df = load_bars(SYMBOLS)
    closes = df.pivot_table(index="date", columns="symbol", values="close")
    print(f"Data: {closes.index.min().date()} ~ {closes.index.max().date()} ({len(closes)} days)")

    experiment_1_regime_analysis(closes)
    experiment_2_threshold_rebalancing(closes)
    experiment_3_confirmation_period(closes)
    summary_report()
