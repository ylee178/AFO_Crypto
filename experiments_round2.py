"""실험 4 & 5 — 혼합 포트폴리오 + Vol Scaling.

실험 4: BTC Core + Strategy Overlay 혼합 포트폴리오
실험 5: Barroso-Santa Clara Vol Scaling
"""

import pandas as pd
import numpy as np

from core.config import SYMBOLS, FEE_ROUNDTRIP_BPS, FORMATION_DAYS, HOLDING_DAYS
from core.db import init_db
from core.data import load_bars
from agents.signal import compute_backtest
from agents.scribe import record_experiment
from analysis.metrics import (
    compute_all_kpis, sharpe_ratio, calmar_ratio, sortino_ratio,
    max_drawdown, fee_adjusted_return,
)
from experiments import simulate_strategy


# ─────────────────────────────────────────────
# Vol Scaling 엔진
# ─────────────────────────────────────────────

def simulate_vol_scaled(
    closes: pd.DataFrame,
    symbols: list[str],
    formation: int,
    confirmation_days: int = 5,
    vol_lookback: int = 20,
    vol_target: float = 0.15,  # 연환산 목표 변동성 15%
    fee_bps: float = FEE_ROUNDTRIP_BPS,
    initial_capital: float = 10000.0,
) -> dict:
    """Barroso-Santa Clara vol scaling + confirmation 기반 백테스트.

    포지션 크기 = vol_target / realized_vol × base_weight
    변동성 높으면 포지션 줄이고, 낮으면 키운다. 단, 레버리지 없으므로 최대 1.0.
    """
    signals_all = compute_backtest(closes, symbols, formation)
    min_start = max(df["date"].iloc[0] for df in signals_all.values())
    all_dates = sorted(closes.index[closes.index >= min_start])

    # 사전 계산: 각 종목의 실현 변동성
    realized_vols = {}
    for sym in symbols:
        if sym in closes.columns:
            rets = closes[sym].pct_change()
            realized_vols[sym] = rets.rolling(vol_lookback).std() * np.sqrt(365)

    portfolio_value = initial_capital
    peak_value = initial_capital
    cash = initial_capital
    positions = {}
    daily_values = []
    trade_count = 0

    prev_signals = {}
    flip_streak = {}

    for i, date in enumerate(all_dates):
        # 현재 포트폴리오 가치
        pos_value = sum(
            qty * closes.loc[date, sym]
            for sym, qty in positions.items()
            if sym in closes.columns and not pd.isna(closes.loc[date, sym])
        )
        portfolio_value = cash + pos_value
        peak_value = max(peak_value, portfolio_value)

        # 시그널 수집
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

        # Confirmation 리밸런싱 판단
        should_rebalance = False
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
                    for s in symbols:
                        flip_streak[s] = 0
                    break

        if should_rebalance:
            # 드로다운 체크
            dd = (portfolio_value - peak_value) / peak_value if peak_value > 0 else 0.0
            force_cash = dd < -0.25

            if force_cash:
                target_weights = {sym: 0.0 for sym in symbols}
            else:
                buy_syms = [sym for sym, sig in day_signals.items() if sig == "BUY"]

                if buy_syms:
                    base_weight = min(1.0 / len(buy_syms), 0.40)
                    target_weights = {}

                    for sym in symbols:
                        if sym in buy_syms:
                            # Vol scaling: 변동성 높으면 포지션 줄임
                            rv = realized_vols.get(sym)
                            if rv is not None and date in rv.index and not pd.isna(rv.loc[date]):
                                current_vol = rv.loc[date]
                                if current_vol > 0:
                                    scale = vol_target / current_vol
                                    scale = min(scale, 1.5)  # 최대 1.5x (레버리지 없음 대비 여유)
                                    scale = max(scale, 0.1)  # 최소 10%
                                else:
                                    scale = 1.0
                            else:
                                scale = 1.0

                            w = base_weight * scale
                            target_weights[sym] = min(w, 0.40)  # 단일 종목 한도
                        else:
                            target_weights[sym] = 0.0

                    # 총 비중이 1.0 초과하지 않도록
                    total_w = sum(target_weights.values())
                    if total_w > 1.0:
                        for sym in target_weights:
                            target_weights[sym] /= total_w
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
    kpis["holding"] = 0  # confirmation 기반

    return {
        "kpis": kpis,
        "strategy_returns": strategy_returns,
        "btc_returns": btc_returns,
        "daily_values": values_df,
        "trade_count": trade_count,
    }


