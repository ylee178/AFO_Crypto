# Crypto AFO — PRD v3

> 주식 AFO와 별개 프로젝트.
> 8개 실험 결과 기반 업데이트. v2 대비 전략 정의, 승격 기준, KPI 전면 수정.
> 최종 업데이트: 2026-03-15

---

## 01. 시스템 정의

### 전략 정체성

**This is not a pure alpha-maximization strategy. It is a volatility-managed defensive crypto sleeve designed to reduce catastrophic drawdowns while preserving acceptable upside participation.**

이 문장이 모든 의사결정의 기준이다. 수익을 더 내려고 복잡성을 추가하고 싶을 때, 이 문장으로 돌아와서 판단한다.

### 정의

**Crypto AFO**: Fund-grade logging과 리스크 통제를 가진 crypto volatility targeting system with momentum crash filter.

### v2 → v3 핵심 변경

| 항목 | PRD v2 | PRD v3 (실험 기반) |
|------|--------|-------------------|
| 전략 정체 | Absolute Momentum + Cash Rotation | **Volatility Targeting + Momentum Crash Filter** |
| 모멘텀의 역할 | Alpha generator | **Crash filter** (alpha는 vol timing에서 나옴) |
| Primary KPI | Sharpe | **MaxDD 방어력** (Sharpe는 secondary) |
| 승격 기준 | Sharpe > 1.0 | **MaxDD < BTC DD × 0.5, Calmar > 0.4** |
| 벤치마크 질문 | "BTC를 이기는가?" | **"BTC 하락장에서 살아남는가?"** |

### 8개 실험이 밝힌 전략의 정체

1. **Momentum 단독은 크립토에서 무의미하다.** (Sharpe 0.030) 주식에서의 6~12개월 모멘텀과 달리, 크립토 time-series momentum은 단독 edge가 없다.
2. **Vol targeting이 수익 엔진이다.** (단독 Sharpe 0.314) 변동성 낮을 때 포지션 키우고 높을 때 줄이는 것만으로도 BTC B&H와 비슷한 수익.
3. **Momentum의 진짜 역할은 crash filter다.** Vol timing 단독 MaxDD -68% → Combined -12%. 모멘텀이 하방 리스크를 차단해서 시너지 발생.
4. **결합이 핵심이다.** Combined Sharpe 0.628 > Vol(0.314) + Mom(0.030). 시너지 +0.284 존재.
5. **OOS에서 crash protection은 살아남았다.** 2025년 BTC -49% 구간에서 Combined -14%. 전략의 핵심 가치가 유지됨.
6. **Vol scaling 파라미터에 과적합 리스크 있다.** Train→Test Sharpe gap 0.553. Adaptive vol scaling으로 전환 필요.

### ⚠️ OOS 경고 — 파라미터는 아직 Provisional

현재 vol scaling 파라미터 (VolLB=40d, VolTarget=10%)는 in-sample 최적값이며, OOS에서 의미 있는 성과 저하가 확인되었다 (Sharpe 0.773 → 0.220, gap 0.553). **이 파라미터는 확정된 정답이 아니라 "current best interpretation"이며, Stage 1.5 robustness 확인을 거쳐야 production 승격이 가능하다.** 문서의 모든 수치는 이 전제 하에 읽어야 한다.

### 성공의 정의

**"BTC exposure의 하방 리스크를 체계적으로 관리하는 자동화 시스템"**

수익 극대화가 아니라 **drawdown suppression**이 핵심 가치. 불장에서 BTC를 이기는 건 목표가 아니다. 하락장에서 BTC 대비 Max DD를 50% 이상 줄이는 것이 목표.

### 우리의 Edge

- **Volatility regime exploitation.** 크립토는 low vol → trending, high vol → chaos. 이 패턴을 vol targeting이 체계적으로 활용.
- **Momentum as crash filter.** 순수 alpha는 아니지만, vol timing의 치명적 약점(sudden crash에 노출)을 보완.
- **Decision Ledger의 복리 효과.** 8개 실험의 가설→결과→인사이트가 모두 기록됨. 이 연구 자산이 시간이 갈수록 전략 개선의 원천.
- **시간 축 차이.** 프로 펀드의 밀리초 게임과 다른 일~주 단위 경기장.

### 우리의 한계 — 솔직하게

- **Alpaca Spot Long-Only 천장.** Funding rate, basis trade, short 전략 같은 구조적 알파 접근 불가.
- **Vol scaling 파라미터 과적합 리스크.** OOS gap 0.553. Adaptive 방식으로 전환 필요.
- **수수료.** Vol scaling이 거래를 늘림 (208회). Threshold 최적화가 아직 미완.
- **유동성.** Alpaca 자체 거래소는 Binance 대비 얕음.

