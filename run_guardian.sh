#!/bin/bash
# Crypto AFO — Guardian 리스크 체크 (4시간마다)

cd ~/Desktop/AFO_Crypto
source .venv/bin/activate

python main.py --mode guardian >> logs/guardian.log 2>&1
