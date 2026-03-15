"""KPI 계산 — Sharpe, Calmar, Sortino, Max Drawdown, Fee-adjusted Return."""

import numpy as np
import pandas as pd


def sharpe_ratio(returns: pd.Series, risk_free: float = 0.0) -> float:
    """연환산 Sharpe Ratio (크립토: 365일)."""
    excess = returns - risk_free / 365
    if excess.std() == 0:
        return 0.0
    return float((excess.mean() / excess.std()) * np.sqrt(365))


def calmar_ratio(returns: pd.Series) -> float:
    """Calmar = 연환산 수익률 / |Max Drawdown|."""
    if len(returns) == 0:
        return 0.0
    annual_return = (1 + returns).prod() ** (365 / len(returns)) - 1
    max_dd = max_drawdown(returns)
    if max_dd == 0:
        return 0.0
    return float(annual_return / abs(max_dd))


def sortino_ratio(returns: pd.Series, risk_free: float = 0.0) -> float:
    """Sortino = (수익 - 무위험) / 하방 변동성."""
    excess = returns - risk_free / 365
    downside = excess[excess < 0]
    downside_std = downside.std() if len(downside) > 0 else 1e-10
    if downside_std == 0:
        downside_std = 1e-10
    return float((excess.mean() / downside_std) * np.sqrt(365))


def max_drawdown(returns: pd.Series) -> float:
    """최대 낙폭 (음수 값)."""
    if len(returns) == 0:
        return 0.0
    cumulative = (1 + returns).cumprod()
    peak = cumulative.cummax()
    drawdown = (cumulative - peak) / peak
    return float(drawdown.min())


def fee_adjusted_return(
    returns: pd.Series,
    trades: int,
    fee_bps: float = 40,
) -> float:
    """수수료 차감 후 총 수익률 (거래 횟수 기반, 레거시 호환)."""
    gross = (1 + returns).prod() - 1
    total_fees = trades * fee_bps / 10000
    return float(gross - total_fees)


def fee_adjusted_return_notional(
    returns: pd.Series,
    total_fee_paid: float,
    initial_capital: float = 10000.0,
) -> float:
    """[FIX #4] 수수료 차감 후 총 수익률 (실제 수수료 금액 기반).

    incremental rebalancing에서는 거래 금액이 매번 다르므로,
    fee_bps * trade_count가 아니라 실제 수수료 합계를 사용해야 정확.
    """
    gross = (1 + returns).prod() - 1
    fee_drag = total_fee_paid / initial_capital
    return float(gross - fee_drag)


def btc_excess_return(
    strategy_returns: pd.Series,
    btc_returns: pd.Series,
) -> float:
    """BTC Buy&Hold 대비 초과수익."""
    strategy_total = (1 + strategy_returns).prod() - 1
    btc_total = (1 + btc_returns).prod() - 1
    return float(strategy_total - btc_total)


def dd_protection_ratio(strategy_dd: float, btc_dd: float) -> float:
    """MaxDD 방어율 (primary KPI). 예: 전략 -12%, BTC -76% → 84.2% 방어."""
    if btc_dd == 0:
        return 0.0
    return float(1 - (strategy_dd / btc_dd))


def annual_trade_count(trades: int, days: int) -> float:
    """연환산 거래 횟수."""
    if days == 0:
        return 0.0
    return trades * (365 / days)


def compute_all_kpis(
    strategy_returns: pd.Series,
    btc_returns: pd.Series,
    trades: int,
    fee_bps: float = 40,
) -> dict:
    """모든 KPI를 한번에 계산."""
    return {
        "sharpe": sharpe_ratio(strategy_returns),
        "calmar": calmar_ratio(strategy_returns),
        "sortino": sortino_ratio(strategy_returns),
        "max_dd": max_drawdown(strategy_returns),
        "fee_adj_return": fee_adjusted_return(strategy_returns, trades, fee_bps),
        "btc_excess": btc_excess_return(strategy_returns, btc_returns),
        "total_return": float((1 + strategy_returns).prod() - 1),
        "btc_total_return": float((1 + btc_returns).prod() - 1),
        "num_trades": trades,
    }
