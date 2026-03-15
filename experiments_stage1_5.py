"""Stage 1.5 실험 9 & 12.

실험 9: Position Threshold — 거래 빈도 감소 vs Sharpe 유지 트레이드오프
실험 12: Parameter Stability — ±20% 내 plateau 존재 여부
"""

import pandas as pd
import numpy as np

from core.config import SYMBOLS, FEE_ROUNDTRIP_BPS
from core.db import init_db
from core.data import load_bars
from agents.signal import compute_backtest
from agents.scribe import record_experiment
from analysis.metrics import (
    compute_all_kpis, sharpe_ratio, calmar_ratio, sortino_ratio,
    max_drawdown, fee_adjusted_return,
)


# ─────────────────────────────────────────────
# Vol Scaled 시뮬레이션 + Position Threshold
# ─────────────────────────────────────────────

def simulate_vol_threshold(
    closes: pd.DataFrame,
    symbols: list[str],
    formation: int = 14,
    confirmation_days: int = 5,
    vol_lookback: int = 40,
    vol_target: float = 0.10,
    position_threshold: float = 0.0,  # 0이면 threshold 없음
    fee_bps: float = FEE_ROUNDTRIP_BPS,
    initial_capital: float = 10000.0,
) -> dict:
    """Vol scaling + momentum crash filter + position change threshold."""
    signals_all = compute_backtest(closes, symbols, formation)
    min_start = max(df["date"].iloc[0] for df in signals_all.values())
    all_dates = sorted(closes.index[closes.index >= min_start])

    realized_vols = {}
    for sym in symbols:
        if sym in closes.columns:
            rets = closes[sym].pct_change()
            realized_vols[sym] = rets.rolling(vol_lookback).std() * np.sqrt(365)

    portfolio_value = initial_capital
    peak_value = initial_capital
    cash = initial_capital
    positions = {}       # {symbol: qty}
    current_weights = {} # {symbol: weight} — threshold 판단용
    daily_values = []
    trade_count = 0

    prev_signals = {}
    flip_streak = {}

    for i, date in enumerate(all_dates):
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

        # Confirmation 리밸런싱
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
            dd = (portfolio_value - peak_value) / peak_value if peak_value > 0 else 0.0
            force_cash = dd < -0.20

            if force_cash:
                target_weights = {sym: 0.0 for sym in symbols}
            else:
                buy_syms = [sym for sym, sig in day_signals.items() if sig == "BUY"]
                target_weights = {}
                if buy_syms:
                    base_weight = min(1.0 / len(buy_syms), 0.40)
                    for sym in symbols:
                        if sym in buy_syms:
                            rv = realized_vols.get(sym)
                            if rv is not None and date in rv.index and not pd.isna(rv.loc[date]):
                                current_vol = rv.loc[date]
                                if current_vol > 0:
                                    scale = vol_target / current_vol
                                    scale = min(scale, 1.5)
                                    scale = max(scale, 0.1)
                                else:
                                    scale = 1.0
                            else:
                                scale = 1.0
                            target_weights[sym] = min(base_weight * scale, 0.40)
                        else:
                            target_weights[sym] = 0.0

                    total_w = sum(target_weights.values())
                    if total_w > 1.0:
                        for sym in target_weights:
                            target_weights[sym] /= total_w
                else:
                    target_weights = {sym: 0.0 for sym in symbols}

            # Position change threshold 체크
            execute = False
            for sym in symbols:
                old_w = current_weights.get(sym, 0.0)
                new_w = target_weights.get(sym, 0.0)
                if abs(new_w - old_w) > position_threshold:
                    execute = True
                    break

            # BUY→CASH 또는 CASH→BUY 전환은 항상 실행
            for sym in symbols:
                old_w = current_weights.get(sym, 0.0)
                new_w = target_weights.get(sym, 0.0)
                if (old_w == 0 and new_w > 0) or (old_w > 0 and new_w == 0):
                    execute = True
                    break

            if execute:
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

                current_weights = dict(target_weights)

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

    return {
        "kpis": kpis,
        "strategy_returns": strategy_returns,
        "btc_returns": btc_returns,
        "daily_values": values_df,
        "trade_count": trade_count,
    }


