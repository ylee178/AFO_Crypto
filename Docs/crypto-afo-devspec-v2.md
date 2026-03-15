# Crypto AFO — Dev Spec v2

> Claude Code 개발 참조 문서. PRD v3 기반.
> v1 대비 변경: 전략이 "Absolute Momentum"에서 "Volatility Targeting + Momentum Crash Filter"로 변경.
> 8개 실험 결과 반영.

---

## 프로젝트 정의

**Crypto AFO**: Volatility targeting + momentum crash filter 기반 crypto quant system.

- Vol targeting이 수익 엔진, momentum이 crash filter (실험 7에서 확인)
- Alpaca Spot Long-Only (Stage 1~3)
- BTC/ETH 2종목
- 모든 의사결정을 Decision Ledger에 기록

**⚠️ 현재 파라미터 상태: Provisional.** VolLB=40d, VolTarget=10%는 OOS에서 의미 있는 성과 저하가 확인됨 (gap 0.553). Stage 1.5 robustness 확인 전까지 production 승격 불가.

---

## 디렉토리 구조

```
crypto-afo/
├── PRD.md                  # 전략/방향/원칙
├── DEVSPEC.md              # 이 문서
├── main.py                 # 일일 실행 진입점
├── backtest.py             # 백테스트 실행기
│
├── agents/
│   ├── signal.py           # Vol targeting + momentum crash filter
│   ├── guardian.py         # 리스크 체크 + hard limits
│   ├── operator.py         # 주문 실행 (Sim/Paper/Live)
│   └── scribe.py           # 결과 기록 + LLM 분석 호출
│
├── core/
│   ├── data.py             # Alpaca 데이터 수집 + feature 계산
│   ├── db.py               # SQLite 연결 + 스키마
│   ├── models.py           # 데이터 모델 (dataclass)
│   └── config.py           # 설정 (파라미터, 임계값)
│
├── analysis/
│   ├── metrics.py          # KPI 계산
│   ├── benchmark.py        # 벤치마크 비교
│   └── attribution.py      # Vol vs Momentum 기여 분해 (v2 추가)
│
├── data/
│   └── crypto_afo.db       # SQLite DB
│
├── logs/
│   └── daily.log
│
└── reports/
    └── ...
```

---

## 의존성

```
# requirements.txt
alpaca-py>=0.21.0
pandas>=2.0
numpy>=1.24
```

---

## 전략 로직 상세

### 핵심 공식

```python
# 1. Momentum Crash Filter
momentum = (close_today / close_N_days_ago) - 1
is_trending = momentum > 0  # True면 long 허용, False면 현금

# 2. Volatility Targeting (수익 엔진)
realized_vol = returns.rolling(vol_lookback).std() * np.sqrt(365)
vol_scalar = target_vol / realized_vol
position_size = min(vol_scalar, max_leverage)  # cap at max_leverage

# 3. Combined Signal
if is_trending:
    final_position = position_size  # vol-scaled long
else:
    final_position = 0.0            # cash (crash filter 발동)
```

### 실험으로 증명된 역할 분담

```
Vol Targeting 단독:  Sharpe 0.314, MaxDD -68%  → 수익은 나지만 방어력 없음
Momentum 단독:       Sharpe 0.030, MaxDD -28%  → 방어는 되지만 수익 없음
Combined:            Sharpe 0.628, MaxDD -12%  → 시너지 발생
Random + Vol:        Sharpe 0.391, MaxDD -16%  → Momentum이 random보다 나음 확인
```

---

## DB 스키마 (SQLite)

### market_bars
```sql
CREATE TABLE market_bars (
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,
    open        REAL NOT NULL,
    high        REAL NOT NULL,
    low         REAL NOT NULL,
    close       REAL NOT NULL,
    volume      REAL NOT NULL,
    source      TEXT DEFAULT 'alpaca',
    PRIMARY KEY (symbol, date)
);
```

### features (v2: vol 관련 컬럼 강조)
```sql
CREATE TABLE features (
    symbol          TEXT NOT NULL,
    date            TEXT NOT NULL,
    momentum_7d     REAL,
    momentum_14d    REAL,
    momentum_21d    REAL,
    momentum_28d    REAL,
    realized_vol_20d REAL,
    realized_vol_40d REAL,          -- v2: vol targeting 핵심 lookback
    vol_scalar      REAL,           -- v2: target_vol / realized_vol
    vol_ratio       REAL,
    PRIMARY KEY (symbol, date)
);
```

