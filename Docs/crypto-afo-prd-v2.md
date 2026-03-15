# Crypto AFO — PRD v2

> 주식 AFO와 별개 프로젝트. Claude + GPT 분석, 학술 연구, Alpaca 문서 기반.
> 최종 업데이트: 2026-03-15

---

## 01. 시스템 정의

### Crypto AFO ≠ 헤지펀드 레플리카

**정의**: Fund-grade logging과 리스크 통제를 가진 directional crypto quant research sleeve.

프로 펀드가 밀리초 단위로 하는 HFT, 마켓 메이킹, funding rate capture는 우리 영역이 아니다. 우리는 일~주 단위 모멘텀/추세 전략을 연구하고, 모든 의사결정을 기록하고, 데이터가 쌓일수록 판단이 좋아지는 시스템을 만든다.

### 성공의 정의

**"내가 자는 동안에도 학습하는 투자 시스템"**

"돈을 많이 버는 시스템"도 아니고 "세계 최고 퀀트 연구 시스템"도 아니다. 규모는 개인 자산 수준이지만, 의사결정 품질이 시간이 갈수록 좋아지는 시스템. 전략 자체가 moat가 아니라, 전략을 찾고 검증하고 개선하는 프로세스가 moat.

### 우리의 Edge

- **시간 축 차이.** 프로 펀드와 다른 경기장. 일~주 단위 전략은 co-location, 수십억 자본이 필요 없다. 판단 품질로 승부하는 영역.
- **규모의 역설.** 대형 펀드가 무시하는 규모($500K 이하)가 개인에겐 충분히 의미 있다.
- **Decision Ledger의 복리 효과.** 시간이 갈수록 가치가 올라가는 자산. 전략 코드는 복제 가능하지만 1년치 의사결정 데이터는 복제 불가.

### 우리의 한계 — 솔직하게

- **Alpaca Spot Long-Only는 천장이 있다.** Funding rate, basis trade, short 전략 같은 구조적 알파는 Spot으로 접근 불가.
- **유동성.** Alpaca 자체 거래소는 Binance 대비 얕다.
- **수수료.** Maker 0.15%, Taker 0.25%. 높은 turnover는 alpha를 빠르게 잠식.

이 한계는 인정하되, 이 환경 안에서 최대한을 뽑아내는 게 Phase 1~2의 목표.

---

## 02. Stage 로드맵

### Stage 1 — 기준선 수립

- **Universe**: BTC, ETH
- **전략**: Absolute Momentum + Cash Rotation
- **인프라**: Alpaca Spot, 로컬 Mac
- **초점**: 백테스트 → 페이퍼 전환
- **증명할 것**: 하락장에서 현금으로 도망가는 시스템이 BTC B&H를 risk-adjusted 기준으로 이기는가

### Stage 2 — Universe 확장

- **Universe**: Core 4~7종목 (BTC, ETH, SOL, XRP, AVAX, LINK, DOT)
- **전략**: + Vol Scaling, Biweekly/Threshold 리밸런싱
- **인프라**: Mac → Oracle Cloud Free
- **초점**: 리밸런싱 최적화, 수수료 관리
- **KPI**: Calmar, Sortino 중심

### Stage 3 — 도구 확장 검토

- **조건**: Stage 2 성과 한계가 데이터로 확인
- **행동**: Binance/Bybit testbed 착수
- **추가 데이터**: Funding rate, Open Interest, 청산 데이터 수집
- **전략**: Signal discovery (구조적 알파 탐색)

### Stage 4 — 필요 시 확장

- **조건**: Stage 3에서 구조적 알파 확인
- **도구**: Futures, Short, Basis Trade
- **핵심**: "목표"가 아니라 "필요성이 증명되면 추가하는 도구"
- **원칙**: 복잡성을 추가하는 게 아니라 데이터가 요구할 때만

---

## 03. Stage 승격 조건

감이 아니라 데이터로. 모든 조건은 사전 정의, 예외 없음.

### Stage 1 → Stage 2