# ─────────────────────────────────────────────
# 실험 9: Position Threshold
# ─────────────────────────────────────────────

def experiment_9_position_threshold(closes: pd.DataFrame):
    """Position change threshold로 거래 빈도 감소 효과 측정."""
    print("\n" + "=" * 90)
    print("실험 9: POSITION THRESHOLD (거래 빈도 감소 vs Sharpe 유지)")
    print("=" * 90)

    symbols = SYMBOLS
    thresholds = [0.0, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.30]

    # Baseline: threshold 0 (모든 변경 실행)
    baseline = simulate_vol_threshold(closes, symbols, position_threshold=0.0)
    bk = baseline["kpis"]
    print(f"\nBaseline (no threshold): Sharpe={bk['sharpe']:.3f}, Trades={bk['num_trades']}, "
          f"FeeAdj={bk['fee_adj_return']:.2%}, MaxDD={bk['max_dd']:.2%}")

    print(f"\n{'Threshold':>10} {'Sharpe':>8} {'Calmar':>8} {'Sortino':>8} {'MaxDD':>8} "
          f"{'Return':>8} {'FeeAdj':>8} {'Trades':>7} {'Reduction':>10} {'Sharpe%':>8}")
    print("-" * 100)

    results = []
    for thr in thresholds:
        r = simulate_vol_threshold(closes, symbols, position_threshold=thr)
        k = r["kpis"]
        reduction = (1 - k["num_trades"] / bk["num_trades"]) * 100 if bk["num_trades"] > 0 else 0
        sharpe_retention = k["sharpe"] / bk["sharpe"] * 100 if bk["sharpe"] != 0 else 0

        print(f"{thr:>9.0%} {k['sharpe']:>8.3f} {k['calmar']:>8.3f} {k['sortino']:>8.3f} "
              f"{k['max_dd']:>8.2%} {k['total_return']:>8.2%} {k['fee_adj_return']:>8.2%} "
              f"{k['num_trades']:>7} {reduction:>9.0f}% {sharpe_retention:>7.0f}%")

        results.append({"threshold": thr, "result": r, "reduction": reduction, "retention": sharpe_retention})

    # Fee-adjusted return이 양수인 최소 threshold
    positive_fee = [r for r in results if r["result"]["kpis"]["fee_adj_return"] >= 0]
    if positive_fee:
        best_fee = min(positive_fee, key=lambda r: r["threshold"])
        bfk = best_fee["result"]["kpis"]
        print(f"\n★ Fee-Adj ≥ 0 달성하는 최소 threshold: {best_fee['threshold']:.0%}")
        print(f"  Sharpe={bfk['sharpe']:.3f}, Trades={bfk['num_trades']}, "
              f"FeeAdj={bfk['fee_adj_return']:.2%}, MaxDD={bfk['max_dd']:.2%}")
    else:
        print("\n✗ 어떤 threshold에서도 Fee-Adj ≥ 0 미달성")

    # 최적 트레이드오프 (Sharpe 70% 유지하면서 거래 최소화)
    viable = [r for r in results if r["retention"] >= 70]
    if viable:
        best_viable = max(viable, key=lambda r: r["result"]["kpis"]["fee_adj_return"])
        bvk = best_viable["result"]["kpis"]
        print(f"\n★ 최적 트레이드오프 (Sharpe 70%+ 유지, FeeAdj 최적):")
        print(f"  Threshold={best_viable['threshold']:.0%}, Sharpe={bvk['sharpe']:.3f} "
              f"({best_viable['retention']:.0f}% 유지), Trades={bvk['num_trades']}, "
              f"FeeAdj={bvk['fee_adj_return']:.2%}")

    # OOS 검증 — best threshold로
    if positive_fee:
        best_thr = best_fee["threshold"]
    elif viable:
        best_thr = best_viable["threshold"]
    else:
        best_thr = 0.10

    print(f"\n--- OOS 검증 (threshold={best_thr:.0%}) ---")
    train_closes = closes[closes.index <= "2024-12-31"]
    test_closes = closes[closes.index >= "2024-11-01"]

    r_train = simulate_vol_threshold(train_closes, symbols, position_threshold=best_thr)
    r_test = simulate_vol_threshold(test_closes, symbols, position_threshold=best_thr)

    tk = r_train["kpis"]
    sk = r_test["kpis"]
    gap = abs(tk["sharpe"] - sk["sharpe"])

    print(f"  Train: Sharpe={tk['sharpe']:.3f}, Trades={tk['num_trades']}, FeeAdj={tk['fee_adj_return']:.2%}")
    print(f"  Test:  Sharpe={sk['sharpe']:.3f}, Trades={sk['num_trades']}, FeeAdj={sk['fee_adj_return']:.2%}")
    print(f"  Gap:   {gap:.3f}")

    # Stage 1.5→2 승격 기준 체크
    annual_trades = sk["num_trades"] * (365 / len(test_closes))
    print(f"\n--- Stage 1.5→2 승격 기준 체크 ---")
    checks = {
        "FeeAdj ≥ 0 (in-sample)": tk["fee_adj_return"] >= 0,
        "OOS FeeAdj > -5%": sk["fee_adj_return"] > -0.05,
        "연간 거래 < 100": annual_trades < 100,
    }
    for check, passed in checks.items():
        icon = "✓" if passed else "✗"
        print(f"  {icon} {check}")

    # Registry
    record_experiment(
        hypothesis=f"Position threshold {best_thr:.0%}로 FeeAdj ≥ 0 달성 가능한가",
        params={"formation": 14, "vol_lookback": 40, "vol_target": 0.10,
                "confirm_days": 5, "position_threshold": best_thr},
        results={**(bfk if positive_fee else (bvk if viable else bk)),
                 "oos_sharpe": sk["sharpe"], "oos_fee_adj": sk["fee_adj_return"]},
        status="PASSED" if (positive_fee and sk["fee_adj_return"] > -0.05) else "FAILED",
        notes=(f"Threshold {best_thr:.0%}: IS Sharpe={tk['sharpe']:.3f} FeeAdj={tk['fee_adj_return']:.2%} "
               f"Trades={tk['num_trades']} | OOS Sharpe={sk['sharpe']:.3f} FeeAdj={sk['fee_adj_return']:.2%} "
               f"Trades={sk['num_trades']} | Gap={gap:.3f}"),
    )

    return results


