"""Operator Agent — Incremental Rebalancing (Binance Spot).

100% 코드. LLM 의존성 0.

핵심: delta만 거래. position 0.8 → 0.7 = SELL 0.1만큼만.
"""

import os
import logging
from datetime import datetime, timezone

from core.config import (
    FEE_MAKER_BPS, FEE_TAKER_BPS,
    to_binance_symbol, from_binance_symbol,
)
from core.models import PositionTarget, Execution
from core.db import get_connection

log = logging.getLogger(__name__)


def execute(
    targets: list[PositionTarget],
    portfolio_value: float,
    current_prices: dict[str, float],
    mode: str = "paper",
) -> list[Execution]:
    """모드에 따라 실행."""
    if mode == "simulate":
        return _execute_sim(targets, portfolio_value, current_prices)
    elif mode == "paper":
        return _execute_binance(targets, portfolio_value, current_prices, testnet=True)
    elif mode == "live":
        return _execute_binance(targets, portfolio_value, current_prices, testnet=False)
    else:
        raise ValueError(f"Unknown mode: {mode}")


def _execute_sim(
    targets: list[PositionTarget],
    portfolio_value: float,
    current_prices: dict[str, float],
) -> list[Execution]:
    """시뮬레이션: delta만큼만 가상 체결."""
    executions = []
    now = datetime.now(timezone.utc).isoformat()

    for t in targets:
        price = current_prices.get(t.symbol, 0.0)

        if not t.execute:
            executions.append(Execution(
                decision_id=None, timestamp=now, symbol=t.symbol,
                side="HOLD", qty=0.0, fill_price=price,
                signal_price=price, slippage_bps=0.0, fee_bps=0.0,
                order_type="SIMULATED", status="SKIPPED",
            ))
            continue

        if price <= 0:
            continue

        delta_value = t.delta * portfolio_value
        if abs(delta_value) < 1.0:
            continue

        side = "BUY" if delta_value > 0 else "SELL"
        qty = abs(delta_value) / price

        executions.append(Execution(
            decision_id=None, timestamp=now, symbol=t.symbol,
            side=side, qty=qty, fill_price=price,
            signal_price=price, slippage_bps=0.0,
            fee_bps=FEE_MAKER_BPS,
            order_type="SIMULATED", status="FILLED",
        ))

    return executions


def _execute_binance(
    targets: list[PositionTarget],
    portfolio_value: float,
    current_prices: dict[str, float],
    testnet: bool = True,
) -> list[Execution]:
    """Binance API로 incremental 주문 실행."""
    api_key = os.environ.get("BINANCE_API_KEY", "")
    secret_key = os.environ.get("BINANCE_SECRET_KEY", "")

    if not api_key or not secret_key:
        # [FIX #2] Silent fallback 제거 — API 키 없으면 명시적 에러
        raise RuntimeError("Binance API keys not set. Set BINANCE_API_KEY and BINANCE_SECRET_KEY in .env")

    from binance.client import Client

    if testnet:
        client = Client(api_key, secret_key, testnet=True)
    else:
        client = Client(api_key, secret_key)

    executions = []
    now = datetime.now(timezone.utc).isoformat()

    for t in targets:
        price = current_prices.get(t.symbol, 0.0)

        if not t.execute:
            executions.append(Execution(
                decision_id=None, timestamp=now, symbol=t.symbol,
                side="HOLD", qty=0.0, fill_price=price,
                signal_price=price, slippage_bps=0.0, fee_bps=0.0,
                order_type="LIMIT", status="SKIPPED",
            ))
            continue

        if price <= 0:
            continue

        delta_value = t.delta * portfolio_value
        if abs(delta_value) < 1.0:
            continue

        bn_symbol = to_binance_symbol(t.symbol)
        side_str = "BUY" if delta_value > 0 else "SELL"
        qty = abs(delta_value) / price

        # 정밀도 처리 (Binance는 심볼별 stepSize가 있음)
        try:
            info = client.get_symbol_info(bn_symbol)
            if info:
                for f in info.get("filters", []):
                    if f["filterType"] == "LOT_SIZE":
                        step = float(f["stepSize"])
                        if step > 0:
                            qty = round(qty - (qty % step), 8)
                        min_qty = float(f["minQty"])
                        if qty < min_qty:
                            log.info(f"  {t.symbol}: qty {qty} < minQty {min_qty} — skipping")
                            continue
        except Exception:
            pass

        try:
            if side_str == "BUY":
                order = client.order_market_buy(symbol=bn_symbol, quantity=qty)
            else:
                order = client.order_market_sell(symbol=bn_symbol, quantity=qty)

            # 체결 정보 추출
            fills = order.get("fills", [])
            if fills:
                total_qty = sum(float(f["qty"]) for f in fills)
                avg_price = sum(float(f["price"]) * float(f["qty"]) for f in fills) / total_qty
                total_commission = sum(float(f["commission"]) for f in fills)
            else:
                total_qty = float(order.get("executedQty", qty))
                avg_price = price
                total_commission = 0

            slippage = abs(avg_price - price) / price * 10000 if price > 0 else 0.0

            executions.append(Execution(
                decision_id=None, timestamp=now, symbol=t.symbol,
                side=side_str, qty=total_qty, fill_price=avg_price,
                signal_price=price, slippage_bps=slippage,
                fee_bps=FEE_TAKER_BPS,
                order_type="MARKET", status=order.get("status", "FILLED"),
            ))
            log.info(f"  {t.symbol}: {side_str} {total_qty:.6f} @ {avg_price:.2f} "
                     f"slip={slippage:.1f}bps commission={total_commission}")

        except Exception as e:
            log.error(f"  {t.symbol}: Order FAILED — {e}")
            executions.append(Execution(
                decision_id=None, timestamp=now, symbol=t.symbol,
                side=side_str, qty=0.0, fill_price=price,
                signal_price=price, slippage_bps=0.0, fee_bps=0.0,
                order_type="MARKET", status="FAILED",
            ))

    return executions