# ─────────────────────────────────────────────
# 혼합 포트폴리오 엔진
# ─────────────────────────────────────────────

def simulate_blend(
    strategy_returns: pd.Series,
    btc_returns: pd.Series,
    btc_weight: float,
) -> pd.Series:
    """BTC B&H + Strategy 혼합 포트폴리오 일일 수익률."""
    common = strategy_returns.index.intersection(btc_returns.index)
    s = strategy_returns.loc[common]
    b = btc_returns.loc[common]
    return btc_weight * b + (1 - btc_weight) * s


# ─────────────────────────────────────────────
# 실험 4: 혼합 포트폴리오
# ─────────────────────────────────────────────

def experiment_4_blend_portfolio(closes: pd.DataFrame):
    """BTC Core + Strategy Overlay 혼합 비율 최적화."""
    print("\n" + "=" * 90)
    print("실험 4: 혼합 포트폴리오 (BTC Core + Strategy Overlay)")
    print("=" * 90)

    symbols = SYMBOLS
    blend_ratios = [0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]
    # btc_weight: 0.0 = 100% strategy, 1.0 = 100% BTC

    # 후보 전략: 실험 3에서 가장 유망했던 조합들
    candidates = [
        ("F21d Confirm5d", 21, 5, "confirmation"),
        ("F28d Confirm5d", 28, 5, "confirmation"),
        ("F7d Threshold",  7,  5, "threshold"),
    ]

    btc_returns = closes["BTC/USD"].pct_change().dropna()

    for label, formation, holding, mode in candidates:
        if mode == "confirmation":
            result = simulate_strategy(closes, symbols, formation, mode, holding, confirmation_days=5)
        else:
            result = simulate_strategy(closes, symbols, formation, mode, holding)

        strat_returns = result["strategy_returns"]

        print(f"\n--- {label} (Standalone: Sharpe={result['kpis']['sharpe']:.3f}, "
              f"MaxDD={result['kpis']['max_dd']:.2%}, Return={result['kpis']['total_return']:.2%}) ---")

        header = (f"{'BTC%':>6} {'Strat%':>6} {'Sharpe':>8} {'Calmar':>8} {'Sortino':>8} "
                  f"{'MaxDD':>8} {'Return':>9} {'BTC Exc':>9}")
        print(header)
        print("-" * len(header))

        best_sharpe = -999
        best_ratio = None

        for btc_w in blend_ratios:
            blend_ret = simulate_blend(strat_returns, btc_returns, btc_w)
            # 혼합 포트폴리오의 거래 횟수 = 전략 부분의 거래만
            est_trades = int(result["trade_count"] * (1 - btc_w))
            kpis = compute_all_kpis(blend_ret, btc_returns.loc[blend_ret.index], est_trades, FEE_ROUNDTRIP_BPS)

            print(f"{btc_w:>5.0%} {1-btc_w:>6.0%} {kpis['sharpe']:>8.3f} {kpis['calmar']:>8.3f} "
                  f"{kpis['sortino']:>8.3f} {kpis['max_dd']:>8.2%} {kpis['total_return']:>9.2%} "
                  f"{kpis['btc_excess']:>9.2%}")

            if kpis["sharpe"] > best_sharpe:
                best_sharpe = kpis["sharpe"]
                best_ratio = btc_w
                best_kpis = kpis

        print(f"\n  ★ 최적 혼합: BTC {best_ratio:.0%} + Strategy {1-best_ratio:.0%} → "
              f"Sharpe={best_kpis['sharpe']:.3f}, MaxDD={best_kpis['max_dd']:.2%}")

    # BTC 100% 기준값
    print(f"\n--- BTC 100% B&H ---")
    btc_kpis = compute_all_kpis(btc_returns, btc_returns, 0, 0)
    print(f"  Sharpe={btc_kpis['sharpe']:.3f}, MaxDD={btc_kpis['max_dd']:.2%}, Return={btc_kpis['total_return']:.2%}")

    # Regime별 혼합 분석 — 2022 하락장에서 혼합이 얼마나 도움되는지
    print("\n--- Regime별 혼합 효과 (F21d Confirm5d, BTC 60% + Strategy 40%) ---")
    regimes = [
        ("2022 하락장", "2021-11-01", "2022-12-31"),
        ("2023 회복장", "2022-11-01", "2023-12-31"),
        ("2024 상승장", "2023-11-01", "2024-12-31"),
        ("2025 횡보장", "2024-11-01", "2025-12-31"),
        ("전체",       None,          None),
    ]

    header = f"{'Regime':<18} {'Blend Sharpe':>12} {'BTC Sharpe':>11} {'Blend MaxDD':>12} {'BTC MaxDD':>10} {'Blend Ret':>10} {'BTC Ret':>9}"
    print(header)
    print("-" * len(header))

    for name, start, end in regimes:
        if start is None:
            period_closes = closes
        else:
            mask = (closes.index >= start) & (closes.index <= end)
            period_closes = closes[mask]

        if len(period_closes) < 30:
            continue

        r = simulate_strategy(period_closes, symbols, 21, "confirmation", 5, confirmation_days=5)
        period_btc = period_closes["BTC/USD"].pct_change().dropna()
        blend = simulate_blend(r["strategy_returns"], period_btc, 0.6)

        common = blend.index.intersection(period_btc.index)
        blend = blend.loc[common]
        pb = period_btc.loc[common]

        b_sharpe = sharpe_ratio(blend)
        btc_sharpe = sharpe_ratio(pb)
        b_mdd = max_drawdown(blend)
        btc_mdd = max_drawdown(pb)
        b_ret = float((1 + blend).prod() - 1)
        btc_ret = float((1 + pb).prod() - 1)

        print(f"{name:<18} {b_sharpe:>12.3f} {btc_sharpe:>11.3f} "
              f"{b_mdd:>12.2%} {btc_mdd:>10.2%} {b_ret:>10.2%} {btc_ret:>9.2%}")

    # Registry에 기록
    best_blend_result = simulate_strategy(closes, symbols, 21, "confirmation", 5, confirmation_days=5)
    blend_ret = simulate_blend(best_blend_result["strategy_returns"], btc_returns, 0.6)
    est_trades = int(best_blend_result["trade_count"] * 0.4)
    blend_kpis = compute_all_kpis(blend_ret, btc_returns.loc[blend_ret.index], est_trades, FEE_ROUNDTRIP_BPS)

    record_experiment(
        hypothesis="BTC 60% + Strategy 40% 혼합이 순수 BTC보다 risk-adjusted로 우월한가",
        params={"strategy": "F21d_Confirm5d", "btc_weight": 0.6, "strategy_weight": 0.4},
        results=blend_kpis,
        status="PASSED" if blend_kpis["sharpe"] > btc_kpis["sharpe"] else "FAILED",
        notes=(f"Blend(60/40): Sharpe={blend_kpis['sharpe']:.3f}, MaxDD={blend_kpis['max_dd']:.2%}, "
               f"Return={blend_kpis['total_return']:.2%} | "
               f"BTC B&H: Sharpe={btc_kpis['sharpe']:.3f}, MaxDD={btc_kpis['max_dd']:.2%}, "
               f"Return={btc_kpis['total_return']:.2%}"),
    )