# ─────────────────────────────────────────────
# 실험 12: Parameter Stability
# ─────────────────────────────────────────────

def experiment_12_parameter_stability(closes: pd.DataFrame):
    """±20% 내 이웃 파라미터들의 Sharpe plateau 존재 여부 확인."""
    print("\n" + "=" * 90)
    print("실험 12: PARAMETER STABILITY (Plateau 확인)")
    print("=" * 90)

    symbols = SYMBOLS

    # 최적 threshold 사용 (실험 9 결과에서 가져올 수도 있지만 0.10 사용)
    best_threshold = 0.10

    # 중심 파라미터
    center = {"formation": 14, "vol_lookback": 40, "vol_target": 0.10}
    center_result = simulate_vol_threshold(
        closes, symbols, **center, position_threshold=best_threshold
    )
    center_sharpe = center_result["kpis"]["sharpe"]
    print(f"\nCenter: F={center['formation']}, VolLB={center['vol_lookback']}, "
          f"VolTgt={center['vol_target']:.0%} → Sharpe={center_sharpe:.3f}")
    print(f"Retention threshold: {center_sharpe * 0.70:.3f} (70%)")

    # 1. Formation Period sweep
    print(f"\n--- Formation Period Sweep (VolLB=40, VolTgt=10%) ---")
    formations = [10, 11, 12, 13, 14, 15, 16, 17, 18]
    header = f"{'Formation':>10} {'Sharpe':>8} {'Calmar':>8} {'MaxDD':>8} {'FeeAdj':>8} {'Trades':>7} {'Retention':>10}"
    print(header)
    print("-" * len(header))

    formation_sharpes = []
    for f in formations:
        r = simulate_vol_threshold(
            closes, symbols, formation=f, vol_lookback=40, vol_target=0.10,
            position_threshold=best_threshold,
        )
        k = r["kpis"]
        ret = k["sharpe"] / center_sharpe * 100 if center_sharpe != 0 else 0
        marker = " ←" if f == center["formation"] else ""
        print(f"{f:>10} {k['sharpe']:>8.3f} {k['calmar']:>8.3f} {k['max_dd']:>8.2%} "
              f"{k['fee_adj_return']:>8.2%} {k['num_trades']:>7} {ret:>9.0f}%{marker}")
        formation_sharpes.append({"param": f, "sharpe": k["sharpe"], "retention": ret})

    f_plateau = all(
        s["retention"] >= 70
        for s in formation_sharpes
        if abs(s["param"] - center["formation"]) <= center["formation"] * 0.20
    )

    # 2. Vol Lookback sweep
    print(f"\n--- Vol Lookback Sweep (F=14, VolTgt=10%) ---")
    lookbacks = [28, 30, 32, 35, 38, 40, 42, 45, 48, 50, 55]
    header = f"{'VolLookback':>12} {'Sharpe':>8} {'Calmar':>8} {'MaxDD':>8} {'FeeAdj':>8} {'Trades':>7} {'Retention':>10}"
    print(header)
    print("-" * len(header))

    lookback_sharpes = []
    for vlb in lookbacks:
        r = simulate_vol_threshold(
            closes, symbols, formation=14, vol_lookback=vlb, vol_target=0.10,
            position_threshold=best_threshold,
        )
        k = r["kpis"]
        ret = k["sharpe"] / center_sharpe * 100 if center_sharpe != 0 else 0
        marker = " ←" if vlb == center["vol_lookback"] else ""
        print(f"{vlb:>12} {k['sharpe']:>8.3f} {k['calmar']:>8.3f} {k['max_dd']:>8.2%} "
              f"{k['fee_adj_return']:>8.2%} {k['num_trades']:>7} {ret:>9.0f}%{marker}")
        lookback_sharpes.append({"param": vlb, "sharpe": k["sharpe"], "retention": ret})

    vlb_plateau = all(
        s["retention"] >= 70
        for s in lookback_sharpes
        if abs(s["param"] - center["vol_lookback"]) <= center["vol_lookback"] * 0.20
    )

    # 3. Vol Target sweep
    print(f"\n--- Vol Target Sweep (F=14, VolLB=40) ---")
    vol_targets = [0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12, 0.13, 0.14, 0.15]
    header = f"{'VolTarget':>10} {'Sharpe':>8} {'Calmar':>8} {'MaxDD':>8} {'FeeAdj':>8} {'Trades':>7} {'Retention':>10}"
    print(header)
    print("-" * len(header))

    vt_sharpes = []
    for vt in vol_targets:
        r = simulate_vol_threshold(
            closes, symbols, formation=14, vol_lookback=40, vol_target=vt,
            position_threshold=best_threshold,
        )
        k = r["kpis"]
        ret = k["sharpe"] / center_sharpe * 100 if center_sharpe != 0 else 0
        marker = " ←" if vt == center["vol_target"] else ""
        print(f"{vt:>9.0%} {k['sharpe']:>8.3f} {k['calmar']:>8.3f} {k['max_dd']:>8.2%} "
              f"{k['fee_adj_return']:>8.2%} {k['num_trades']:>7} {ret:>9.0f}%{marker}")
        vt_sharpes.append({"param": vt, "sharpe": k["sharpe"], "retention": ret})

    vt_plateau = all(
        s["retention"] >= 70
        for s in vt_sharpes
        if abs(s["param"] - center["vol_target"]) <= center["vol_target"] * 0.20
    )

    # 종합 판정
    print(f"\n--- PLATEAU 판정 ---")
    print(f"  Formation (±20% = {int(center['formation']*0.8)}~{int(center['formation']*1.2)}): "
          f"{'✓ PLATEAU' if f_plateau else '✗ NO PLATEAU'}")
    print(f"  Vol Lookback (±20% = {int(center['vol_lookback']*0.8)}~{int(center['vol_lookback']*1.2)}): "
          f"{'✓ PLATEAU' if vlb_plateau else '✗ NO PLATEAU'}")
    print(f"  Vol Target (±20% = {center['vol_target']*0.8:.0%}~{center['vol_target']*1.2:.0%}): "
          f"{'✓ PLATEAU' if vt_plateau else '✗ NO PLATEAU'}")

    all_plateau = f_plateau and vlb_plateau and vt_plateau
    print(f"\n  ★ 전체 Stability: {'✓ ROBUST — 모든 파라미터 plateau 존재' if all_plateau else '⚠️ PARTIAL — 일부 파라미터 불안정'}")

    # OOS stability 체크 — 중심 ±1단계 이웃으로 OOS 비교
    print(f"\n--- OOS Parameter Stability ---")
    test_closes = closes[closes.index >= "2024-11-01"]
    oos_params = [
        (12, 35, 0.08),
        (14, 40, 0.10),
        (16, 45, 0.12),
    ]
    header = f"{'F':>4} {'VLB':>5} {'VT':>5} {'IS Sharpe':>10} {'OOS Sharpe':>11} {'Gap':>6}"
    print(header)
    print("-" * len(header))
    for f, vlb, vt in oos_params:
        r_is = simulate_vol_threshold(closes, symbols, formation=f, vol_lookback=vlb, vol_target=vt,
                                      position_threshold=best_threshold)
        r_oos = simulate_vol_threshold(test_closes, symbols, formation=f, vol_lookback=vlb, vol_target=vt,
                                       position_threshold=best_threshold)
        gap = abs(r_is["kpis"]["sharpe"] - r_oos["kpis"]["sharpe"])
        print(f"{f:>4} {vlb:>5} {vt:>4.0%} {r_is['kpis']['sharpe']:>10.3f} "
              f"{r_oos['kpis']['sharpe']:>11.3f} {gap:>6.3f}")

    # Registry
    record_experiment(
        hypothesis="최적 파라미터 ±20% 내에서 Sharpe 70% 이상 유지되는 plateau가 존재하는가",
        params={**center, "position_threshold": best_threshold, "test": "plateau"},
        results=center_result["kpis"],
        status="PASSED" if all_plateau else "FAILED",
        notes=(f"Formation plateau: {'Yes' if f_plateau else 'No'}, "
               f"VolLB plateau: {'Yes' if vlb_plateau else 'No'}, "
               f"VolTarget plateau: {'Yes' if vt_plateau else 'No'}. "
               f"Overall: {'ROBUST' if all_plateau else 'PARTIAL'}"),
    )


if __name__ == "__main__":
    init_db()

    df = load_bars(SYMBOLS)
    closes = df.pivot_table(index="date", columns="symbol", values="close")
    print(f"Data: {closes.index.min().date()} ~ {closes.index.max().date()} ({len(closes)} days)")

    experiment_9_position_threshold(closes)
    experiment_12_parameter_stability(closes)

    # 최종 Registry 출력
    from core.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, hypothesis, status, notes FROM experiments
           WHERE id > 25 ORDER BY id"""
    ).fetchall()
    conn.close()

    print("\n" + "=" * 90)
    print("NEW EXPERIMENT RESULTS")
    print("=" * 90)
    for row in rows:
        icon = {"PASSED": "✓", "FAILED": "✗"}.get(row[2], "?")
        print(f"\n{icon} Experiment #{row[0]}: {row[1]}")
        print(f"  Status: {row[2]}")
        if row[3]:
            print(f"  Notes: {row[3]}")
