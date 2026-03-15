"""벤치마크 비교 — BTC B&H, Equal Weight B&H, Cash."""

import pandas as pd
from analysis.metrics import compute_all_kpis


def btc_buy_and_hold(closes: pd.DataFrame) -> pd.Series:
    """BTC Buy & Hold 일일 수익률."""
    return closes["BTC/USD"].pct_change().dropna()


def equal_weight_buy_and_hold(closes: pd.DataFrame, symbols: list[str]) -> pd.Series:
    """Equal Weight Buy & Hold 일일 수익률."""
    available = [s for s in symbols if s in closes.columns]
    daily = closes[available].pct_change().dropna()
    return daily.mean(axis=1)


def cash_benchmark(n_days: int) -> pd.Series:
    """Cash (수익률 0) 벤치마크."""
    return pd.Series([0.0] * n_days)


def compare_all(
    strategy_returns: pd.Series,
    closes: pd.DataFrame,
    symbols: list[str],
    strategy_trades: int,
    fee_bps: float = 40,
) -> dict[str, dict]:
    """전략 vs 모든 벤치마크 비교."""
    btc_returns = btc_buy_and_hold(closes)
    ew_returns = equal_weight_buy_and_hold(closes, symbols)

    # 날짜 범위 맞추기
    common_dates = strategy_returns.index.intersection(btc_returns.index)
    strat = strategy_returns.loc[common_dates]
    btc = btc_returns.loc[common_dates]

    common_ew = strat.index.intersection(ew_returns.index)
    ew = ew_returns.loc[common_ew]

    results = {
        "strategy": compute_all_kpis(strat, btc, strategy_trades, fee_bps),
        "btc_bh": compute_all_kpis(btc, btc, 0, 0),
        "ew_bh": compute_all_kpis(ew.loc[ew.index.intersection(btc.index)], btc.loc[btc.index.intersection(ew.index)], 0, 0),
    }

    return results


def format_comparison(results: dict[str, dict]) -> str:
    """비교 결과를 보기 좋게 포맷팅."""
    lines = []
    header = f"{'Metric':<25} {'Strategy':>12} {'BTC B&H':>12} {'EW B&H':>12}"
    lines.append(header)
    lines.append("-" * len(header))

    metrics = [
        ("Total Return", "total_return", "{:.2%}"),
        ("Sharpe Ratio", "sharpe", "{:.3f}"),
        ("Calmar Ratio", "calmar", "{:.3f}"),
        ("Sortino Ratio", "sortino", "{:.3f}"),
        ("Max Drawdown", "max_dd", "{:.2%}"),
        ("Fee-Adj Return", "fee_adj_return", "{:.2%}"),
        ("BTC Excess Return", "btc_excess", "{:.2%}"),
        ("Num Trades", "num_trades", "{:.0f}"),
    ]

    for label, key, fmt in metrics:
        vals = []
        for name in ["strategy", "btc_bh", "ew_bh"]:
            v = results[name].get(key, 0)
            vals.append(fmt.format(v))
        lines.append(f"{label:<25} {vals[0]:>12} {vals[1]:>12} {vals[2]:>12}")

    return "\n".join(lines)