---

## 02. 전략 구조

### Volatility Targeting + Momentum Crash Filter

**이 전략은 CTA trend following 펀드와 구조적으로 동일하다.** Bull market에서 underperform, crash/bear에서 outperform, sideways에서 소폭 우위.

#### 수익 엔진: Volatility Targeting

```
position_size = target_vol / realized_vol
```

- Target vol: 10% (annualized) — **provisional, subject to Stage 1.5 validation**
- Realized vol lookback: 40일 — **provisional**
- Vol 낮으면 → 포지션 확대 (trending 구간 활용)
- Vol 높으면 → 포지션 축소 (chaos 구간 회피)

#### Crash Filter: Momentum

```
if momentum(N일) > 0: vol targeting 활성화 (long)
if momentum(N일) ≤ 0: 현금 (vol targeting 비활성화)
```

- Formation: 14일 (현재 최적)
- Momentum의 역할: alpha 생성이 아니라 **vol timing이 하락장에 노출되는 걸 차단**

#### 실험으로 증명된 역할 분담

| 컴포넌트 | 역할 | 단독 Sharpe | 기여 |
|----------|------|------------|------|
| Vol Targeting | 수익 엔진 | 0.314 | Return generation |
| Momentum | Crash filter | 0.030 | MaxDD -68% → -12% |
| Combined | 시너지 | 0.628 | +0.284 시너지 |
| Random + Vol | 대조군 | 0.391 | Momentum이 random보다 나음 확인 |

### 최적 파라미터 (Phase 1 백테스트 결과)

| 파라미터 | 값 | 근거 | 상태 |
|----------|------|------|------|
| Formation Period | 14일 | Grid search 최적 (학술: 크립토 1~4주) | Provisional |
| Vol Lookback | 40일 | Grid search 최적 | **Provisional — OOS gap 큼** |
| Vol Target | 10% annualized | Grid search 최적 | **Provisional — OOS gap 큼** |
| Confirmation | 5일 | Whipsaw 제거 효과 확인 | Adopted |

### Parameter Stability Requirement

**파라미터 승격 기준:** 선택된 파라미터의 ±20% 범위 내 이웃 파라미터들이 해당 파라미터 성과의 70% 이상을 유지해야 한다. 최고점이 아니라 "평평한 고원(plateau)"을 찾아야 한다. 날카로운 peak는 과적합의 징후.

```
예: VolTarget=10%가 Sharpe 0.628이면,
VolTarget=8%와 12%에서 Sharpe ≥ 0.440 (70%)이어야 승격 가능.
```

### 거래비용 문제 — 미해결

**현재 상태:** Vol scaling이 208회 거래를 유발. Fee-adjusted return -49%. **이것이 실전 전환 전 반드시 해결해야 할 최대 과제.**

**다음 실험 (미완):**
- Position change threshold (5/10/15/20%) — 거래 빈도 감소
- Vol scaling + confirmation 결합 — whipsaw + micro-trade 동시 제거
- Adaptive vol lookback — 과적합 감소

---

## 03. Stage 로드맵

### Stage 1 — 전략 검증 ✅ (현재 완료)

- **Universe**: BTC, ETH
- **실험**: 8개 완료, Research Registry에 기록
- **발견**: 전략 정체 확인 (Vol Targeting + Momentum Crash Filter)
- **미완 과제**: 거래비용 최적화 (threshold 실험)

### Stage 1.5 — 거래비용 최적화 (다음)

- **목표**: Fee-adjusted return ≥ 0 달성
- **실험**: Position change threshold + adaptive vol
- **완료 기준**: 동일 데이터에서 fee-adjusted return 양수 + OOS에서도 양수

### Stage 2 — 페이퍼 트레이딩

- **조건**: Stage 1.5 완료
- **Universe**: BTC, ETH (검증된 2종목 유지)
- **기간**: 최소 4주
- **측정**: 백테스트 vs 페이퍼 괴리, execution lag, 실제 슬리피지
- **인프라**: Mac 로컬 + cron

### Stage 3 — 실전 + Universe 확장 검토

- **조건**: Stage 2 승격 기준 충족
- **시작 금액**: $500 (잃어도 되는 돈)
- **Universe 확장**: Core 4~7종목으로 확장 검토 (백테스트 선행)
- **인프라**: Mac → Oracle Cloud Free Tier