- IF: 백테스트 Sharpe > 1.0
- AND: Max DD < -30%
- AND: BTC B&H 대비 risk-adjusted 우위 확인
- AND: 페이퍼 트레이딩 4주 이상 안정 운영
- → THEN: Core 종목 확장 + 리밸런싱 최적화 착수

### Stage 2 → Stage 3

- IF: 페이퍼 3개월 연속 운영
- AND: 백테스트 vs 페이퍼 성과 괴리 < 20%
- AND: Calmar > 0.5
- AND: Sortino > 1.0
- AND: fee-adjusted return 양수 유지
- → THEN: 실전 전환 ($500 시작) + Binance 데이터 수집 병행

### Stage 3 → Stage 4

- IF: Spot long-flat이 6개월간 BTC 대비 risk-adjusted 우위 없음 (데이터로 증명)
- AND: funding/basis 데이터에서 유의미한 시그널 발견 (백테스트 확인)
- → THEN: Futures testbed 착수. 아니면 Stage 3 유지.

### 안전장치 — 강제 회귀

어느 Stage에서든: 실전 포트폴리오 Max DD > -25% → 즉시 전량 현금 전환 + Stage 1 (백테스트)로 회귀해서 전략 재검토. 감정 개입 없이 자동 실행.

---

## 04. 벤치마크 & KPI

Sharpe 하나만 보면 크립토에서 착시가 생긴다. 최소 4개 지표를 모든 Phase에서 측정.

| 지표 | 정의 | 목표 | 왜 필요한가 |
|------|------|------|------------|
| **BTC B&H 초과수익** | 전략 수익 - BTC Buy&Hold 수익 | > 0 | "그냥 BTC 들고 있는 것보다 나은가?" |
| **Calmar Ratio** | 연환산 수익 / \|Max Drawdown\| | > 0.5 | 수익 대비 최악의 낙폭 |
| **Sortino Ratio** | (수익 - 무위험) / 하방 변동성 | > 1.0 | 하락만 벌하는 공정한 지표 |
| **Fee-Adjusted Return** | 수수료 차감 후 순수익 | > 0 | 수수료가 alpha를 잠식하는지 확인 |

**벤치마크 비교 대상**:
- Primary: BTC Buy & Hold
- Secondary: Equal Weight B&H (BTC+ETH 50/50), 현금 100%

---

## 05. 전략 설계 — Stage 1

### Absolute Momentum + Cash Rotation

가장 단순한 형태의 추세 추종. 모멘텀이 양수면 매수, 음수면 현금. Bitwise Trendwise (EMA 기반 추세 → 크립토 선물 vs 국채 로테이션)와 철학적으로 동일하되, 도구는 Spot + Cash.

**매커니즘 (매일 UTC 00:00 기준)**:
1. 각 종목의 N일 수익률 계산 (= momentum score)
2. Momentum > 0 → 해당 종목에 투자
3. Momentum ≤ 0 → 현금 (USDC 또는 cash)
4. 투자 종목들은 Equal Weight로 배분

**Phase 1 백테스트 파라미터 그리드**:
- Formation Period: 7, 14, 21, 28일
- Holding Period: 1, 3, 5, 7일
- = 16개 조합 Grid Search

### 학술 근거

**주식 vs 크립토 모멘텀 차이**:
- 주식: formation 6~12개월, persistence 1~3개월
- 크립토: formation **1~4주**, persistence **~1주**
- 주식 AFO의 20일/60일 lookback을 그대로 적용하면 안 됨
- 출처: Liu et al. (2022), Borgards (2021)

**Risk-Managed Momentum**:
- Barroso & Santa-Clara (2015)의 vol-scaling을 크립토에 적용 시:
  - 주식에서는 crash 방어가 주효과
  - 크립토에서는 **수익 향상**이 주효과
- → Stage 2에서 vol scaling 추가 예정

