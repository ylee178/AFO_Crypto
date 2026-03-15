#!/bin/bash
# Crypto AFO — 일일 전략 실행 + 시스템 헬스체크 + AI 분석 (매일 UTC 00:05)

cd ~/Desktop/AFO_Crypto
source .venv/bin/activate

# 1. 트레이딩 실행 (순수 Python, 항상 성공해야 함)
python main.py --mode paper >> logs/daily.log 2>&1

# 2. AI 인사이트 (트레이딩 + 시스템 상태 + 승격 기준)
# 실패해도 트레이딩에 영향 없음 (PRD 원칙 "LLM ≠ 실행")
if [ -f /tmp/afo_daily_result.json ]; then
    HEALTH=$(python check_system_health.py 2>/dev/null)
    PROMOTION=$(python check_promotion.py 2>/dev/null)

    claude -p "
트레이딩 결과: $(cat /tmp/afo_daily_result.json)

시스템 상태: ${HEALTH}

승격 기준 현황: ${PROMOTION}

당신은 전문 크립토 퀀트 트레이더입니다.
전략: Volatility Targeting (수익 엔진) + Momentum Crash Filter (방어 엔진) + Incremental Rebalancing

아래 3개 섹션으로 분석해주세요. Slack mrkdwn 포맷. 한국어. 각 섹션 3~5줄. 핵심만 간결하게.

*📈 오늘 트레이딩 분석*
- 오늘 포지션 결정이 합리적이었는지 (변동성 수준 vs vol scalar, 모멘텀 상태)
- 현금 비중이 높다면 왜 정상인지 설명
- 주목할 시장 움직임

*⚠️ 시스템 개선 필요*
- 시스템 상태 체크 결과에서 FAIL/WARN 항목을 [P0] [P1] [P2]로 우선순위 매겨서 나열
- kill switch 비활성, drawdown 계산 오류, 데이터 지연 등
- 모두 OK면 '시스템 정상 작동 중' 한 줄

*📌 이번 주 체크*
- 승격 기준 충족 현황 요약 (4개 중 몇 개)
- 다음에 주목할 가격/변동성 레벨
- 승격까지 남은 일수나 조건

*⏰ 다음 스케줄*
- 다음 Crypto AFO 실행: 내일 오전 11:05 AM AEDT
- Guardian 리스크 체크: 4시간마다 자동
- 이 메시지가 매일 오지 않으면 cron 또는 Mac 상태 확인 필요
" 2>/dev/null | python send_insight_to_slack.py >> logs/daily.log 2>&1
fi
