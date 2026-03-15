"""스모크 테스트 — 모든 핵심 모듈이 import 가능한지 확인."""

import pytest


class TestImports:
    def test_import_main(self):
        import main

    def test_import_signal(self):
        from agents import signal

    def test_import_guardian(self):
        from agents import guardian

    def test_import_operator(self):
        from agents import operator

    def test_import_scribe(self):
        from agents import scribe

    def test_import_metrics(self):
        from analysis import metrics

    def test_import_benchmark(self):
        from analysis import benchmark

    def test_import_attribution(self):
        from analysis import attribution

    def test_import_config(self):
        from core import config

    def test_import_db(self):
        from core import db

    def test_import_data(self):
        from core import data

    def test_import_models(self):
        from core import models

    def test_import_backtest(self):
        import backtest

    def test_import_check_system_health(self):
        import check_system_health

    def test_import_check_promotion(self):
        import check_promotion


class TestDataclasses:
    def test_signal_result_creation(self):
        from core.models import SignalResult
        s = SignalResult(
            symbol="BTC/USD", date="2026-01-01",
            momentum_score=0.05, is_trending=True,
            realized_vol=0.08, vol_scalar=1.0,
            final_position=1.0, reason="VOL_LONG",
        )
        assert s.symbol == "BTC/USD"

    def test_position_target_creation(self):
        from core.models import PositionTarget
        t = PositionTarget(
            symbol="BTC/USD", target_weight=0.5,
            current_weight=0.3, delta=0.2,
            execute=True, reason="VOL_LONG",
        )
        assert t.delta == 0.2

    def test_execution_creation(self):
        from core.models import Execution
        e = Execution(
            decision_id=None, timestamp="2026-01-01T00:00:00",
            symbol="BTC/USD", side="BUY", qty=0.1,
            fill_price=70000.0, signal_price=70000.0,
            slippage_bps=0.0, fee_bps=10.0,
            order_type="SIMULATED", status="FILLED",
        )
        assert e.side == "BUY"
