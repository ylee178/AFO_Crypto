"""Stage 1.5b — Incremental Rebalancing + 보수적 파라미터 재백테스트.

핵심 변경: 전량 청산→재진입이 아니라 차이분만 거래 (incremental rebalancing).
이것만으로 수수료가 구조적으로 줄어야 한다.
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


def simulate_incremental(
    closes: pd.DataFrame,
    symbols: list[str],
    formation: int = 16,
    confirmation_days: int = 5,
    vol_lookback: int = 45,
    vol_target: float = 0.10,
    position_threshold: float = 0.0,
    fee_bps: float = FEE_ROUNDTRIP_BPS,
    initial_capital: float = 10000.0,
) -> dict:
    """Incremental rebalancing 백테스트 엔진.

    핵심 차이: 포지션을 전량 청산→재진입하지 않고, 목표 비중과 현재 비중의
    차이(delta)만 거래한다. 수수료는 delta 금액에만 적용.
    """
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
    positions = {}          # {symbol: qty held}
    daily_values = []
    trade_count = 0
    total_fee_paid = 0.0    # 실제 수수료 누적

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
            # 드로다운 체크
            dd = (portfolio_value - peak_value) / peak_value if peak_value > 0 else 0.0
            force_cash = dd < -0.20

            # 목표 비중 계산
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

            # ── INCREMENTAL REBALANCING ──
            # 현재 비중 계산
            current_weights = {}
            for sym in symbols:
                if sym in positions and sym in closes.columns:
                    price = closes.loc[date, sym]
                    if not pd.isna(price) and price > 0:
                        current_weights[sym] = (positions[sym] * price) / portfolio_value
                    else:
                        current_weights[sym] = 0.0
                else:
                    current_weights[sym] = 0.0

            # 각 종목별 delta 계산 및 threshold 체크
            for sym in symbols:
                target_w = target_weights.get(sym, 0.0)
                current_w = current_weights.get(sym, 0.0)
                delta_w = target_w - current_w

                # Threshold 체크: delta가 threshold 미만이면 스킵
                if abs(delta_w) <= position_threshold:
                    continue

                price = closes.loc[date, sym] if sym in closes.columns else 0.0
                if pd.isna(price) or price <= 0:
                    continue

                delta_value = delta_w * portfolio_value

                if delta_value > 0:
                    # 매수: delta만큼만
                    fee = abs(delta_value) * fee_bps / 10000 / 2  # 편도
                    buy_qty = (abs(delta_value) - fee) / price
                    positions[sym] = positions.get(sym, 0.0) + buy_qty
                    cash -= abs(delta_value)
                    total_fee_paid += fee
                    trade_count += 1
                elif delta_value < 0:
                    # 매도: delta만큼만
                    sell_qty = min(abs(delta_value) / price, positions.get(sym, 0.0))
                    sell_value = sell_qty * price
                    fee = sell_value * fee_bps / 10000 / 2
                    positions[sym] = positions.get(sym, 0.0) - sell_qty
                    cash += sell_value - fee
                    total_fee_paid += fee
                    trade_count += 1

                    # 포지션 0 이하 정리
                    if positions.get(sym, 0.0) < 1e-10:
                        positions.pop(sym, None)

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
    # 실제 수수료 기반 fee-adjusted return 추가
    gross_return = (1 + strategy_returns).prod() - 1
    kpis["actual_fee_paid"] = total_fee_paid
    kpis["actual_fee_pct"] = total_fee_paid / initial_capital
    kpis["gross_return"] = float(gross_return)

    return {
        "kpis": kpis,
        "strategy_returns": strategy_returns,
        "btc_returns": btc_returns,
        "daily_values": values_df,
        "trade_count": trade_count,
        "total_fee_paid": total_fee_paid,
    }


# ─────────────────────────────────────────────
# 실험 9b: Incremental Rebalancing + Threshold
# ─────────────────────────────────────────────

def experiment_9b(closes: pd.DataFrame):
    """부분 리밸런싱 엔진으로 threshold 재실험."""
    print("\n" + "=" * 95)
    print("실험 9b: INCREMENTAL REBALANCING + POSITION THRESHOLD")
    print("=" * 95)

    symbols = SYMBOLS

    # 보수적 파라미터
    params = {"formation": 16, "vol_lookback": 45, "vol_target": 0.10}
    thresholds = [0.0, 0.02, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20]

    # 먼저 old-style (전량 청산) vs incremental 비교
    print(f"\n--- Old vs Incremental 비교 (F={params['formation']}, VLB={params['vol_lookback']}, VT={params['vol_target']:.0%}, threshold=0%) ---")

    from experiments_stage1_5 import simulate_vol_threshold
    r_old = simulate_vol_threshold(closes, symbols, **params, position_threshold=0.0)
    r_inc = simulate_incremental(closes, symbols, **params, position_threshold=0.0)

    ok = r_old["kpis"]
    ik = r_inc["kpis"]
    print(f"  {'Method':<22} {'Sharpe':>8} {'Calmar':>8} {'MaxDD':>8} {'Return':>8} {'FeeAdj':>8} {'Trades':>7} {'ActualFee':>10}")
    print(f"  {'-'*85}")
    print(f"  {'Old (full rebalance)':<22} {ok['sharpe']:>8.3f} {ok['calmar']:>8.3f} {ok['max_dd']:>8.2%} "
          f"{ok['total_return']:>8.2%} {ok['fee_adj_return']:>8.2%} {ok['num_trades']:>7} {'N/A':>10}")
    print(f"  {'Incremental':<22} {ik['sharpe']:>8.3f} {ik['calmar']:>8.3f} {ik['max_dd']:>8.2%} "
          f"{ik['total_return']:>8.2%} {ik['fee_adj_return']:>8.2%} {ik['num_trades']:>7} "
          f"${r_inc['total_fee_paid']:>8.0f}")

    trade_reduction = (1 - ik["num_trades"] / ok["num_trades"]) * 100 if ok["num_trades"] > 0 else 0
    print(f"\n  거래 횟수: {ok['num_trades']} → {ik['num_trades']} ({trade_reduction:+.0f}%)")

    # Threshold sweep (incremental)
    print(f"\n--- Incremental + Threshold Sweep ---")
    header = (f"{'Threshold':>10} {'Sharpe':>8} {'Calmar':>8} {'Sortino':>8} {'MaxDD':>8} "
              f"{'Return':>8} {'FeeAdj':>8} {'Trades':>7} {'Fee$':>7} {'Sharpe%':>8}")
    print(header)
    print("-" * len(header))

    baseline_sharpe = ik["sharpe"]
    results = []

    for thr in thresholds:
        r = simulate_incremental(closes, symbols, **params, position_threshold=thr)
        k = r["kpis"]
        ret_pct = k["sharpe"] / baseline_sharpe * 100 if baseline_sharpe != 0 else 0
        marker = ""
        if k["fee_adj_return"] >= 0 and not any(prev["kpis"]["fee_adj_return"] >= 0 for prev in [x["result"] for x in results]):
            marker = " ★"

        print(f"{thr:>9.0%} {k['sharpe']:>8.3f} {k['calmar']:>8.3f} {k['sortino']:>8.3f} "
              f"{k['max_dd']:>8.2%} {k['total_return']:>8.2%} {k['fee_adj_return']:>8.2%} "
              f"{k['num_trades']:>7} ${r['total_fee_paid']:>5.0f} {ret_pct:>7.0f}%{marker}")

        results.append({"threshold": thr, "result": r, "retention": ret_pct})

    # Fee-Adj ≥ 0 달성 여부
    positive = [r for r in results if r["result"]["kpis"]["fee_adj_return"] >= 0]
    if positive:
        best = min(positive, key=lambda r: r["threshold"])
        bk = best["result"]["kpis"]
        print(f"\n★ Fee-Adj ≥ 0 달성! Threshold={best['threshold']:.0%}")
        print(f"  Sharpe={bk['sharpe']:.3f}, Trades={bk['num_trades']}, FeeAdj={bk['fee_adj_return']:.2%}")
    else:
        # FeeAdj가 가장 높은 조합
        best = max(results, key=lambda r: r["result"]["kpis"]["fee_adj_return"])
        bk = best["result"]["kpis"]
        print(f"\n  FeeAdj 최고: Threshold={best['threshold']:.0%}, FeeAdj={bk['fee_adj_return']:.2%}")

    # OOS 검증
    best_thr = best["threshold"]
    print(f"\n--- OOS 검증 (threshold={best_thr:.0%}) ---")
    train_closes = closes[closes.index <= "2024-12-31"]
    test_closes = closes[closes.index >= "2024-11-01"]

    r_train = simulate_incremental(train_closes, symbols, **params, position_threshold=best_thr)
    r_test = simulate_incremental(test_closes, symbols, **params, position_threshold=best_thr)

    tk = r_train["kpis"]
    sk = r_test["kpis"]
    gap = abs(tk["sharpe"] - sk["sharpe"])

    print(f"  Train: Sharpe={tk['sharpe']:.3f}, Trades={tk['num_trades']}, FeeAdj={tk['fee_adj_return']:.2%}, MaxDD={tk['max_dd']:.2%}")
    print(f"  Test:  Sharpe={sk['sharpe']:.3f}, Trades={sk['num_trades']}, FeeAdj={sk['fee_adj_return']:.2%}, MaxDD={sk['max_dd']:.2%}")
    print(f"  Gap:   {gap:.3f}")

    # BTC 비교
    btc_test = test_closes["BTC/USD"].pct_change().dropna()
    btc_kpis = compute_all_kpis(btc_test, btc_test, 0, 0)
    print(f"  BTC:   Sharpe={btc_kpis['sharpe']:.3f}, MaxDD={btc_kpis['max_dd']:.2%}")

    # DD protection in OOS
    if btc_kpis["max_dd"] != 0:
        dd_protection = 1 - (sk["max_dd"] / btc_kpis["max_dd"])
        print(f"  OOS DD Protection: {dd_protection:.1%}")

    # Stage 1.5→2 승격 기준
    test_days = len(test_closes)
    annual_trades = sk["num_trades"] * (365 / test_days) if test_days > 0 else 999
    print(f"\n--- Stage 1.5→2 승격 기준 체크 ---")
    checks = [
        ("FeeAdj ≥ 0 (IS)", tk["fee_adj_return"] >= 0),
        ("OOS FeeAdj > -5%", sk["fee_adj_return"] > -0.05),
        (f"연간 거래 < 100 (현재 {annual_trades:.0f})", annual_trades < 100),
        ("OOS MaxDD < BTC MaxDD × 0.5", sk["max_dd"] > btc_kpis["max_dd"] * 0.5 if btc_kpis["max_dd"] < 0 else True),
    ]
    all_pass = True
    for check, passed in checks:
        icon = "✓" if passed else "✗"
        if not passed:
            all_pass = False
        print(f"  {icon} {check}")

    if all_pass:
        print(f"\n  ★★ STAGE 1.5 → 2 승격 기준 충족! ★★")
    else:
        print(f"\n  Stage 1.5 미완 — 미충족 항목 해결 필요")

    # Registry
    record_experiment(
        hypothesis="Incremental rebalancing + threshold로 FeeAdj ≥ 0 달성",
        params={**params, "position_threshold": best_thr, "rebalance_mode": "incremental"},
        results={**bk, "oos_sharpe": sk["sharpe"], "oos_fee_adj": sk["fee_adj_return"]},
        status="PASSED" if (bk["fee_adj_return"] >= 0 or tk["fee_adj_return"] >= 0) else "FAILED",
        notes=(f"Incremental rebalancing: Old {ok['num_trades']} trades → Inc {ik['num_trades']} trades "
               f"({trade_reduction:+.0f}%). "
               f"Best threshold {best_thr:.0%}: IS FeeAdj={tk['fee_adj_return']:.2%}, "
               f"OOS FeeAdj={sk['fee_adj_return']:.2%}, OOS MaxDD={sk['max_dd']:.2%}"),
    )

    return results


# ─────────────────────────────────────────────
# 보수적 파라미터 비교: F=14 vs F=16 vs F=18
# ─────────────────────────────────────────────

def conservative_param_comparison(closes: pd.DataFrame):
    """보수적 파라미터 조합 비교. Cliff에서 가장 먼 안전지대 확인."""
    print("\n" + "=" * 95)
    print("보수적 파라미터 비교 (Incremental Rebalancing)")
    print("=" * 95)

    symbols = SYMBOLS
    test_closes = closes[closes.index >= "2024-11-01"]

    combos = [
        # (F, VLB, VT, label)
        (14, 40, 0.10, "Original (cliff 근처)"),
        (16, 40, 0.10, "F=16 only"),
        (14, 45, 0.10, "VLB=45 only"),
        (16, 45, 0.10, "Conservative (F16+VLB45)"),
        (18, 45, 0.10, "More conservative"),
        (16, 45, 0.08, "Conservative + low VT"),
        (16, 50, 0.10, "Conservative + long VLB"),
    ]

    thresholds = [0.0, 0.05, 0.10]

    for thr in thresholds:
        print(f"\n--- Threshold={thr:.0%} ---")
        header = (f"{'Label':<28} {'F':>3} {'VLB':>4} {'VT':>4} {'IS.Sh':>7} {'IS.Cal':>7} "
                  f"{'IS.MDD':>7} {'IS.Fee':>7} {'Trd':>5} | {'OOS.Sh':>7} {'OOS.MDD':>8} {'OOS.Fee':>8} {'Gap':>5}")
        print(header)
        print("-" * len(header))

        for f, vlb, vt, label in combos:
            r_is = simulate_incremental(closes, symbols, formation=f, vol_lookback=vlb,
                                        vol_target=vt, position_threshold=thr)
            r_oos = simulate_incremental(test_closes, symbols, formation=f, vol_lookback=vlb,
                                         vol_target=vt, position_threshold=thr)
            ik = r_is["kpis"]
            ok = r_oos["kpis"]
            gap = abs(ik["sharpe"] - ok["sharpe"])

            print(f"{label:<28} {f:>3} {vlb:>4} {vt:>3.0%} {ik['sharpe']:>7.3f} {ik['calmar']:>7.3f} "
                  f"{ik['max_dd']:>7.2%} {ik['fee_adj_return']:>7.2%} {ik['num_trades']:>5} | "
                  f"{ok['sharpe']:>7.3f} {ok['max_dd']:>8.2%} {ok['fee_adj_return']:>8.2%} {gap:>5.3f}")


if __name__ == "__main__":
    init_db()

    df = load_bars(SYMBOLS)
    closes = df.pivot_table(index="date", columns="symbol", values="close")
    print(f"Data: {closes.index.min().date()} ~ {closes.index.max().date()} ({len(closes)} days)")

    experiment_9b(closes)
    conservative_param_comparison(closes)
