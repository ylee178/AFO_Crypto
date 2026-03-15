"""백테스트 실행기 — 16개 파라미터 조합 Grid Search."""

import argparse
import json
from datetime import datetime
from itertools import product

import pandas as pd
import numpy as np

from core.config import (
    SYMBOLS,
    FORMATION_DAYS, HOLDING_DAYS,
    VOL_LOOKBACK, VOL_TARGET, MAX_VOL_SCALE,
    MAX_SINGLE_POSITION_WEIGHT,
    FEE_ROUNDTRIP_BPS,
)
from core.db import init_db, get_connection
from core.data import load_bars
from agents.signal import compute_backtest
from agents.scribe import record_experiment
from analysis.metrics import compute_all_kpis
from analysis.benchmark import compare_all, format_comparison

# Grid search 범위 (단일값일 경우 리스트로 변환)
_FORMATION_GRID = FORMATION_DAYS if isinstance(FORMATION_DAYS, list) else [FORMATION_DAYS]
_HOLDING_GRID = HOLDING_DAYS if isinstance(HOLDING_DAYS, list) else [HOLDING_DAYS]


def run_single_backtest(
    closes: pd.DataFrame,
    symbols: list[str],
    formation: int,
    holding: int,
    fee_bps: float = FEE_ROUNDTRIP_BPS,
    initial_capital: float = 10000.0,
) -> dict:
    """단일 파라미터 조합 백테스트 실행."""
    # 시그널 계산
    signals_all = compute_backtest(closes, symbols, formation)

    # 가장 짧은 시그널 기간에 맞춤
    min_start = max(
        df["date"].iloc[0] for df in signals_all.values()
    )

    # 포트폴리오 시뮬레이션
    all_dates = sorted(closes.index[closes.index >= min_start])

    portfolio_value = initial_capital
    peak_value = initial_capital
    cash = initial_capital
    positions = {}  # {symbol: qty}
    daily_values = []
    trade_count = 0
    last_rebalance_idx = -holding  # 첫 거래 즉시 가능

    for i, date in enumerate(all_dates):
        # 현재 포트폴리오 가치 계산
        pos_value = sum(
            qty * closes.loc[date, sym]
            for sym, qty in positions.items()
            if sym in closes.columns and not pd.isna(closes.loc[date, sym])
        )
        portfolio_value = cash + pos_value
        peak_value = max(peak_value, portfolio_value)

        # 리밸런싱 체크 (holding period마다)
        if i - last_rebalance_idx >= holding:
            # 각 종목 시그널 수집
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

            # 일일 수익률 계산
            daily_returns = {}
            if i > 0:
                prev_date = all_dates[i - 1]
                for sym in symbols:
                    if sym in closes.columns:
                        prev_close = closes.loc[prev_date, sym]
                        curr_close = closes.loc[date, sym]
                        if not pd.isna(prev_close) and not pd.isna(curr_close) and prev_close > 0:
                            daily_returns[sym] = (curr_close - prev_close) / prev_close

            # 단순 타겟 생성 (백테스트용 — 시그널 기반 비중 결정)
            from core.models import PositionTarget
            targets = {}
            buy_syms = [s for s, sig in day_signals.items() if sig == "BUY"]
            for sym, sig in day_signals.items():
                if sig == "BUY" and buy_syms:
                    tw = min(1.0 / len(buy_syms), MAX_SINGLE_POSITION_WEIGHT)
                    targets[sym] = PositionTarget(
                        symbol=sym, target_weight=tw,
                        current_weight=0.0, delta=tw,
                        execute=True, reason="BT_BUY",
                    )
                else:
                    targets[sym] = PositionTarget(
                        symbol=sym, target_weight=0.0,
                        current_weight=0.0, delta=0.0,
                        execute=False, reason="BT_CASH",
                    )

            # 포지션 조정
            # 먼저 전부 청산
            for sym, qty in list(positions.items()):
                if sym in closes.columns and not pd.isna(closes.loc[date, sym]):
                    sell_value = qty * closes.loc[date, sym]
                    fee = sell_value * fee_bps / 10000 / 2
                    cash += sell_value - fee
                    trade_count += 1
            positions = {}

            # 새 포지션 진입
            for sym, target in targets.items():
                if target.target_weight > 0 and sym in closes.columns:
                    price = closes.loc[date, sym]
                    if not pd.isna(price) and price > 0:
                        alloc = portfolio_value * target.target_weight
                        fee = alloc * fee_bps / 10000 / 2
                        buy_value = alloc - fee
                        qty = buy_value / price
                        positions[sym] = qty
                        cash -= alloc
                        trade_count += 1

            last_rebalance_idx = i

        # 현재 포트폴리오 가치 재계산
        pos_value = sum(
            qty * closes.loc[date, sym]
            for sym, qty in positions.items()
            if sym in closes.columns and not pd.isna(closes.loc[date, sym])
        )
        portfolio_value = cash + pos_value
        peak_value = max(peak_value, portfolio_value)

        daily_values.append({"date": date, "value": portfolio_value})

    # 수익률 계산
    values_df = pd.DataFrame(daily_values).set_index("date")
    strategy_returns = values_df["value"].pct_change().dropna()

    # BTC 수익률
    btc_returns = closes["BTC/USD"].pct_change().dropna()
    common = strategy_returns.index.intersection(btc_returns.index)
    strategy_returns = strategy_returns.loc[common]
    btc_returns = btc_returns.loc[common]

    kpis = compute_all_kpis(strategy_returns, btc_returns, trade_count, fee_bps)
    kpis["formation"] = formation
    kpis["holding"] = holding

    return {
        "params": {"formation": formation, "holding": holding, "fee_bps": fee_bps},
        "kpis": kpis,
        "strategy_returns": strategy_returns,
        "daily_values": values_df,
        "trade_count": trade_count,
    }