### decisions (v2: vol/momentum 기여 분해 추가)
```sql
CREATE TABLE decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    action          TEXT NOT NULL,   -- BUY / SELL / HOLD / CASH
    reason          TEXT,            -- SIGNAL_LONG / CRASH_FILTER_CASH / DD_LIMIT
    momentum_value  REAL,            -- momentum score
    vol_scalar      REAL,            -- vol targeting multiplier
    final_position  REAL,            -- 최종 포지션 크기 (0~max_leverage)
    position_change REAL,            -- 이전 대비 변경량 (threshold 판단용)
    market_regime   TEXT,
    confidence      REAL
);
```

### executions
```sql
CREATE TABLE executions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id     INTEGER REFERENCES decisions(id),
    timestamp       TEXT NOT NULL,
    fill_price      REAL NOT NULL,
    signal_price    REAL,
    slippage_bps    REAL,
    fee_bps         REAL,
    order_type      TEXT,            -- MARKET / LIMIT
    status          TEXT             -- FILLED / PARTIAL / FAILED / SKIPPED_THRESHOLD
);
```

### portfolio_snapshots (v2: MaxDD 방어율 추가)
```sql
CREATE TABLE portfolio_snapshots (
    date            TEXT PRIMARY KEY,
    total_value     REAL NOT NULL,
    cash_value      REAL NOT NULL,
    cash_pct        REAL NOT NULL,
    positions_json  TEXT,
    drawdown_pct    REAL,
    btc_drawdown_pct REAL,          -- v2: BTC의 현재 drawdown (방어율 계산용)
    dd_protection   REAL,           -- v2: 1 - (전략DD / BTC DD)
    sharpe_30d      REAL,
    calmar          REAL,
    sortino         REAL,
    fee_adj_return  REAL            -- v2: 수수료 차감 후 수익
);
```

### experiments (v2: OOS 컬럼 추가)
```sql
CREATE TABLE experiments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    hypothesis      TEXT NOT NULL,
    params_json     TEXT NOT NULL,
    backtest_sharpe REAL,
    backtest_calmar REAL,
    backtest_sortino REAL,
    backtest_max_dd REAL,
    backtest_fee_adj_return REAL,
    oos_sharpe      REAL,           -- v2: out-of-sample 결과
    oos_max_dd      REAL,           -- v2
    train_test_gap  REAL,           -- v2: overfitting 지표
    walkforward_sharpe REAL,
    status          TEXT DEFAULT 'PENDING',
    notes           TEXT
);
```

### attribution (v2: 핵심 테이블)
```sql
CREATE TABLE attribution (
    date            TEXT PRIMARY KEY,
    total_return    REAL,
    vol_timing_return REAL,         -- vol targeting에 의한 수익
    momentum_filter_return REAL,    -- momentum filter에 의한 수익 (보통 음수 — 기회비용)
    crash_savings   REAL,           -- momentum filter가 막아준 손실
    market_return   REAL,
    residual        REAL
);
```

---

## Agent 상세 명세

### Signal Agent (`agents/signal.py`)

**역할**: Vol targeting 계산 + momentum crash filter

**v1→v2 변경**: 순수 모멘텀 시그널 → vol scalar + momentum filter 결합

```python
@dataclass
class SignalResult:
    symbol: str
    date: str
    momentum_score: float       # N일 수익률
    is_trending: bool           # momentum > 0
    realized_vol: float         # annualized realized vol
    vol_scalar: float           # target_vol / realized_vol
    final_position: float       # 0 (cash) 또는 vol_scalar (long)
    signal_reason: str          # "VOL_LONG" | "CRASH_FILTER_CASH"
```

**로직**:
```python
def compute(symbol, date, bars, config):
    # 1. Momentum crash filter
    momentum = bars.close[-1] / bars.close[-config.FORMATION_DAYS] - 1
    is_trending = momentum > 0

    # 2. Confirmation (optional)
    if config.CONFIRMATION_DAYS > 0:
        recent_momentum = [
            bars.close[-i] / bars.close[-i - config.FORMATION_DAYS] - 1
            for i in range(1, config.CONFIRMATION_DAYS + 1)
        ]
        is_trending = all(m > 0 for m in recent_momentum)

    # 3. Vol targeting
    returns = bars.close.pct_change().dropna()
    realized_vol = returns[-config.VOL_LOOKBACK:].std() * np.sqrt(365)
    vol_scalar = config.VOL_TARGET / realized_vol
    vol_scalar = min(vol_scalar, config.MAX_LEVERAGE)

    # 4. Combined
    final_position = vol_scalar if is_trending else 0.0

    return SignalResult(...)
```

---

### Guardian Agent (`agents/guardian.py`)

**역할**: 리스크 체크, hard limits 강제, position change threshold

```python
@dataclass
class PositionTarget:
    symbol: str
    target_weight: float        # 0 ~ max_leverage
    reason: str                 # SIGNAL_LONG | CRASH_FILTER_CASH | DD_LIMIT | THRESHOLD_SKIP
    position_change: float      # 이전 대비 변경량
    execute_trade: bool         # threshold 이상이면 True
```

