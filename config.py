import os

# ============================================================
#  幣圈趨勢策略 Bot — 設定檔
#  Keys 從 Railway Variables 讀取，請勿直接填入
# ============================================================

BINGX_API_KEY      = os.environ.get("BINGX_API_KEY", "")
BINGX_API_SECRET   = os.environ.get("BINGX_API_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "6364030571")

# ── 交易設定 ──────────────────────────────────────────────
USDT_PER_TRADE  = 20     # 每筆下單金額 (USDT)
LEVERAGE        = 3      # 槓桿倍數（3倍風險可控）
STOP_LOSS_PCT   = 0.05   # 停損 5%（含槓桿實際虧損 15%）
TAKE_PROFIT_PCT = 0.15   # 停利 15%（含槓桿實際獲利 45%）
MAX_OPEN_ORDERS = 3      # 最多同時持倉數
MAX_SWING       = 2      # 波段單上限
MAX_SCALP       = 1      # 短線單上限

# ── 監控幣對（只選流動性最好的）──────────────────────────
WATCHLIST = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]

# ── 掃描設定 ──────────────────────────────────────────────
SCAN_INTERVAL_SEC  = 60
SIGNAL_COOLDOWN    = 3600   # 同一訊號 1 小時內不重複

# ── 波段策略參數（4H）────────────────────────────────────
SWING_EMA_FAST       = 20
SWING_EMA_SLOW       = 50
SWING_RSI_MIN        = 40   # RSI 下限（不追超賣反彈）
SWING_RSI_MAX        = 65   # RSI 上限（不追高）
SWING_VOL_MIN        = 1.5  # 最小成交量倍數

# ── 短線策略參數（1H）────────────────────────────────────
SCALP_BB_PERIOD      = 20
SCALP_BB_STD         = 2.0
SCALP_RSI_OVERSOLD   = 32   # 更嚴格的超賣門檻
SCALP_RSI_OVERBOUGHT = 68   # 更嚴格的超買門檻
SCALP_VOL_MIN        = 2.0  # 短線需要更大的量確認