**최적 Lookback/Holding (학술 참고)**:
- 28일 formation / 5일 holding → Sharpe 1.51 달성 (시장 포트폴리오 0.84 대비)
- CTREND factor: 55,296개 구현 조합 중 79%에서 유의미한 양의 Sharpe
- 주말 효과 존재: 주말 모멘텀 수익률이 평일보다 높음 (알트코인에서 특히)

### 거래비용 전략

**수수료 구조**: Alpaca Tier 1 Maker 0.15%, Taker 0.25%. 라운드트립 0.30~0.50%.

**위험**: 주간 리밸런싱 시 연간 ~26% 수수료 부담 가능 (Taker 기준).

**완화 전략**:
- Limit order 우선으로 Maker 수수료 확보
- 리밸런싱 빈도를 최소화 (biweekly 또는 threshold 기반)
- 포지션 변경이 없으면 거래 안 함 (turnover filter)

---

## 06. 에이전트 아키텍처

하이브리드 구조: 계산/실행은 순수 Python, 분석/판단은 Claude Code CLI (Max 플랜 활용, API 비용 $0).

### Phase 1~2: 에이전트 4명

| 에이전트 | 역할 | 구현 | 호출 주기 |
|----------|------|------|----------|
| **Signal** | 모멘텀 계산, 시그널 생성 | Python 95% + LLM 5% | 매일 |
| **Guardian** | 리스크 체크, 포지션 사이징, 드로다운 관리 | Python 90% + LLM 10% | 매일 |
| **Operator** | 주문 실행 (Paper/Live) | Python 100% | 리밸런싱일 |
| **Scribe** | 의사결정 기록, 패턴 분석, 저널링 | Claude Code CLI 80% + Python 20% | 매일/주간 |

### Phase 3+: 추가 에이전트 2명

| 에이전트 | 역할 | 추가 조건 |
|----------|------|----------|
| **Watchdog** | 24/7 시장 감시, 이상 탐지, 긴급 알림 | 실전 트레이딩 전환 시 |
| **Architect** | 메타 분석, regime detection, 전략 방향 제안 | 3개월 이상 데이터 축적 후 |

### 핵심 원칙

**돈을 지키는 코드 (Signal, Guardian, Operator)는 절대 LLM에 의존하지 않는다.** LLM은 "더 똑똑하게 만드는 레이어"이지, "작동에 필수인 레이어"가 아니다. LLM이 다운돼도 시스템은 어제의 규칙대로 계속 돌아간다.

### 실행 흐름

**매일 자동 (cron, LLM 불필요)**:
```
python main.py → Signal → Guardian → Operator → DB 저장
```

**일일/주간 분석 (Claude Code CLI)**:
```
claude -p "오늘 결과 분석해줘: $(cat daily_result.json)"
```
실패해도 트레이딩은 계속 돌아감.

---

## 07. 데이터 아키텍처

### 5개 데이터 레이어

| 레이어 | 내용 | 가치 |
|--------|------|------|
| **L1: Market Data** | OHLCV 일봉, Core 종목 | Commodity — 누구나 접근 가능 |
| **L2: Feature Store** | 모멘텀 스코어, vol, 상관관계, regime | 가공 데이터 — Signal의 원료 |
| **L3: Decision Ledger** | 매수/매도 사유, 시그널 값, 시장 상태 | **Moat — 복제 불가능한 자산** |
| **L4: Research Registry** | 실험 기록, 가설, 백테스트 결과, 채택/기각 | 메타 학습 — 시스템 진화의 원천 |
| **L5: Attribution** | 수익 원천 분해 (시그널 / vol scaling / 시장 베타) | "뭐가 진짜 작동하는지" 증명 |

DB: SQLite (Phase 1~2) → Supabase Free (Phase 3+). 스키마 상세는 DEVSPEC.md 참조.

---

## 08. 운영 인프라 & 비용

