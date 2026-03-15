# CLAUDE.md — Crypto AFO 개발 규칙

## 프로젝트 개요
- **전략**: Volatility Targeting (수익 엔진) + Momentum Crash Filter (방어 엔진)
- **파라미터**: F=16d, VLB=45d, VT=8%, Threshold=5%
- **현재 단계**: Stage 2 Paper Trading (Binance Testnet)
- **PRD**: `Docs/crypto-afo-prd-v3.md`
- **DEVSPEC**: `Docs/crypto-afo-devspec-v2.md`

## 배포 전 필수 검증 (모든 변경에 적용)

### 1. 스펙 일치 확인
코드 변경 후 반드시 PRD와 DEVSPEC를 다시 읽고:
- 변경사항이 PRD의 운영 원칙에 위배되지 않는지
- DEVSPEC의 에이전트 명세(signal/guardian/operator/scribe)와 일치하는지
- Stage 승격 기준 4개가 코드에 정확히 반영돼있는지
- "LLM ≠ 실행" 원칙: 트레이딩 크리티컬 패스에 LLM이 들어가지 않는지

### 2. 테스트 실행
모든 변경 후:
```bash
python -m pytest tests/ -v
```
- 76개 이상 테스트 전부 통과해야 함
- 새 기능 추가 시 테스트도 같이 추가

### 3. End-to-End 검증
새 기능이나 버그 수정 후:
```bash
# 1) simulate 모드로 전체 파이프라인
python main.py --mode simulate

# 2) paper 모드로 실제 Testnet 주문 체결 확인
python main.py --mode paper --dry-run  # 시그널만 확인
python main.py --mode paper            # 실제 주문

# 3) DB 기록 확인
python -c "from core.db import get_connection; conn = get_connection(); print(conn.execute('SELECT * FROM executions ORDER BY id DESC LIMIT 3').fetchall())"

# 4) 시스템 건강 상태
python check_system_health.py
python check_promotion.py
```

### 4. 안전장치 검증
안전 관련 코드 변경 시:
- kill switch가 실제로 발동하는지 테스트 데이터로 확인
- `get_portfolio_state()`의 total_value가 현재 가격 기반으로 계산되는지 (FIX #8)
- drawdown이 0.0 고정이 아닌지 확인
- Guardian이 4시간마다 실행되고 있는지 (`logs/guardian.log` mtime)

### 5. Silent Fallback 금지
- API 실패 시 조용히 simulate로 넘어가지 않는다
- 에러는 명시적으로 `raise`하고 Slack CRITICAL 전송
- "작동하는 것처럼 보이지만 실제론 다른 모드" 절대 불가
- paper 모드 체결 기록의 `order_type`이 반드시 `MARKET`이어야 함 (`SIMULATED`이면 실제 Testnet 주문 아님)

### 6. 변경 보고 형식
모든 작업 완료 시 반드시 포함:
- 뭘 바꿨는지 (파일, 함수, 로직)
- 어떻게 검증했는지 (실행 로그 첨부)
- 잔존 리스크가 뭔지
- PRD/DEVSPEC 업데이트가 필요한지

## Stage 2 승격 기준 (전부 충족해야 Stage 3)
1. 페이퍼 4주 이상 운영
2. MaxDD < BTC MaxDD × 0.5
3. Fee-adjusted return ≥ 0 (실측)
4. 백테스트 vs 페이퍼 괴리 < 5%p

## Stage 3 전환 시 필수 스모크 테스트
`testnet=True` → `testnet=False` 전환은 코드 한 줄이지만 진짜 돈이 움직인다.
전환 전 반드시:
1. live 키로 Binance 연결 테스트 (잔고 조회만)
2. `--mode live --dry-run`으로 시그널 확인 (주문 안 넣음)
3. 최소 금액($10)으로 수동 주문 1건 체결 확인
4. `order_type=MARKET`, `status=FILLED` DB 기록 확인
5. Slack CRITICAL이 아닌 INFO 리포트 수신 확인
6. 이 모든 과정을 CLAUDE.md 검증 보고서 형식으로 기록

## 핵심 FIX 히스토리 (절대 되돌리지 말 것)
- **FIX #1**: positions_json은 비중(weight) 저장. 수량이 아님.
- **FIX #2**: paper 모드 API 키 없으면 RuntimeError. silent fallback 금지.
- **FIX #4**: fee_adjusted_return은 notional 기반 (`fee_adjusted_return_notional`). count 기반은 레거시.
- **FIX #6**: 매도 먼저 → 매수 나중. main.py에서 sells/buys 분리 실행.
- **FIX #8**: total_value는 현재 시장 가격 기반 재계산. 스냅샷 값 그대로 쓰지 않음.

## 코드 구조
```
agents/         # signal, guardian, operator, scribe (100% 코드, LLM 없음)
analysis/       # metrics, attribution, benchmark
core/           # config, data, db, models
tests/          # pytest 기반 (conftest.py에 tmp_db, seeded_db fixture)
Docs/           # PRD v3, DEVSPEC v2
```

## cron 스케줄
```
5 0 * * *    run_daily.sh          # UTC 00:05 일일 실행
0 */4 * * *  run_guardian.sh        # 4시간마다 리스크 체크
```