### Stage 4 — 도구 확장 (조건부)

- **조건**: Stage 3에서 성과 한계가 데이터로 확인
- **행동**: Binance/Bybit testbed, funding/basis 데이터 수집
- **원칙**: 필요성이 증명될 때만

---

## 04. Stage 승격 조건

### Stage 1.5 → Stage 2

- IF: Fee-adjusted return ≥ 0 (in-sample)
- AND: OOS fee-adjusted return > -5%
- AND: 거래 횟수 < 100/년
- AND: Parameter stability 확인 (plateau 존재)
- → THEN: 페이퍼 트레이딩 시작

### Stage 2 → Stage 3

- IF: 페이퍼 4주 이상 운영
- AND: 백테스트 vs 페이퍼 MaxDD 괴리 < 5%p
- AND: 페이퍼 기간 중 MaxDD < BTC MaxDD × 0.5
- AND: Fee-adjusted return ≥ 0 (실측)
- → THEN: 실전 전환 ($500)

### Stage 3 → Stage 4

- IF: Spot 전략 6개월 운영
- AND: 데이터가 성과 한계를 보여줌 (Calmar 하락 추세 등)
- AND: Funding/basis 데이터에서 유의미한 시그널 발견
- → THEN: Futures testbed 착수

### 안전장치 — 강제 회귀

어느 Stage에서든:
- 실전 MaxDD > -20% → 즉시 전량 현금 + Stage 1로 회귀
- Fee-adjusted return 3개월 연속 음수 → 전략 재검토

---

## 05. KPI 체계

### Primary Metrics (전략의 핵심 가치 측정)

| 지표 | 정의 | 목표 | 왜 primary인가 |
|------|------|------|---------------|
| **MaxDD 방어율** | 1 - (전략 MaxDD / BTC MaxDD) | > 50% | 전략의 존재 이유. 하락장에서 BTC 대비 얼마나 살아남는가. |
| **Calmar Ratio** | 연환산 수익 / \|MaxDD\| | > 0.4 | 수익 대비 최악의 낙폭. Crash protection 전략에 가장 적합한 지표. |
| **Fee-Adjusted Return** | 수수료 차감 후 순수익 | ≥ 0 | 실전 가능성의 최소 조건. 이게 음수면 전략이 아니라 수수료 기부. |

### Secondary Metrics

| 지표 | 정의 | 목표 | 역할 |
|------|------|------|------|
| **Sharpe Ratio** | (수익-무위험)/변동성 × √365 | > 0.3 | 전반적 risk-adjusted 성과. 단, 이 전략의 primary 가치를 정확히 반영 못함. |
| **Sortino Ratio** | (수익-무위험)/하방변동성 × √365 | > 0.5 | 하방 리스크만 벌하는 공정한 지표. |

### Benchmark-Relative Metrics

| 지표 | 정의 | 역할 |
|------|------|------|
| **BTC 초과수익** | 전략 총수익 - BTC 총수익 | 불장에서 음수가 정상. 하락장에서의 양수가 의미 있음. |
| **거래 횟수/연** | 연간 총 거래 수 (< 100 목표) | 수수료 관리. |
| **DD Protection Ratio** | 1 - (전략 MaxDD / BTC MaxDD) | 벤치마크 대비 방어력 직접 측정. |

### 벤치마크 3층 구조

| 벤치마크 | 목적 | 이걸 이기면 증명하는 것 |
|----------|------|----------------------|
| **BTC Buy & Hold** | Primary. 하락장 구간 비교가 핵심. | 전략이 crash protection 가치가 있다 |
| **50/50 BTC-Cash** | 가장 단순한 방어 전략. | Vol targeting이 naïve 방어보다 낫다 |
| **Vol-targeted BTC (no momentum)** | Momentum filter의 순수 기여 측정. | Momentum crash filter가 가치를 더한다 |

---

## 06. 에이전트 아키텍처

### 핵심 원칙

**돈을 지키는 코드 (Signal, Guardian, Operator)는 절대 LLM에 의존하지 않는다.** LLM이 다운돼도 시스템은 어제의 규칙대로 계속 돌아간다.

### Phase 1~2: 에이전트 4명

| 에이전트 | 역할 | 구현 | v3 변경사항 |
|----------|------|------|-----------|
| **Signal** | Vol targeting 계산 + momentum crash filter | Python 95% + LLM 5% | 모멘텀 계산 → vol scaling 계산이 핵심으로 변경 |
| **Guardian** | 리스크 체크, hard limits 강제 | Python 90% + LLM 10% | 변경 없음 |
| **Operator** | 주문 실행 (Paper/Live) | Python 100% | Position change threshold 추가 예정 |
| **Scribe** | 의사결정 기록, 분석, 저널링 | Claude Code CLI 80% + Python 20% | Attribution 분석 추가 (vol vs momentum 기여 분해) |

