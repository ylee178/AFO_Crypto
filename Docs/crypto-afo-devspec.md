# Crypto AFO — Dev Spec v1

> Claude Code 개발 참조 문서. 이 파일을 프로젝트 루트에 두고 개발 시 참조.

---

## 프로젝트 정의

**Crypto AFO**: Fund-grade logging과 리스크 통제를 가진 directional crypto quant research sleeve.

- Alpaca Spot Long-Only (Stage 1~2)
- Absolute Momentum + Cash Rotation
- BTC/ETH 2종목으로 시작 → Core 7종목 확장
- 모든 의사결정을 Decision Ledger에 기록

---

## 디렉토리 구조

```
crypto-afo/
├── DEVSPEC.md              # 이 문서
├── main.py                 # 일일 실행 진입점
├── backtest.py             # 백테스트 실행기
│
├── agents/
│   ├── signal.py           # 모멘텀 시그널 계산
│   ├── guardian.py         # 리스크 체크 + 포지션 사이징
│   ├── operator.py         # 주문 실행 (Paper/Live)
│   └── scribe.py           # 결과 기록 + LLM 분석 호출
│
├── core/
│   ├── data.py             # Alpaca 데이터 수집
│   ├── db.py               # SQLite 연결 + 스키마
│   ├── models.py           # 데이터 모델 (dataclass)
│   └── config.py           # 설정 (종목, 파라미터, 임계값)
│
├── analysis/
│   ├── metrics.py          # KPI 계산 (Sharpe, Calmar, Sortino 등)
│   └── benchmark.py        # 벤치마크 비교
│
├── data/
│   └── crypto_afo.db       # SQLite DB
│
├── logs/
│   └── daily.log           # 실행 로그
│
└── reports/
    └── ...                 # Scribe가 생성하는 리포트
```

---

## 의존성

```
# requirements.txt
alpaca-py>=0.21.0        # Alpaca SDK (데이터 + 트레이딩)
pandas>=2.0              # 데이터 처리
numpy>=1.24              # 수치 계산
```

추가 패키지 없음. 최소 의존성 유지.

---

## DB 스키마 (SQLite)

### market_bars — 시장 데이터
```sql
CREATE TABLE market_bars (
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,       -- YYYY-MM-DD
    open        REAL NOT NULL,
    high        REAL NOT NULL,
    low         REAL NOT NULL,
    close       REAL NOT NULL,
    volume      REAL NOT NULL,
    source      TEXT DEFAULT 'alpaca',
    PRIMARY KEY (symbol, date)
);
```

### features — 파생 지표 (Feature Store)
```sql
CREATE TABLE features (
    symbol          TEXT NOT NULL,
    date            TEXT NOT NULL,
    momentum_7d     REAL,           -- 7일 수익률
    momentum_14d    REAL,           -- 14일 수익률
    momentum_21d    REAL,           -- 21일 수익률
    momentum_28d    REAL,           -- 28일 수익률
    realized_vol_20d REAL,          -- 20일 실현 변동성
    vol_ratio       REAL,           -- 단기vol / 장기vol
    PRIMARY KEY (symbol, date)
);
```

### decisions — 의사결정 기록 (Decision Ledger)
```sql
CREATE TABLE decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    action          TEXT NOT NULL,   -- BUY / SELL / HOLD / CASH
    reason          TEXT,            -- 왜 이 결정을 했는지
    signal_name     TEXT,            -- 어떤 시그널 기반인지
    signal_value    REAL,            -- 시그널 수치
    position_size   REAL,            -- 목표 비중 (0~1)
    market_regime   TEXT,            -- TRENDING / MEAN_REVERTING / UNKNOWN
    confidence      REAL             -- 0~1
);
```

### executions — 체결 기록
```sql
CREATE TABLE executions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id     INTEGER REFERENCES decisions(id),
    timestamp       TEXT NOT NULL,
    fill_price      REAL NOT NULL,
    signal_price    REAL,            -- 시그널 시점 가격
    slippage_bps    REAL,            -- 슬리피지 (bps)
    fee_bps         REAL,            -- 수수료 (bps)
    order_type      TEXT,            -- MARKET / LIMIT
    status          TEXT             -- FILLED / PARTIAL / FAILED
);
```

