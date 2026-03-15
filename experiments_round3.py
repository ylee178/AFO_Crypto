"""실험 7 & 8 — Signal Decomposition + Out-of-Sample.

실험 7: Factor Attribution (Momentum vs Vol Timing vs Combined vs Random)
실험 8: Out-of-Sample Validation (Train 2021-2024, Test 2025)
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
# 공통 시뮬레이션 엔진 (4가지 전략 모드)
# ─────────────────────────────────────────────

def simulate_decomposed(
    closes: pd.DataFrame,
    symbols: list[str],
    mode: str,  # "momentum_only" | "vol_only" | "combined" | "random_vol"
    formation: int = 14,
    confirmation_days: int = 5,
    vol_lookback: int = 40,
    vol_target: float = 0.10,
    fee_bps: float = FEE_ROUNDTRIP_BPS,
    initial_capital: float = 10000.0,
    random_seed: int | None = None,
) -> dict:
    """4가지 전략 모드를 동일 엔진으로 시뮬레이션.

    A) momentum_only:  signal=momentum, position={0,1}, no vol scaling
    B) vol_only:       always long, position=vol_target/realized_vol
    C) combined:       signal=momentum, position=signal*(vol_target/realized_vol)
    D) random_vol:     signal=random, position=signal*(vol_target/realized_vol)
    """
    # 모멘텀 시그널 (A, C에서 사용)
    signals_all = compute_backtest(closes, symbols, formation)
    min_start = max(df["date"].iloc[0] for df in signals_all.values())
    all_dates = sorted(closes.index[closes.index >= min_start])

    # 실현 변동성 사전 계산
    realized_vols = {}
    for sym in symbols:
        if sym in closes.columns:
            rets = closes[sym].pct_change()
            realized_vols[sym] = rets.rolling(vol_lookback).std() * np.sqrt(365)

    # 랜덤 시그널 사전 생성 (D에서 사용)
    rng = np.random.RandomState(random_seed if random_seed is not None else 42)
    random_signals = {}
    for sym in symbols:
        random_signals[sym] = rng.choice(["BUY", "CASH"], size=len(all_dates))

    portfolio_value = initial_capital
    peak_value = initial_capital
    cash = initial_capital
    positions = {}
    daily_values = []
    trade_count = 0

    prev_signals = {}
    flip_streak = {}

    for i, date in enumerate(all_dates):
        # 포트폴리오 가치
        pos_value = sum(
            qty * closes.loc[date, sym]
            for sym, qty in positions.items()
            if sym in closes.columns and not pd.isna(closes.loc[date, sym])
        )
        portfolio_value = cash + pos_value
        peak_value = max(peak_value, portfolio_value)

        # 시그널 결정 (모드별)
        day_signals = {}
        for sym in symbols:
            if mode == "vol_only":
                # B: 항상 롱
                day_signals[sym] = "BUY"
            elif mode == "random_vol":
                # D: 랜덤 시그널
                day_signals[sym] = random_signals[sym][i]
            else:
                # A, C: 모멘텀 시그널
                if sym in signals_all:
                    sym_df = signals_all[sym]
                    row = sym_df[sym_df["date"] == date]
                    if len(row) > 0:
                        day_signals[sym] = row.iloc[0]["signal"]

        if not day_signals:
            daily_values.append({"date": date, "value": portfolio_value})
            continue

        # 리밸런싱 판단 (confirmation 방식)
        should_rebalance = False

        if mode == "vol_only":
            # B: vol 변화에 따라 매일 포지션 조정이 이상적이지만,
            # 공정한 비교를 위해 confirmation과 동일한 빈도 사용
            if not prev_signals:
                should_rebalance = True
            else:
                # vol이 유의미하게 변하면 리밸런싱 (20% 이상 변화)
                for sym in symbols:
                    rv = realized_vols.get(sym)
                    if rv is not None and date in rv.index:
                        current_vol = rv.loc[date]
                        # 이전 리밸런싱 시점의 vol과 비교
                        prev_vol = getattr(simulate_decomposed, f'_prev_vol_{sym}', current_vol)
                        if abs(current_vol - prev_vol) / max(prev_vol, 0.01) > 0.20:
                            should_rebalance = True
                            break
                # 최소 confirmation_days 간격
                if not hasattr(simulate_decomposed, '_last_rebal'):
                    simulate_decomposed._last_rebal = -confirmation_days
                if i - simulate_decomposed._last_rebal < confirmation_days:
                    should_rebalance = False

        elif mode in ("momentum_only", "combined", "random_vol"):
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

            if force_cash and mode != "vol_only":
                target_weights = {sym: 0.0 for sym in symbols}
            else:
                target_weights = {}

                if mode == "momentum_only":
                    # A: 모멘텀만, equal weight
                    buy_syms = [sym for sym, sig in day_signals.items() if sig == "BUY"]
                    if buy_syms:
                        w = min(1.0 / len(buy_syms), 0.40)
                        for sym in symbols:
                            target_weights[sym] = w if sym in buy_syms else 0.0
                    else:
                        target_weights = {sym: 0.0 for sym in symbols}

                elif mode == "vol_only":
                    # B: 항상 롱, vol scaling으로 포지션 크기만 조정
                    base_weight = 1.0 / len(symbols)
                    for sym in symbols:
                        rv = realized_vols.get(sym)
                        if rv is not None and date in rv.index and not pd.isna(rv.loc[date]):
                            current_vol = rv.loc[date]
                            if current_vol > 0:
                                scale = vol_target / current_vol
                                scale = min(scale, 1.5)
                                scale = max(scale, 0.1)
                            else:
                                scale = 1.0
                            # 이전 vol 저장
                            setattr(simulate_decomposed, f'_prev_vol_{sym}', current_vol)
                        else:
                            scale = 1.0
                        target_weights[sym] = min(base_weight * scale, 0.40)

                    total_w = sum(target_weights.values())
                    if total_w > 1.0:
                        for sym in target_weights:
                            target_weights[sym] /= total_w

                elif mode in ("combined", "random_vol"):
                    # C, D: 시그널 + vol scaling
                    buy_syms = [sym for sym, sig in day_signals.items() if sig == "BUY"]
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
            if mode == "vol_only":
                simulate_decomposed._last_rebal = i

        # 가치 재계산
        pos_value = sum(
            qty * closes.loc[date, sym]
            for sym, qty in positions.items()
            if sym in closes.columns and not pd.isna(closes.loc[date, sym])
        )
        portfolio_value = cash + pos_value
        peak_value = max(peak_value, portfolio_value)
        daily_values.append({"date": date, "value": portfolio_value})

    # 정적 변수 정리
    for sym in symbols:
        if hasattr(simulate_decomposed, f'_prev_vol_{sym}'):
            delattr(simulate_decomposed, f'_prev_vol_{sym}')
    if hasattr(simulate_decomposed, '_last_rebal'):
        delattr(simulate_decomposed, '_last_rebal')

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
# 실험 7: Signal Decomposition
# ─────────────────────────────────────────────

def experiment_7_signal_decomposition(closes: pd.DataFrame):
    """Factor Attribution: Momentum vs Vol Timing vs Combined vs Random."""
    print("\n" + "=" * 90)
    print("실험 7: SIGNAL DECOMPOSITION (Factor Attribution)")
    print("=" * 90)

    symbols = SYMBOLS
    params = dict(formation=14, vol_lookback=40, vol_target=0.10, confirmation_days=5)

    strategies = [
        ("A) Momentum Only",   "momentum_only"),
        ("B) Vol Timing Only", "vol_only"),
        ("C) Mom + Vol (Best)","combined"),
    ]

    # D) Random: 여러 시드로 평균
    random_seeds = list(range(42, 52))  # 10개 시드

    print(f"\nParams: F={params['formation']}d, VolLB={params['vol_lookback']}d, "
          f"VolTgt={params['vol_target']:.0%}, Confirm={params['confirmation_days']}d")

    header = (f"{'Strategy':<25} {'Sharpe':>8} {'Calmar':>8} {'Sortino':>8} "
              f"{'MaxDD':>8} {'Return':>9} {'FeeAdj':>9} {'Trades':>7}")
    print(f"\n{header}")
    print("-" * len(header))

    results = {}
    for label, mode in strategies:
        r = simulate_decomposed(closes, symbols, mode, **params)
        k = r["kpis"]
        results[mode] = k
        print(f"{label:<25} {k['sharpe']:>8.3f} {k['calmar']:>8.3f} {k['sortino']:>8.3f} "
              f"{k['max_dd']:>8.2%} {k['total_return']:>9.2%} {k['fee_adj_return']:>9.2%} "
              f"{k['num_trades']:>7}")

    # D) Random signal — 10번 돌려서 평균/min/max
    random_sharpes = []
    random_kpis_list = []
    for seed in random_seeds:
        r = simulate_decomposed(closes, symbols, "random_vol", **params, random_seed=seed)
        random_sharpes.append(r["kpis"]["sharpe"])
        random_kpis_list.append(r["kpis"])

    avg_random = {
        "sharpe": np.mean([k["sharpe"] for k in random_kpis_list]),
        "calmar": np.mean([k["calmar"] for k in random_kpis_list]),
        "sortino": np.mean([k["sortino"] for k in random_kpis_list]),
        "max_dd": np.mean([k["max_dd"] for k in random_kpis_list]),
        "total_return": np.mean([k["total_return"] for k in random_kpis_list]),
        "fee_adj_return": np.mean([k["fee_adj_return"] for k in random_kpis_list]),
        "num_trades": np.mean([k["num_trades"] for k in random_kpis_list]),
    }

    print(f"{'D) Random+Vol (avg 10x)':<25} {avg_random['sharpe']:>8.3f} {avg_random['calmar']:>8.3f} "
          f"{avg_random['sortino']:>8.3f} {avg_random['max_dd']:>8.2%} {avg_random['total_return']:>9.2%} "
          f"{avg_random['fee_adj_return']:>9.2%} {avg_random['num_trades']:>7.0f}")
    print(f"   Random Sharpe range: [{min(random_sharpes):.3f}, {max(random_sharpes):.3f}]")

    # BTC B&H 기준
    btc_ret = closes["BTC/USD"].pct_change().dropna()
    btc_kpis = compute_all_kpis(btc_ret, btc_ret, 0, 0)
    print(f"{'BTC Buy & Hold':<25} {btc_kpis['sharpe']:>8.3f} {btc_kpis['calmar']:>8.3f} "
          f"{btc_kpis['sortino']:>8.3f} {btc_kpis['max_dd']:>8.2%} {btc_kpis['total_return']:>9.2%} "
          f"{'N/A':>9} {'0':>7}")

    # Attribution 분석
    mom_sharpe = results["momentum_only"]["sharpe"]
    vol_sharpe = results["vol_only"]["sharpe"]
    comb_sharpe = results["combined"]["sharpe"]
    rand_sharpe = avg_random["sharpe"]

    print("\n--- ATTRIBUTION 분석 ---")
    print(f"  Momentum 기여:       Sharpe {mom_sharpe:.3f}")
    print(f"  Vol Timing 기여:     Sharpe {vol_sharpe:.3f}")
    print(f"  Combined:            Sharpe {comb_sharpe:.3f}")
    print(f"  Random + Vol:        Sharpe {rand_sharpe:.3f}")
    print(f"  시너지 효과:         {comb_sharpe - max(mom_sharpe, vol_sharpe):+.3f}")
    print(f"  Momentum 순수 효과:  {comb_sharpe - vol_sharpe:+.3f} (Combined - Vol Only)")
    print(f"  Vol 순수 효과:       {comb_sharpe - mom_sharpe:+.3f} (Combined - Mom Only)")
    print(f"  Random vs Combined:  {rand_sharpe - comb_sharpe:+.3f} (음수면 momentum이 의미 있음)")

    # 결론 판정
    if vol_sharpe > mom_sharpe * 2 and rand_sharpe > mom_sharpe:
        conclusion = "ALPHA = VOLATILITY TIMING. Momentum은 보조적 역할."
        alpha_source = "vol_timing"
    elif mom_sharpe > vol_sharpe * 2:
        conclusion = "ALPHA = MOMENTUM. Vol scaling은 risk enhancer."
        alpha_source = "momentum"
    elif comb_sharpe > max(mom_sharpe, vol_sharpe) * 1.2:
        conclusion = "ALPHA = SYNERGY. 둘의 결합이 핵심."
        alpha_source = "synergy"
    else:
        conclusion = "ALPHA = MIXED. 추가 분석 필요."
        alpha_source = "mixed"

    if rand_sharpe >= comb_sharpe * 0.8:
        conclusion += " ⚠️ Random이 Combined와 비슷 — momentum signal이 약할 수 있음."

    print(f"\n  ★ 결론: {conclusion}")

    # Regime별 decomposition
    print("\n--- Regime별 Factor Attribution ---")
    regimes = [
        ("2022 하락장", "2021-11-01", "2022-12-31"),
        ("2023 회복장", "2022-11-01", "2023-12-31"),
        ("2024 상승장", "2023-11-01", "2024-12-31"),
        ("2025 횡보장", "2024-11-01", "2025-12-31"),
    ]

    header = f"{'Regime':<18} {'Mom':>8} {'Vol':>8} {'Comb':>8} {'Rand':>8} {'Winner':<12}"
    print(header)
    print("-" * len(header))

    for name, start, end in regimes:
        mask = (closes.index >= start) & (closes.index <= end)
        period_closes = closes[mask]
        if len(period_closes) < 50:
            continue

        r_mom = simulate_decomposed(period_closes, symbols, "momentum_only", **params)
        r_vol = simulate_decomposed(period_closes, symbols, "vol_only", **params)
        r_comb = simulate_decomposed(period_closes, symbols, "combined", **params)
        r_rand = simulate_decomposed(period_closes, symbols, "random_vol", **params, random_seed=42)

        sharpes = {
            "Mom": r_mom["kpis"]["sharpe"],
            "Vol": r_vol["kpis"]["sharpe"],
            "Comb": r_comb["kpis"]["sharpe"],
            "Rand": r_rand["kpis"]["sharpe"],
        }
        winner = max(sharpes, key=sharpes.get)

        print(f"{name:<18} {sharpes['Mom']:>8.3f} {sharpes['Vol']:>8.3f} "
              f"{sharpes['Comb']:>8.3f} {sharpes['Rand']:>8.3f} {winner:<12}")

    # Registry 기록
    record_experiment(
        hypothesis="전략 알파의 원천: Momentum vs Vol Timing vs Combined vs Random",
        params={**params, "mode": "decomposition"},
        results=results["combined"],
        status="PASSED",
        notes=(f"Mom Sharpe={mom_sharpe:.3f}, Vol Sharpe={vol_sharpe:.3f}, "
               f"Comb Sharpe={comb_sharpe:.3f}, Rand Sharpe={rand_sharpe:.3f}. "
               f"Alpha source: {alpha_source}. {conclusion}"),
    )

    return results, avg_random


# ─────────────────────────────────────────────
# 실험 8: Out-of-Sample Validation
# ─────────────────────────────────────────────

def experiment_8_out_of_sample(closes: pd.DataFrame):
    """Train/Test split으로 overfitting 검증."""
    print("\n" + "=" * 90)
    print("실험 8: OUT-OF-SAMPLE VALIDATION")
    print("=" * 90)

    symbols = SYMBOLS

    # Split: Train = ~2021-11 to 2024-12, Test = 2025-01 to end
    train_end = "2024-12-31"
    test_start = "2024-11-01"  # lookback 여유분 포함

    train_closes = closes[closes.index <= train_end]
    test_closes = closes[closes.index >= test_start]

    print(f"\n  Train: {train_closes.index.min().date()} ~ {train_closes.index.max().date()} ({len(train_closes)} days)")
    print(f"  Test:  {test_closes.index.min().date()} ~ {test_closes.index.max().date()} ({len(test_closes)} days)")

    # Train에서 최적 파라미터 찾기 (Grid Search)
    print("\n--- Train 기간 Grid Search ---")

    formations = [7, 14, 21, 28]
    vol_lookbacks = [20, 40, 60]
    vol_targets = [0.10, 0.15, 0.20]
    modes = ["momentum_only", "vol_only", "combined"]

    best_train = {"sharpe": -999}
    all_train_results = []

    for mode in modes:
        if mode == "momentum_only":
            for f in formations:
                r = simulate_decomposed(train_closes, symbols, mode, formation=f)
                k = r["kpis"]
                entry = {"mode": mode, "formation": f, "sharpe": k["sharpe"],
                         "max_dd": k["max_dd"], "return": k["total_return"]}
                all_train_results.append(entry)
                if k["sharpe"] > best_train["sharpe"]:
                    best_train = {**entry, "vol_lookback": 40, "vol_target": 0.10}

        elif mode == "vol_only":
            for vlb in vol_lookbacks:
                for vt in vol_targets:
                    r = simulate_decomposed(train_closes, symbols, mode, vol_lookback=vlb, vol_target=vt)
                    k = r["kpis"]
                    entry = {"mode": mode, "vol_lookback": vlb, "vol_target": vt,
                             "sharpe": k["sharpe"], "max_dd": k["max_dd"], "return": k["total_return"]}
                    all_train_results.append(entry)
                    if k["sharpe"] > best_train["sharpe"]:
                        best_train = {**entry, "formation": 14}

        elif mode == "combined":
            for f in formations:
                for vlb in vol_lookbacks:
                    for vt in vol_targets:
                        r = simulate_decomposed(train_closes, symbols, mode,
                                                formation=f, vol_lookback=vlb, vol_target=vt)
                        k = r["kpis"]
                        entry = {"mode": mode, "formation": f, "vol_lookback": vlb, "vol_target": vt,
                                 "sharpe": k["sharpe"], "max_dd": k["max_dd"], "return": k["total_return"]}
                        all_train_results.append(entry)
                        if k["sharpe"] > best_train["sharpe"]:
                            best_train = entry

    # Train 상위 5개 출력
    sorted_train = sorted(all_train_results, key=lambda x: x["sharpe"], reverse=True)
    print(f"\n  Top 5 Train Results:")
    header = f"  {'Mode':<18} {'Form':>6} {'VolLB':>6} {'VolTgt':>7} {'Sharpe':>8} {'MaxDD':>8} {'Return':>8}"
    print(header)
    for entry in sorted_train[:5]:
        print(f"  {entry['mode']:<18} {entry.get('formation',''):>6} "
              f"{entry.get('vol_lookback',''):>6} "
              f"{str(entry.get('vol_target',''))[:5]:>7} "
              f"{entry['sharpe']:>8.3f} {entry['max_dd']:>8.2%} {entry['return']:>8.2%}")

    # Best Train 파라미터로 Test 실행
    print(f"\n--- Best Train 파라미터로 OOS Test ---")
    print(f"  Best Train: mode={best_train['mode']}, F={best_train.get('formation')}, "
          f"VolLB={best_train.get('vol_lookback')}, VolTgt={best_train.get('vol_target')}")

    train_result = simulate_decomposed(
        train_closes, symbols, best_train["mode"],
        formation=best_train.get("formation", 14),
        vol_lookback=best_train.get("vol_lookback", 40),
        vol_target=best_train.get("vol_target", 0.10),
    )
    test_result = simulate_decomposed(
        test_closes, symbols, best_train["mode"],
        formation=best_train.get("formation", 14),
        vol_lookback=best_train.get("vol_lookback", 40),
        vol_target=best_train.get("vol_target", 0.10),
    )

    tk = train_result["kpis"]
    sk = test_result["kpis"]

    header = f"{'Period':<12} {'Sharpe':>8} {'Calmar':>8} {'Sortino':>8} {'MaxDD':>8} {'Return':>9} {'Trades':>7}"
    print(f"\n{header}")
    print("-" * len(header))
    print(f"{'Train':<12} {tk['sharpe']:>8.3f} {tk['calmar']:>8.3f} {tk['sortino']:>8.3f} "
          f"{tk['max_dd']:>8.2%} {tk['total_return']:>9.2%} {tk['num_trades']:>7}")
    print(f"{'Test (OOS)':<12} {sk['sharpe']:>8.3f} {sk['calmar']:>8.3f} {sk['sortino']:>8.3f} "
          f"{sk['max_dd']:>8.2%} {sk['total_return']:>9.2%} {sk['num_trades']:>7}")

    # 괴리 측정
    sharpe_gap = abs(tk["sharpe"] - sk["sharpe"])
    print(f"\n  Sharpe 괴리: {sharpe_gap:.3f} (Train {tk['sharpe']:.3f} vs Test {sk['sharpe']:.3f})")

    # 모든 모드에 대해 OOS 테스트
    print(f"\n--- 전략별 OOS 비교 ---")
    oos_modes = [
        ("Mom Only",    "momentum_only", {"formation": 14}),
        ("Vol Only",    "vol_only",      {"vol_lookback": 40, "vol_target": 0.10}),
        ("Combined",    "combined",      {"formation": 14, "vol_lookback": 40, "vol_target": 0.10}),
        ("Best Train",  best_train["mode"], {
            "formation": best_train.get("formation", 14),
            "vol_lookback": best_train.get("vol_lookback", 40),
            "vol_target": best_train.get("vol_target", 0.10),
        }),
    ]

    header = f"{'Strategy':<18} {'Train Sharpe':>12} {'Test Sharpe':>12} {'Gap':>8} {'Test MaxDD':>10} {'Test Ret':>9}"
    print(header)
    print("-" * len(header))

    for label, mode, extra_params in oos_modes:
        r_train = simulate_decomposed(train_closes, symbols, mode, **extra_params)
        r_test = simulate_decomposed(test_closes, symbols, mode, **extra_params)
        gap = abs(r_train["kpis"]["sharpe"] - r_test["kpis"]["sharpe"])
        print(f"{label:<18} {r_train['kpis']['sharpe']:>12.3f} {r_test['kpis']['sharpe']:>12.3f} "
              f"{gap:>8.3f} {r_test['kpis']['max_dd']:>10.2%} {r_test['kpis']['total_return']:>9.2%}")

    # BTC OOS 비교
    btc_test_ret = test_closes["BTC/USD"].pct_change().dropna()
    btc_test_kpis = compute_all_kpis(btc_test_ret, btc_test_ret, 0, 0)
    print(f"{'BTC B&H':<18} {'N/A':>12} {btc_test_kpis['sharpe']:>12.3f} "
          f"{'N/A':>8} {btc_test_kpis['max_dd']:>10.2%} {btc_test_kpis['total_return']:>9.2%}")

    # Overfitting 판정
    if sharpe_gap < 0.3:
        overfit_status = "LOW — 파라미터 robust"
    elif sharpe_gap < 0.5:
        overfit_status = "MODERATE — 주의 필요"
    else:
        overfit_status = "HIGH — overfitting 가능성"

    print(f"\n  ★ Overfitting Risk: {overfit_status}")

    # Registry 기록
    record_experiment(
        hypothesis="Best 전략 파라미터가 OOS(2025)에서도 유효한가",
        params={
            "best_train_mode": best_train["mode"],
            "formation": best_train.get("formation"),
            "vol_lookback": best_train.get("vol_lookback"),
            "vol_target": best_train.get("vol_target"),
            "train_period": f"~{train_end}",
            "test_period": f"{test_start}~",
        },
        results=sk,
        status="PASSED" if sharpe_gap < 0.5 and sk["sharpe"] > 0 else "FAILED",
        notes=(f"Train Sharpe={tk['sharpe']:.3f}, Test Sharpe={sk['sharpe']:.3f}, "
               f"Gap={sharpe_gap:.3f}. {overfit_status}. "
               f"Test MaxDD={sk['max_dd']:.2%}, Test Return={sk['total_return']:.2%}"),
    )


# ─────────────────────────────────────────────
# 종합 리포트
# ─────────────────────────────────────────────

def summary_report():
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

    experiment_7_signal_decomposition(closes)
    experiment_8_out_of_sample(closes)
    summary_report()