### Phase 3+: 추가 에이전트

| 에이전트 | 역할 | 추가 조건 |
|----------|------|----------|
| **Watchdog** | 24/7 시장 감시, 급변 탐지 | 실전 전환 시 |
| **Architect** | 메타 분석, regime detection | 3개월+ 데이터 축적 후 |

### 실행 흐름

```
매일 UTC 00:05 (cron):
  1. data.py     → 일봉 데이터 수집
  2. signal.py   → vol targeting + momentum filter 계산
  3. guardian.py  → 리스크 체크 + hard limits
  4. operator.py  → 주문 실행 (threshold 이상 변경만)
  5. scribe.py   → DB 기록

주간 (Claude Code CLI):
  6. claude -p "주간 분석" → attribution 분해 + 패턴 분석
```

---

## 07. 데이터 아키텍처

### 5개 데이터 레이어 (변경 없음)

| 레이어 | 내용 | 가치 |
|--------|------|------|
| L1: Market Data | OHLCV 일봉 | Commodity |
| L2: Feature Store | 모멘텀, vol, regime | Signal 원료 |
| L3: Decision Ledger | 매수/매도 사유, 시그널 값 | **Moat** |
| L4: Research Registry | 실험 기록 (현재 8개 축적) | 메타 학습 |
| L5: Attribution | **vol timing return vs momentum filter return 분해** | v3에서 추가 강조 |

---

## 08. 운영 인프라 & 비용

| 항목 | 서비스 | 비용 |
|------|--------|------|
| 마켓 데이터 | Alpaca Free | $0 |
| 페이퍼/실전 | Alpaca Paper → Live | $0 (수수료 별도) |
| 서버 | Mac → Oracle Cloud Free | $0 |
| DB | SQLite → Supabase Free | $0 |
| LLM | Claude Code CLI (Max Plan) | $0 |
| 알림 | Telegram Bot | $0 |
| **총 월 운영비** | | **$0** |

---

## 09. 리스크 관리

### Hard Limits — 자동 실행

- 포트폴리오 MaxDD > -20% → 전량 현금 (v2의 -25%에서 하향 조정)
- 개별 종목 1일 -15% → 해당 종목 청산
- 단일 종목 비중 > 40% → 리밸런싱

### 알려진 리스크 (실험으로 확인)

| 리스크 | 증거 | 완화 |
|--------|------|------|
| Vol scaling 과적합 | OOS gap 0.553 | Adaptive lookback, 보수적 target |
| 수수료 잠식 | FeeAdj -49% | Position threshold (미완 실험) |
| Regime shift | 2025 OOS Sharpe 하락 | 지속적 OOS 모니터링 |
| Liquidity gap | Alpaca 유동성 한계 | BTC/ETH만 (가장 유동적) |
| Volatility clustering breakdown | Low vol → sudden crash | Momentum filter가 부분적 보호 |

### 실전 진입 원칙

$500으로 시작. 3개월 연속 fee-adjusted return 양수 후에만 증액. Decision Ledger가 증명해야 함.

---

## 10. 운영 원칙 10개

1. **Vol targeting이 수익 엔진, momentum이 crash filter.** 이 역할 분담을 잊지 않는다.
2. **MaxDD 방어가 primary KPI.** Sharpe나 return이 아니라 하락장 생존이 전략의 존재 이유.
3. **데이터로 승격.** 모든 Stage 전환은 사전 정의된 KPI. 감정/의견 불가.
4. **LLM ≠ 실행.** 돈을 지키는 코드에 LLM 의존성 0.
5. **기록이 자산.** 8개 실험이 Research Registry에 있다. 이게 moat의 시작.
6. **수수료 먼저.** Fee-adjusted return이 양수가 아니면 실전 불가.
7. **과적합 경계.** OOS gap 0.553을 잊지 않는다. 파라미터는 plateau여야 승격 가능.
8. **도구 확장은 데이터가 요구할 때만.** Futures/Short은 목표가 아니다.
9. **단순함 유지.** 전략의 핵심은 2개 컴포넌트 (vol targeting + momentum filter). 복잡성 추가는 데이터가 정당화해야.
10. **운영비 $0 고수.** Max 플랜 + 무료 인프라 최대 활용.

---

## 11. 현재 상태 — Stage 2 페이퍼 트레이딩