**Hard Limits**:
- 포트폴리오 DD > -20% → 전량 CASH
- 개별 종목 1일 변동 > -15% → 해당 종목 CASH
- 단일 종목 비중 > 40% → 초과분 매도

**Position Change Threshold** (v2 추가):
```python
position_change = abs(new_position - current_position)
execute_trade = position_change > config.POSITION_THRESHOLD
# threshold 미만이면 거래 안 함 → 수수료 절약
```

---

### Operator Agent (`agents/operator.py`)

**변경 없음.** Guardian이 `execute_trade=True`인 것만 실행.

---

### Scribe Agent (`agents/scribe.py`)

**v2 추가**: Attribution 분석

```python
def record_attribution(date, total_return, signals):
    """vol timing vs momentum filter 기여 분해"""
    # vol timing return: vol scalar를 적용한 수익
    # momentum filter return: momentum 때문에 cash였던 날의 기회비용
    # crash savings: momentum filter가 막아준 하락
```

---

## 설정 (config.py) — v2

```python
# ── Universe ──
STAGE_1_SYMBOLS = ["BTC/USD", "ETH/USD"]

# ── 전략 파라미터 (실험 6 최적, PROVISIONAL) ──
FORMATION_DAYS = 14             # momentum lookback
CONFIRMATION_DAYS = 5           # whipsaw filter (실험 3)
VOL_LOOKBACK = 40               # realized vol 계산 기간
VOL_TARGET = 0.10               # 10% annualized target vol
MAX_LEVERAGE = 2.0              # vol scalar 상한

# ── Position Change Threshold (Stage 1.5 실험 대상) ──
POSITION_THRESHOLD = 0.10       # 10% 이상 변경 시에만 거래 (실험 9에서 최적화)

# ── 거래비용 (bps) ──
FEE_MAKER_BPS = 15
FEE_TAKER_BPS = 25
FEE_ROUNDTRIP_BPS = 40

# ── 리스크 Hard Limits ──
MAX_PORTFOLIO_DRAWDOWN = -0.20  # v2: -25% → -20% 하향
MAX_SINGLE_DAY_LOSS = -0.15
MAX_SINGLE_POSITION_WEIGHT = 0.40

# ── 리밸런싱 ──
REBALANCE_TIME_UTC = "00:05"

# ── Stage 승격 기준 ──
STAGE_1_5_TO_2 = {
    "min_fee_adj_return": 0.0,      # fee-adjusted ≥ 0
    "max_oos_fee_adj": -0.05,       # OOS > -5%
    "max_annual_trades": 100,
}

STAGE_2_TO_3 = {
    "min_paper_weeks": 4,
    "max_dd_vs_btc_dd_gap": 0.05,   # 괴리 < 5%p
    "dd_protection_ratio": 0.50,     # MaxDD < BTC DD × 0.5
    "min_fee_adj_return": 0.0,
}

# ── Parameter Stability Requirement ──
PARAM_STABILITY = {
    "neighbor_range": 0.20,         # ±20% 범위
    "min_retention": 0.70,          # 성과의 70% 이상 유지
}
```

---

## KPI 계산 (analysis/metrics.py) — v2

```python
def dd_protection_ratio(strategy_dd: float, btc_dd: float) -> float:
    """MaxDD 방어율 (v2 primary KPI)"""
    if btc_dd == 0:
        return 0
    return 1 - (strategy_dd / btc_dd)
    # 예: 전략 -12%, BTC -76% → 1 - (12/76) = 0.842 = 84.2% 방어

def calmar_ratio(returns: pd.Series) -> float:
    annual_return = (1 + returns).prod() ** (365 / len(returns)) - 1
    max_dd = max_drawdown(returns)
    return annual_return / abs(max_dd) if max_dd != 0 else 0

def sortino_ratio(returns: pd.Series, risk_free: float = 0.0) -> float:
    excess = returns - risk_free / 365
    downside = excess[excess < 0]
    downside_std = downside.std() if len(downside) > 0 else 1e-10
    return (excess.mean() / downside_std) * np.sqrt(365)

def sharpe_ratio(returns: pd.Series, risk_free: float = 0.0) -> float:
    excess = returns - risk_free / 365
    return (excess.mean() / excess.std()) * np.sqrt(365)

def max_drawdown(returns: pd.Series) -> float:
    cumulative = (1 + returns).cumprod()
    peak = cumulative.cummax()
    drawdown = (cumulative - peak) / peak
    return drawdown.min()

def fee_adjusted_return(returns: pd.Series, trades: int, fee_bps: float = 40) -> float:
    gross = (1 + returns).prod() - 1
    total_fees = trades * fee_bps / 10000
    return gross - total_fees

def annual_trade_count(trades: int, days: int) -> float:
    return trades * (365 / days)
```