def save_executions(executions: list[Execution]):
    """체결 결과를 DB에 저장."""
    conn = get_connection()
    for ex in executions:
        conn.execute(
            """INSERT INTO executions
               (decision_id, timestamp, symbol, side, qty, fill_price,
                signal_price, slippage_bps, fee_bps, order_type, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ex.decision_id, ex.timestamp, ex.symbol, ex.side, ex.qty,
                ex.fill_price, ex.signal_price, ex.slippage_bps, ex.fee_bps,
                ex.order_type, ex.status,
            ),
        )
    conn.commit()
    conn.close()


def get_binance_positions(testnet: bool = True) -> dict[str, float]:
    """Binance에서 현재 보유 자산 조회."""
    api_key = os.environ.get("BINANCE_API_KEY", "")
    secret_key = os.environ.get("BINANCE_SECRET_KEY", "")

    if not api_key or not secret_key:
        return {}

    from binance.client import Client
    client = Client(api_key, secret_key, testnet=testnet, requests_params={"timeout": 15})

    positions = {}
    account = client.get_account()
    for balance in account.get("balances", []):
        free = float(balance["free"])
        locked = float(balance["locked"])
        total = free + locked
        if total > 0 and balance["asset"] not in ("USDT", "BNB", "USD"):
            sym = from_binance_symbol(balance["asset"] + "USDT")
            positions[sym] = total

    return positions


def get_binance_account(testnet: bool = True) -> dict:
    """Binance 계좌 정보 조회."""
    api_key = os.environ.get("BINANCE_API_KEY", "")
    secret_key = os.environ.get("BINANCE_SECRET_KEY", "")

    if not api_key or not secret_key:
        return {"equity": 0, "cash": 0}

    from binance.client import Client
    client = Client(api_key, secret_key, testnet=testnet, requests_params={"timeout": 15})

    account = client.get_account()
    usdt_balance = 0.0
    total_btc = 0.0

    for balance in account.get("balances", []):
        if balance["asset"] == "USDT":
            usdt_balance = float(balance["free"]) + float(balance["locked"])

    # 총 자산을 USDT 기준으로 추정
    positions = get_binance_positions(testnet)
    total_value = usdt_balance
    for sym, qty in positions.items():
        bn_sym = to_binance_symbol(sym)
        try:
            ticker = client.get_symbol_ticker(symbol=bn_sym)
            price = float(ticker["price"])
            total_value += qty * price
        except Exception:
            pass

    return {
        "equity": total_value,
        "cash": usdt_balance,
    }