### Stage 1.5 완료 (실험 9b, 12)

- **Incremental rebalancing** 구현 → 수수료 구조적 해결 (FeeAdj -49% → +6.4%)
- **보수적 파라미터 확정**: F=16d, VLB=45d, VT=8%, Threshold=5%
- **Parameter stability**: VolTarget plateau 확인. Formation/VolLookback에 cliff 존재하나 보수적 방향(높은 값)에서 안정.
- **거래소 전환**: Alpaca → Binance (수수료 40bps → 20bps)

### Stage 2 운영 중

- **인프라**: Binance Testnet + Slack 알림 + cron 자동 실행
- **측정 항목**: 백테스트 vs 실전 괴리, 실측 슬리피지, 실측 수수료, MaxDD 방어력
- **기간**: 최소 4주
- **승격 기준**: MaxDD < BTC MaxDD × 0.5, FeeAdj ≥ 0 (실측), 괴리 < 5%p

---

## 12. Signal Discovery Protocol (Phase 3+)

> evolving.ai "10 Claude Prompts" 리뷰 기반. 프롬프트 → 리포트가 아니라 **프롬프트 → 데이터 파이프라인 → signal feature**로 변환.

### 지금 시작 — Shadow Mode 데이터 수집

실제 signal로 쓰는 건 Phase 3에서. 하지만 **데이터가 없으면 연구 자체가 불가능**하므로 수집은 지금 시작.

| 카테고리 | 데이터 | 소스 | 비용 |
|----------|--------|------|------|
| Macro | Fed funds rate, real yield, DXY, M2 liquidity | FRED | $0 |
| Crypto flow | Exchange inflow/outflow, stablecoin supply | CoinGecko / Glassnode free tier | $0 |

### Phase 3 Signal Candidates

| Priority | Signal | 출처 (프롬프트 변환) | 데이터 | 비고 |
|----------|--------|---------------------|--------|------|
| **P1** | **Macro Regime Enhancement** | McKinsey Macro #10 변환 | real yield + USD liquidity → regime detection 강화 | 지금 momentum crash filter 위에 macro overlay 추가 |
| **P2** | **Vol Breakout Detection** | Citadel Technical #6 변환 | volatility structure + breakout probability | 기존 OHLCV로 가능. crash filter 강화 |
| **P3** | **Flow-Based Signals** | Renaissance Pattern #9 변환 | funding_rate, perp_basis, open_interest_change, liquidation_cascade, exchange_inflow/outflow, stablecoin_supply | Stage 3 Binance 전환 후 접근 가능 |
| **P4** | **On-chain Signals** | — | whale wallet tracking, miner flow, NVT ratio | Shadow mode 데이터 축적 후 백테스트 |

### 크립토에 안 맞는 것 (기록용)

Goldman Screener #1 (P/E 없음), Morgan Stanley DCF #2 (현금흐름 없음), JPMorgan Earnings #4 (어닝 없음), Harvard Dividend #7 (배당 없음) — 크립토에 펀더멘털이 없으므로 부적합.

### 이미 적용된 것

- Bridgewater Risk #3 → Guardian 에이전트 (drawdown management, position sizing, hard limits)
- Renaissance Pattern #9 → 실험 7 signal decomposition (vol timing이 핵심이라는 발견)

---

## 13. Research Registry 요약 (8개 실험)

| # | 실험 | 핵심 발견 | Status |
|---|------|----------|--------|
| 1 | Regime 분리 | 하락장 MaxDD 76%→31%. 전략 존재 이유 확인. | ✅ Insight |
| 2 | Threshold 리밸런싱 | 긴 formation + threshold로 거래 83% 감소. | ✅ Insight |
| 3 | Confirmation Period | F=21d+Confirm=5d: fee-adj 첫 양수 (+0.65%). | ✅ Adopted |
| 4 | 혼합 포트폴리오 | 단순 혼합은 Sharpe 개선 효과 없음. | ✅ Rejected |
| 5 | Vol Scaling | Sharpe 0.207→0.628. 게임 체인저. 수수료 문제 재발. | ✅ Adopted |
| 6 | Vol Scaling 상세 | 최적: F=14d, VolLB=40d, VolTarget=10%. | ✅ Adopted |
| 7 | Signal Decomposition | Alpha = vol timing. Momentum = crash filter. 전략 정체 확인. | ✅ Critical |
| 8 | Out-of-Sample | OOS Sharpe 0.220, MaxDD 방어 유지. Vol 파라미터 과적합 경고. | ✅ Critical |
