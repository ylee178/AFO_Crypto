# ── Universe ──
# 내부 표현: "BTC/USD" 유지 (DB, 시그널 등)
# Binance API 호출 시 to_binance_symbol()로 변환
SYMBOLS = ["BTC/USD", "ETH/USD"]

# ── 전략 파라미터 (Stage 1.5 확정) ──
FORMATION_DAYS = 16
CONFIRMATION_DAYS = 5
VOL_LOOKBACK = 45
VOL_TARGET = 0.08
MAX_VOL_SCALE = 1.5

# ── Incremental Rebalancing ──
POSITION_THRESHOLD = 0.05

# ── 거래비용 (bps) — Binance Spot Tier 1 ──
FEE_MAKER_BPS = 10          # 0.10%
FEE_TAKER_BPS = 10          # 0.10%
FEE_BNB_DISCOUNT_BPS = 7.5  # BNB 결제 시 0.075%
FEE_ROUNDTRIP_BPS = 20      # maker+taker (0.10%+0.10%)

# ── 리스크 Hard Limits ──
MAX_PORTFOLIO_DRAWDOWN = -0.20
MAX_SINGLE_DAY_LOSS = -0.15
MAX_SINGLE_POSITION_WEIGHT = 0.40

# ── 리밸런싱 ──
REBALANCE_TIME_UTC = "00:05"

# ── Stage 승격 기준 ──
STAGE_2_TO_3 = {
    "min_paper_weeks": 4,
    "max_dd_vs_btc_gap": 0.05,
    "dd_protection_ratio": 0.50,
    "min_fee_adj_return": 0.0,
}

# ── DB ──
DB_PATH = "data/crypto_afo.db"

# ── 데이터 수집 ──
DEFAULT_HISTORY_YEARS = 3

# ── Binance ──
# 환경변수: BINANCE_API_KEY, BINANCE_SECRET_KEY
# Testnet: BINANCE_TESTNET=1
BINANCE_TESTNET_URL = "https://testnet.binance.vision"


def to_binance_symbol(symbol: str) -> str:
    """내부 심볼 → Binance 심볼. 'BTC/USD' → 'BTCUSDT'"""
    return symbol.replace("/USD", "USDT").replace("/", "")


def from_binance_symbol(symbol: str) -> str:
    """Binance 심볼 → 내부 심볼. 'BTCUSDT' → 'BTC/USD'"""
    if symbol.endswith("USDT"):
        return symbol[:-4] + "/USD"
    return symbol