### portfolio_snapshots — 일일 포트폴리오 상태
```sql
CREATE TABLE portfolio_snapshots (
    date            TEXT PRIMARY KEY,
    total_value     REAL NOT NULL,
    cash_value      REAL NOT NULL,
    cash_pct        REAL NOT NULL,
    positions_json  TEXT,            -- {"BTC": 0.4, "ETH": 0.3, ...}
    drawdown_pct    REAL,            -- 고점 대비 현재 낙폭
    sharpe_30d      REAL,
    calmar          REAL,
    sortino         REAL
);
```

### experiments — 연구 기록 (Research Registry)
```sql
CREATE TABLE experiments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    hypothesis      TEXT NOT NULL,    -- "28일 모멘텀이 7일보다 나을 것이다"
    params_json     TEXT NOT NULL,    -- {"formation": 28, "holding": 7, ...}
    backtest_sharpe REAL,
    backtest_calmar REAL,
    backtest_sortino REAL,
    backtest_max_dd REAL,
    backtest_fee_adj_return REAL,
    walkforward_sharpe REAL,
    status          TEXT DEFAULT 'PENDING',  -- PENDING / PASSED / FAILED / ADOPTED
    notes           TEXT              -- Scribe 분석 내용
);
```

### attribution — 수익 원천 분해
```sql
CREATE TABLE attribution (
    date            TEXT PRIMARY KEY,
    total_return    REAL,
    signal_return   REAL,            -- 시그널에 의한 수익
    market_return   REAL,            -- BTC 베타에 의한 수익
    residual        REAL             -- 설명 안 되는 수익
);
```

---

## Agent 상세 명세

### Signal Agent (`agents/signal.py`)

**역할**: 모멘텀 시그널 계산 + 매수/매도 판단 생성

**입력**: market_bars 테이블의 최신 데이터

**출력**: 각 종목별 SignalResult

```python
@dataclass
class SignalResult:
    symbol: str
    date: str
    momentum_score: float    # N일 수익률
    signal: str              # "BUY" | "CASH"
    formation_days: int      # 사용된 lookback 기간
```

**로직**:
```
momentum = (close_today / close_N_days_ago) - 1
if momentum > 0: signal = "BUY"
else: signal = "CASH"
```

**Phase 1 (백테스트)**: 16개 formation×holding 조합을 순회하며 SignalResult 생성
**Phase 2 (페이퍼)**: 채택된 최적 파라미터로 매일 1회 실행

---

### Guardian Agent (`agents/guardian.py`)

**역할**: 리스크 체크, 포지션 사이징, hard limit 강제

**입력**: SignalResult[], portfolio_snapshots 최신 상태

**출력**: 각 종목별 PositionTarget (Signal을 리스크 필터링한 결과)

```python
@dataclass
class PositionTarget:
    symbol: str
    target_weight: float     # 0~1 (0이면 미보유)
    reason: str              # "SIGNAL_BUY" | "RISK_OVERRIDE_CASH" | "DD_LIMIT"
```

**Hard Limits (자동 실행, 예외 없음)**:
- 포트폴리오 drawdown > -25% → 전량 CASH (모든 signal 무시)
- 개별 종목 1일 변동 > -15% → 해당 종목 CASH
- 단일 종목 비중 > 40% → 초과분 매도

**포지션 사이징**:
- Equal Weight: BUY 시그널 종목에 균등 배분
- 예: BTC=BUY, ETH=CASH → BTC 100%, ETH 0%
- 예: BTC=BUY, ETH=BUY → BTC 50%, ETH 50%

---

### Operator Agent (`agents/operator.py`)

**역할**: Guardian의 PositionTarget을 실제 주문으로 변환

**입력**: PositionTarget[], 현재 포지션 (Alpaca API)

**출력**: 체결 결과 → executions 테이블에 저장

**로직**:
```
현재 포지션과 PositionTarget의 차이 계산
차이 > 최소 거래 단위 → 주문 생성
Limit order 우선 (maker 수수료 확보)
30분 후 미체결 → market order 전환 (Phase 3+에서)
```

**Phase 1 (백테스트)**: 시뮬레이션 — 실제 주문 없이 가상 체결
**Phase 2 (페이퍼)**: Alpaca Paper API로 실제 주문
**Phase 3 (실전)**: Alpaca Live API (코드 동일, 도메인만 전환)

**중요**: 이 에이전트는 100% 코드. LLM 의존성 0. 규칙대로만 실행.

---

### Scribe Agent (`agents/scribe.py`)

**역할**: 모든 의사결정 기록 + LLM 분석