| 항목 | Phase 1-2 | Phase 3+ | 비용 |
|------|-----------|----------|------|
| 마켓 데이터 | Alpaca Free | Alpaca Free | $0 |
| 페이퍼 트레이딩 | Alpaca Paper | — | $0 |
| 서버 | Mac 로컬 | Oracle Cloud Free Tier | $0 |
| DB | SQLite (로컬) | Supabase Free | $0 |
| LLM | Claude Code CLI (Max Plan) | 동일 | $0 (구독 포함) |
| 알림 | Telegram Bot | 동일 | $0 |
| **총 월 운영비** | **$0** | **$0** | **$0** |
| 실전 거래 수수료 | — | 0.15~0.25%/거래 | Alpha에서 차감 |

---

## 09. 리스크 관리 원칙

### Hard Limits — 자동 실행, 예외 없음

- 포트폴리오 Max DD > -25% → 전량 현금 전환
- 개별 종목 1일 -15% → 해당 종목 청산
- 단일 종목 포트폴리오 비중 > 40% → 리밸런싱

### Soft Limits — Scribe가 기록, Sean이 판단

- 30일 Sharpe < 0.5 → 전략 재검토 트리거
- Fee-adjusted return 3주 연속 음수 → 리밸런싱 빈도 재검토
- 백테스트 vs 실전 괴리 > 30% → 원인 분석

### 실전 진입 원칙

**잃어도 되는 돈만 투입.** $500으로 시작. 3개월 연속 양의 alpha 후에만 증액. Decision Ledger의 데이터가 "증액해도 된다"를 증명해야 함. 감이 아니라 데이터로.

---

## 10. 운영 원칙 10개

1. **도구보다 판단.** 복잡한 도구를 추가하는 것이 목표가 아니라, 시스템이 더 똑똑해지는 게 목표.
2. **데이터로 승격.** 모든 Stage 전환은 사전 정의된 KPI 충족으로만. 감정/의견으로 절대 불가.
3. **LLM ≠ 실행.** 돈을 지키는 코드에 LLM 의존성 0. LLM은 분석/학습 레이어.
4. **기록이 자산.** 모든 의사결정은 Scribe가 기록. 기록 안 된 결정은 존재하지 않는 결정.
5. **수수료 먼저.** 어떤 전략이든 fee-adjusted return이 양수가 아니면 실전 불가.
6. **생존 우선.** 수익보다 드로다운 관리가 먼저. 살아남아야 데이터가 쌓인다.
7. **단순함 유지.** 복잡성을 추가하려면 데이터가 정당화해야 함. 아니면 추가 안 함.
8. **도구 확장은 필요가 증명될 때만.** Futures/Short은 "목표"가 아니라 데이터가 요구할 때만.
9. **백테스트 ≠ 실전.** 페이퍼 트레이딩으로 괴리를 측정하고, 괴리가 충분히 작을 때만 실전.
10. **운영비 $0 고수.** 고정 비용이 올라가면 수익 압박이 전략을 왜곡한다. Max 플랜 + 무료 인프라 최대 활용.

---

## 11. Phase 1 실행 계획 (4주)

### Week 1 — 데이터 수집 + 기본 인프라

- Alpaca `CryptoHistoricalDataClient`로 BTC/ETH 일봉 3~5년 수집
- SQLite DB 스키마 생성 (market_bars, features, experiments)
- 기본 모멘텀 계산 함수 구현 (7/14/21/28일)

### Week 2 — 백테스트 엔진

- 16개 파라미터 조합 Grid Search 구현
- 거래비용 반영 (30~50bps)
- KPI 4개 (BTC B&H 초과수익, Calmar, Sortino, Fee-adjusted) 자동 계산
- 벤치마크 비교 (BTC B&H, EW B&H, Cash)

### Week 3 — 결과 분석 + Walk-Forward

- 최적 파라미터 조합 선정
- Walk-forward validation (과적합 체크)
- Scribe: 실험 결과를 Research Registry에 기록 (Claude Code CLI)

### Week 4 — 페이퍼 전환 준비

- Alpaca Paper Trading 연결
- Operator 에이전트 구현 (주문 실행)
- cron 스케줄 설정
- Telegram 알림 연결
- Stage 1 → 2 승격 기준 달성 여부 평가
