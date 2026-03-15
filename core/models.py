from dataclasses import dataclass, field


@dataclass
class SignalResult:
    symbol: str
    date: str
    momentum_score: float       # N일 수익률
    is_trending: bool           # momentum > 0 (confirmation 포함)
    realized_vol: float         # annualized realized vol
    vol_scalar: float           # target_vol / realized_vol (capped)
    final_position: float       # 0 (cash) 또는 vol_scalar (long)
    reason: str                 # "VOL_LONG" | "CRASH_FILTER_CASH" | "DD_LIMIT"


@dataclass
class PositionTarget:
    symbol: str
    target_weight: float        # 0 ~ max
    current_weight: float       # 현재 비중
    delta: float                # target - current
    execute: bool               # |delta| > threshold
    reason: str                 # "VOL_LONG" | "CRASH_FILTER_CASH" | "DD_LIMIT" | "THRESHOLD_SKIP"


@dataclass
class Execution:
    decision_id: int | None
    timestamp: str
    symbol: str
    side: str                   # "BUY" | "SELL"
    qty: float
    fill_price: float
    signal_price: float | None
    slippage_bps: float
    fee_bps: float
    order_type: str             # "MARKET" | "LIMIT" | "SIMULATED"
    status: str                 # "FILLED" | "PARTIAL" | "FAILED" | "SKIPPED"