**구현**: 2단계

1. **구조화 기록 (Python)**: decisions, executions, portfolio_snapshots 테이블에 기계적으로 저장
2. **분석 기록 (Claude Code CLI)**: 일일/주간 분석을 LLM으로 생성

```bash
# 일일 분석 호출 예시
claude -p "다음 트레이딩 결과를 분석해줘. 
오늘 결정: $(cat /tmp/daily_decisions.json)
포트폴리오 상태: $(cat /tmp/portfolio_snapshot.json)
분석 포인트: 1) 오늘 결정의 근거가 타당했는가 2) 어제 결정의 결과는 어떠했는가 3) 특이 패턴이 있는가"
```

**주간 분석**:
```bash
claude -p "이번 주 트레이딩 주간 리뷰를 작성해줘.
이번 주 결정들: $(cat /tmp/weekly_decisions.json)
KPI: $(cat /tmp/weekly_kpi.json)
분석 포인트: 1) 이번 주 가장 좋은/나쁜 결정 2) KPI 추이 3) 다음 주 조정 필요사항"
```

**Phase 1 (백테스트)**: 실험마다 Claude Code CLI로 결과 분석 → experiments.notes에 저장
**Phase 2+**: cron으로 자동 호출

---

## KPI 계산 공식

### metrics.py에 구현

```python
def sharpe_ratio(returns: pd.Series, risk_free: float = 0.0) -> float:
    """연환산 Sharpe Ratio"""
    excess = returns - risk_free / 365
    return (excess.mean() / excess.std()) * np.sqrt(365)  # 크립토는 365일

def calmar_ratio(returns: pd.Series) -> float:
    """Calmar = 연환산 수익률 / |Max Drawdown|"""
    annual_return = (1 + returns).prod() ** (365 / len(returns)) - 1
    max_dd = max_drawdown(returns)
    return annual_return / abs(max_dd) if max_dd != 0 else 0

def sortino_ratio(returns: pd.Series, risk_free: float = 0.0) -> float:
    """Sortino = (수익 - 무위험) / 하방 변동성"""
    excess = returns - risk_free / 365
    downside = excess[excess < 0]
    downside_std = downside.std() if len(downside) > 0 else 1e-10
    return (excess.mean() / downside_std) * np.sqrt(365)

def max_drawdown(returns: pd.Series) -> float:
    """최대 낙폭 (음수)"""
    cumulative = (1 + returns).cumprod()
    peak = cumulative.cummax()
    drawdown = (cumulative - peak) / peak
    return drawdown.min()

def fee_adjusted_return(returns: pd.Series, trades: int, fee_bps: float = 40) -> float:
    """수수료 차감 후 총 수익률. fee_bps = 라운드트립 수수료 (기본 40bps)"""
    gross = (1 + returns).prod() - 1
    total_fees = trades * fee_bps / 10000
    return gross - total_fees

def btc_excess_return(strategy_returns: pd.Series, btc_returns: pd.Series) -> float:
    """BTC Buy&Hold 대비 초과수익"""
    strategy_total = (1 + strategy_returns).prod() - 1
    btc_total = (1 + btc_returns).prod() - 1
    return strategy_total - btc_total
```

---

## 설정 (config.py)

```python
# ── Universe ──
STAGE_1_SYMBOLS = ["BTC/USD", "ETH/USD"]
STAGE_2_SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", 
                   "AVAX/USD", "LINK/USD", "DOT/USD"]

# ── 백테스트 파라미터 그리드 ──
FORMATION_DAYS = [7, 14, 21, 28]
HOLDING_DAYS = [1, 3, 5, 7]

# ── 거래비용 (bps) ──
FEE_MAKER_BPS = 15
FEE_TAKER_BPS = 25
FEE_ROUNDTRIP_BPS = 40   # maker+taker 평균

# ── 리스크 Hard Limits ──
MAX_PORTFOLIO_DRAWDOWN = -0.25    # -25%
MAX_SINGLE_DAY_LOSS = -0.15      # -15%
MAX_SINGLE_POSITION_WEIGHT = 0.40 # 40%

# ── 리밸런싱 ──
REBALANCE_TIME_UTC = "00:05"      # UTC 00:05 (일봉 확정 후)

# ── Stage 승격 기준 ──
STAGE_1_TO_2 = {
    "min_sharpe": 1.0,
    "max_drawdown": -0.30,
    "min_paper_days": 28,
}

STAGE_2_TO_3 = {
    "min_paper_months": 3,
    "max_backtest_paper_gap": 0.20,
    "min_calmar": 0.5,
    "min_sortino": 1.0,
}
```