---

## Attribution 분석 (analysis/attribution.py) — v2 신규

```python
def decompose_returns(
    combined_returns: pd.Series,
    vol_only_returns: pd.Series,
    momentum_only_returns: pd.Series,
    btc_returns: pd.Series
) -> dict:
    """
    전략 수익을 vol timing / momentum filter / 시너지로 분해.
    실험 7의 결과를 일일 단위로 재현.
    """
    return {
        "total": combined_returns.sum(),
        "vol_timing_contribution": vol_only_returns.sum(),
        "momentum_filter_contribution": momentum_only_returns.sum(),
        "synergy": combined_returns.sum() - vol_only_returns.sum() - momentum_only_returns.sum(),
        "btc_market": btc_returns.sum(),
    }
```

---

## 벤치마크 구조 (analysis/benchmark.py) — v2

```python
# 3층 벤치마크
BENCHMARKS = {
    "btc_bh":       "BTC Buy & Hold — primary, 하락장 구간 비교 핵심",
    "btc_cash_50":  "50/50 BTC-Cash — 가장 단순한 방어 전략",
    "vol_only":     "Vol-targeted BTC (no momentum) — momentum filter 순수 기여 측정",
}
```

---

## 실행 명령어

### Stage 1.5: 거래비용 최적화 실험
```bash
# 실험 9: Position threshold 최적화
python backtest.py --experiment threshold --thresholds 0.05,0.10,0.15,0.20

# 실험 10: Vol scaling + confirmation 결합
python backtest.py --experiment vol_confirm

# 실험 11: Adaptive vol lookback
python backtest.py --experiment adaptive_vol

# 실험 12: Parameter stability
python backtest.py --experiment param_stability
```

### Stage 2: 페이퍼 트레이딩
```bash
# 일일 실행
python main.py --mode paper

# crontab
5 0 * * * cd ~/crypto-afo && python main.py --mode paper >> logs/daily.log 2>&1

# 주간 분석 (Claude Code CLI)
0 1 * * 0 cd ~/crypto-afo && claude -p "$(python -m agents.scribe --mode weekly)" >> reports/weekly.md 2>&1
```

---

## main.py 실행 흐름 — v2

```python
def run_daily(mode: str = "paper"):
    # 1. 데이터 수집
    data.update_bars(config.STAGE_1_SYMBOLS)

    # 2. Signal Agent — vol targeting + momentum filter
    signals = signal.compute(config.STAGE_1_SYMBOLS)

    # 3. Guardian Agent — 리스크 + position threshold
    targets = guardian.check(signals, get_portfolio_state())

    # 4. Operator Agent — threshold 이상만 실행
    executions = operator.execute(targets, mode=mode)

    # 5. Scribe Agent — 기록
    scribe.record_decisions(signals, targets, executions)
    scribe.snapshot_portfolio()
    scribe.record_attribution(signals)  # v2: attribution 분해

    # 6. 알림
    if any(t.reason == "DD_LIMIT" for t in targets):
        notify_telegram("⚠️ DD limit → 전량 현금")
```

---

## 개발 순서 (Stage 1.5)

이미 Phase 1 코드가 있으므로, 추가/수정이 필요한 것:

1. `core/config.py` — v2 파라미터 업데이트 (VOL_TARGET, POSITION_THRESHOLD 등)
2. `agents/signal.py` — vol targeting 로직 추가 (이미 실험에서 구현됨 → 정리)
3. `agents/guardian.py` — position change threshold 추가
4. `analysis/attribution.py` — 신규 생성
5. `analysis/metrics.py` — dd_protection_ratio 추가
6. `backtest.py` — 실험 9~12 실행 기능 추가
7. `core/db.py` — 스키마 v2 마이그레이션

---

## 참고 — Research Registry 현황 (8개 실험)

| # | 실험 | Sharpe | MaxDD | Status |
|---|------|--------|-------|--------|
| 1 | Regime 분리 | — | -31% (BTC -76%) | ✅ Insight |
| 2 | Threshold 리밸런싱 | — | — | ✅ Insight |
| 3 | Confirmation Period | 0.207 | -27% | ✅ Adopted |
| 4 | 혼합 포트폴리오 | 0.362 | -61% | ✅ Rejected |
| 5 | Vol Scaling | 0.628 | -12% | ✅ Adopted |
| 6 | Vol Scaling 상세 | 0.628 | -12% | ✅ Adopted |
| 7 | Signal Decomposition | 0.628 (C) | -12% (C) | ✅ Critical |
| 8 | Out-of-Sample | 0.220 (OOS) | -14% (OOS) | ✅ Critical |