# ─────────────────────────────────────────────
# 실험 5: Vol Scaling
# ─────────────────────────────────────────────

def experiment_5_vol_scaling(closes: pd.DataFrame):
    """Barroso-Santa Clara Vol Scaling 효과 측정."""
    print("\n" + "=" * 90)
    print("실험 5: VOL SCALING (Barroso-Santa Clara)")
    print("=" * 90)

    symbols = SYMBOLS
    vol_targets = [0.10, 0.15, 0.20, 0.25, 0.30]
    vol_lookbacks = [20, 40, 60]

    # Baseline: F=21d + Confirm=5d (vol scaling 없음)
    baseline = simulate_strategy(closes, symbols, 21, "confirmation", 5, confirmation_days=5)
    bk = baseline["kpis"]
    print(f"\nBaseline (no vol scaling): Sharpe={bk['sharpe']:.3f}, MaxDD={bk['max_dd']:.2%}, "
          f"Return={bk['total_return']:.2%}, Trades={bk['num_trades']}")

    # Formation 후보들
    formations = [14, 21, 28]

    print(f"\n{'Form':>6} {'VolLB':>6} {'VolTgt':>7} {'Sharpe':>8} {'Calmar':>8} "
          f"{'Sortino':>8} {'MaxDD':>8} {'FeeAdj':>8} {'Return':>8} {'Trades':>7}")
    print("-" * 90)

    best_sharpe = -999
    best_result = None
    best_params = None

    for formation in formations:
        for vol_lb in vol_lookbacks:
            for vol_tgt in vol_targets:
                r = simulate_vol_scaled(
                    closes, symbols, formation,
                    confirmation_days=5,
                    vol_lookback=vol_lb,
                    vol_target=vol_tgt,
                )
                k = r["kpis"]

                print(f"{formation:>6} {vol_lb:>6} {vol_tgt:>7.0%} {k['sharpe']:>8.3f} "
                      f"{k['calmar']:>8.3f} {k['sortino']:>8.3f} {k['max_dd']:>8.2%} "
                      f"{k['fee_adj_return']:>8.2%} {k['total_return']:>8.2%} {k['num_trades']:>7}")

                if k["sharpe"] > best_sharpe:
                    best_sharpe = k["sharpe"]
                    best_result = r
                    best_params = {
                        "formation": formation, "vol_lookback": vol_lb,
                        "vol_target": vol_tgt, "confirm_days": 5,
                    }

    bk2 = best_result["kpis"]
    print(f"\n★ Best Vol Scaled: F={best_params['formation']}d, VolLB={best_params['vol_lookback']}d, "
          f"VolTgt={best_params['vol_target']:.0%}")
    print(f"  Sharpe={bk2['sharpe']:.3f} (baseline {bk['sharpe']:.3f}, Δ{bk2['sharpe']-bk['sharpe']:+.3f})")
    print(f"  MaxDD={bk2['max_dd']:.2%} (baseline {bk['max_dd']:.2%})")
    print(f"  Return={bk2['total_return']:.2%} (baseline {bk['total_return']:.2%})")
    print(f"  Trades={bk2['num_trades']} (baseline {bk['num_trades']})")

    # Regime별 vol scaling 효과
    print("\n--- Regime별 Vol Scaling 효과 ---")
    regimes = [
        ("2022 하락장", "2021-11-01", "2022-12-31"),
        ("2023 회복장", "2022-11-01", "2023-12-31"),
        ("2024 상승장", "2023-11-01", "2024-12-31"),
        ("2025 횡보장", "2024-11-01", "2025-12-31"),
    ]

    header = f"{'Regime':<18} {'VS Sharpe':>10} {'Base Sharpe':>12} {'VS MaxDD':>9} {'Base MaxDD':>11} {'VS Ret':>8} {'Base Ret':>9}"
    print(header)
    print("-" * len(header))

    for name, start, end in regimes:
        mask = (closes.index >= start) & (closes.index <= end)
        period_closes = closes[mask]
        if len(period_closes) < 30:
            continue

        r_vs = simulate_vol_scaled(
            period_closes, symbols, best_params["formation"],
            confirmation_days=5,
            vol_lookback=best_params["vol_lookback"],
            vol_target=best_params["vol_target"],
        )
        r_base = simulate_strategy(period_closes, symbols, 21, "confirmation", 5, confirmation_days=5)

        vk = r_vs["kpis"]
        bsk = r_base["kpis"]

        print(f"{name:<18} {vk['sharpe']:>10.3f} {bsk['sharpe']:>12.3f} "
              f"{vk['max_dd']:>9.2%} {bsk['max_dd']:>11.2%} "
              f"{vk['total_return']:>8.2%} {bsk['total_return']:>9.2%}")

    # Vol Scaling + 혼합 포트폴리오 최종 조합
    print("\n--- 최종 조합: Vol Scaled Strategy + BTC 혼합 ---")
    btc_returns = closes["BTC/USD"].pct_change().dropna()
    btc_kpis = compute_all_kpis(btc_returns, btc_returns, 0, 0)

    header = f"{'BTC%':>6} {'Strat%':>6} {'Sharpe':>8} {'Calmar':>8} {'MaxDD':>8} {'Return':>9}"
    print(header)
    print("-" * len(header))

    best_blend_sharpe = -999
    best_blend_ratio = None
    for btc_w in [0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]:
        blend = simulate_blend(best_result["strategy_returns"], btc_returns, btc_w)
        est_trades = int(best_result["trade_count"] * (1 - btc_w))
        kpis = compute_all_kpis(blend, btc_returns.loc[blend.index], est_trades, FEE_ROUNDTRIP_BPS)

        print(f"{btc_w:>5.0%} {1-btc_w:>6.0%} {kpis['sharpe']:>8.3f} {kpis['calmar']:>8.3f} "
              f"{kpis['max_dd']:>8.2%} {kpis['total_return']:>9.2%}")

        if kpis["sharpe"] > best_blend_sharpe:
            best_blend_sharpe = kpis["sharpe"]
            best_blend_ratio = btc_w
            best_blend_kpis = kpis

    print(f"\n  ★ 최적 최종 조합: BTC {best_blend_ratio:.0%} + VolScaled Strategy {1-best_blend_ratio:.0%}")
    print(f"    Sharpe={best_blend_kpis['sharpe']:.3f}, MaxDD={best_blend_kpis['max_dd']:.2%}, "
          f"Return={best_blend_kpis['total_return']:.2%}")
    print(f"    vs BTC 100%: Sharpe={btc_kpis['sharpe']:.3f}, MaxDD={btc_kpis['max_dd']:.2%}")

    # Registry
    record_experiment(
        hypothesis="Vol Scaling(BSC)이 Confirmation 전략의 Sharpe를 개선하는가",
        params=best_params,
        results=bk2,
        status="PASSED" if bk2["sharpe"] > bk["sharpe"] else "FAILED",
        notes=(f"VolScaled: Sharpe={bk2['sharpe']:.3f}, MaxDD={bk2['max_dd']:.2%}, "
               f"Return={bk2['total_return']:.2%}, Trades={bk2['num_trades']} | "
               f"Baseline(noVS): Sharpe={bk['sharpe']:.3f}, MaxDD={bk['max_dd']:.2%}, "
               f"Return={bk['total_return']:.2%}, Trades={bk['num_trades']} | "
               f"Δ Sharpe={bk2['sharpe']-bk['sharpe']:+.3f}"),
    )

    record_experiment(
        hypothesis="VolScaled + BTC 혼합이 순수 BTC를 risk-adjusted로 이기는가",
        params={"vol_params": best_params, "btc_weight": best_blend_ratio,
                "strategy_weight": 1 - best_blend_ratio},
        results=best_blend_kpis,
        status="PASSED" if best_blend_kpis["sharpe"] > btc_kpis["sharpe"] else "FAILED",
        notes=(f"최종 조합(BTC {best_blend_ratio:.0%} + VS {1-best_blend_ratio:.0%}): "
               f"Sharpe={best_blend_kpis['sharpe']:.3f}, MaxDD={best_blend_kpis['max_dd']:.2%} | "
               f"BTC 100%: Sharpe={btc_kpis['sharpe']:.3f}, MaxDD={btc_kpis['max_dd']:.2%}"),
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

    print("\n" + "=" * 90)
    print("RESEARCH REGISTRY — 전체 실험 기록")
    print("=" * 90)
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

    experiment_4_blend_portfolio(closes)
    experiment_5_vol_scaling(closes)
    summary_report()