---

## 실행 명령어

### Phase 1: 백테스트
```bash
# 데이터 수집
python -m core.data --symbols BTC/USD ETH/USD --years 3

# 백테스트 실행 (16개 조합 Grid Search)
python backtest.py --stage 1

# 결과 확인
python -m analysis.metrics --experiment latest

# Scribe 분석 (Claude Code CLI)
claude -p "$(python -m agents.scribe --mode backtest_summary)"
```

### Phase 2: 페이퍼 트레이딩
```bash
# 일일 실행 (cron에 등록)
python main.py --mode paper

# 수동 실행 시
python main.py --mode paper --dry-run  # 주문 안 넣고 시그널만 확인
```

### crontab 등록
```bash
# 매일 UTC 00:05 (한국시간 09:05, 호주시간 11:05 AEDT)
5 0 * * * cd ~/crypto-afo && python main.py --mode paper >> logs/daily.log 2>&1

# 주간 Scribe 분석 (매주 일요일 UTC 01:00)
0 1 * * 0 cd ~/crypto-afo && claude -p "$(python -m agents.scribe --mode weekly)" >> reports/weekly.md 2>&1
```

---

## main.py 실행 흐름

```python
def run_daily(mode: str = "paper"):
    """매일 자동 실행되는 메인 루프"""
    
    # 1. 데이터 수집
    data.update_bars(config.STAGE_1_SYMBOLS)
    
    # 2. Signal Agent — 모멘텀 계산
    signals = signal.compute(config.STAGE_1_SYMBOLS)
    
    # 3. Guardian Agent — 리스크 필터링
    targets = guardian.check(signals, get_portfolio_state())
    
    # 4. Operator Agent — 주문 실행
    if mode == "paper":
        executions = operator.execute_paper(targets)
    elif mode == "live":
        executions = operator.execute_live(targets)
    elif mode == "backtest":
        executions = operator.simulate(targets)
    
    # 5. Scribe Agent — 기록 (Python 부분만, LLM은 별도 cron)
    scribe.record_decisions(signals, targets, executions)
    scribe.snapshot_portfolio()
    
    # 6. 알림 (Phase 2+)
    if any(t.reason == "DD_LIMIT" for t in targets):
        notify_telegram("⚠️ Drawdown limit triggered — 전량 현금 전환")
```

---

## 참고 — Alpaca 크립토 API 요약

```python
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame

# 크립토 데이터는 API 키 없이 접근 가능
client = CryptoHistoricalDataClient()

# 일봉 데이터 수집
request = CryptoBarsRequest(
    symbol_or_symbols=["BTC/USD", "ETH/USD"],
    timeframe=TimeFrame.Day,
    start=datetime(2022, 1, 1),
    end=datetime(2025, 12, 31)
)
bars = client.get_crypto_bars(request)
df = bars.df  # MultiIndex DataFrame

# 페이퍼 트레이딩 (API 키 필요)
from alpaca.trading.client import TradingClient
trading = TradingClient(api_key, secret_key, paper=True)

# 실전 전환 시 paper=False로만 변경
trading = TradingClient(api_key, secret_key, paper=False)
```

### 주요 제약
- 숏 셀링 불가
- 마진 매수 불가
- 주문당 $200K 상한
- 주문 타입: Market, Limit, Stop Limit
- TIF: GTC, IOC만
- 24/7 거래 가능
- 수수료: Maker 0.15%, Taker 0.25% (Tier 1)

---

## 개발 순서 (Phase 1)

1. `core/db.py` — SQLite 스키마 생성
2. `core/config.py` — 설정값
3. `core/models.py` — 데이터 모델
4. `core/data.py` — Alpaca 데이터 수집 → market_bars에 저장
5. `agents/signal.py` — 모멘텀 계산
6. `agents/guardian.py` — 리스크 체크
7. `agents/operator.py` — 백테스트 시뮬레이션
8. `analysis/metrics.py` — KPI 4개 계산
9. `analysis/benchmark.py` — BTC B&H, EW B&H 비교
10. `backtest.py` — Grid Search 실행기
11. `agents/scribe.py` — 결과 기록
12. `main.py` — 통합 실행기
