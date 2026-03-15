"""stdin으로 받은 텍스트를 Slack mrkdwn 포맷으로 전송."""

import os
import sys
from pathlib import Path

# .env 로드
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import requests

webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
if not webhook_url:
    print("SLACK_WEBHOOK_URL not set", file=sys.stderr)
    sys.exit(1)

insight = sys.stdin.read().strip()
if not insight:
    print("No input received", file=sys.stderr)
    sys.exit(0)

message = f":brain: *AI 트레이더 인사이트*\n\n{insight}"

resp = requests.post(
    webhook_url,
    json={
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": message}}
        ]
    },
    timeout=10,
)

if resp.status_code == 200:
    print("Insight sent to Slack")
else:
    print(f"Slack failed: {resp.status_code} {resp.text}", file=sys.stderr)