def run_grid_search(stage: int = 1) -> list[dict]:
    """Grid Search: 모든 formation × holding 조합 실행."""
    symbols = SYMBOLS if stage == 1 else SYMBOLS

    # 데이터 로드
    df = load_bars(symbols)
    closes = df.pivot_table(index="date", columns="symbol", values="close")

    print(f"Data: {closes.index.min().date()} ~ {closes.index.max().date()} ({len(closes)} days)")
    print(f"Symbols: {symbols}")
    print(f"Grid: {len(_FORMATION_GRID)} × {len(_HOLDING_GRID)} = {len(_FORMATION_GRID) * len(_HOLDING_GRID)} combinations\n")

    results = []
    for formation, holding in product(_FORMATION_GRID, _HOLDING_GRID):
        print(f"  Testing formation={formation}d, holding={holding}d ... ", end="")
        result = run_single_backtest(closes, symbols, formation, holding)
        kpis = result["kpis"]
        print(f"Sharpe={kpis['sharpe']:.3f}, Calmar={kpis['calmar']:.3f}, "
              f"MaxDD={kpis['max_dd']:.2%}, Trades={kpis['num_trades']}")

        # 실험 기록
        status = "PASSED" if kpis["sharpe"] > 1.0 and kpis["max_dd"] > -0.30 else "FAILED"
        record_experiment(
            hypothesis=f"Formation {formation}d + Holding {holding}d → Sharpe > 1.0",
            params=result["params"],
            results=kpis,
            status=status,
        )

        results.append(result)

    return results


def print_results_table(results: list[dict]):
    """Grid Search 결과 테이블 출력."""
    print("\n" + "=" * 90)
    print("GRID SEARCH RESULTS")
    print("=" * 90)

    header = (f"{'Form':>6} {'Hold':>6} {'Sharpe':>8} {'Calmar':>8} {'Sortino':>8} "
              f"{'MaxDD':>8} {'FeeAdj':>8} {'BTC+':>8} {'Trades':>7}")
    print(header)
    print("-" * 90)

    sorted_results = sorted(results, key=lambda r: r["kpis"]["sharpe"], reverse=True)

    for r in sorted_results:
        k = r["kpis"]
        print(f"{k['formation']:>6} {k['holding']:>6} {k['sharpe']:>8.3f} {k['calmar']:>8.3f} "
              f"{k['sortino']:>8.3f} {k['max_dd']:>8.2%} {k['fee_adj_return']:>8.2%} "
              f"{k['btc_excess']:>8.2%} {k['num_trades']:>7}")

    # Best combo
    best = sorted_results[0]
    print(f"\n★ Best: formation={best['kpis']['formation']}d, holding={best['kpis']['holding']}d "
          f"(Sharpe={best['kpis']['sharpe']:.3f})")

    # Stage 1→2 승격 체크
    # Stage 승격 기준은 PRD v3에서 재정의됨 (Calmar > 0.4, DD protection > 50%)
    from core.config import STAGE_2_TO_3
    print(f"  Calmar: {best['kpis']['calmar']:.3f} (기준 > 0.4: {'✓' if best['kpis']['calmar'] > 0.4 else '✗'})")


def print_benchmark_comparison(results: list[dict]):
    """최적 전략 vs 벤치마크 비교."""
    best = max(results, key=lambda r: r["kpis"]["sharpe"])

    df = load_bars(SYMBOLS)
    closes = df.pivot_table(index="date", columns="symbol", values="close")

    comparison = compare_all(
        best["strategy_returns"],
        closes,
        SYMBOLS,
        best["trade_count"],
    )

    print("\n" + "=" * 65)
    print(f"BENCHMARK COMPARISON (Best: F={best['kpis']['formation']}d H={best['kpis']['holding']}d)")
    print("=" * 65)
    print(format_comparison(comparison))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crypto AFO Backtest")
    parser.add_argument("--stage", type=int, default=1, choices=[1, 2])
    args = parser.parse_args()

    init_db()
    results = run_grid_search(args.stage)
    print_results_table(results)
    print_benchmark_comparison(results)
