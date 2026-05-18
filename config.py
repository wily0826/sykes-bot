import os

# ============================================================
#  賽克斯策略 Bot — 設定檔
#  所有敏感資訊從環境變數讀取（Railway Variables）
# ============================================================

BINGX_API_KEY    = os.environ.get(WkmH3XFTbcYYRzxwdAzTukB97COu8V8RLNFGrtjP6dvWdn4Hddo9bps4eLXHUTOEosrynYo6atDEmZgCQ", "")
BINGX_API_SECRET = os.environ.get("YzD6GRCzK68Esfy4g3Kiq7tMOPttGoMQDswqn9UWDBPLloDYMgzyCE6XM6CUUN65cjxlyCw4Xgo8RIG2rwVQ", "")

TELEGRAM_BOT_TOKEN = os.environ.get("8687011550:AAFU7N6wRLTCovfkvJRn-n9IoA7J_FyNFvA", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "6364030571")

# ── 交易設定 ──────────────────────────────────────────────
TRADE_TYPE       = "PERPETUAL"   # SPOT=現貨 / PERPETUAL=永續合約
USDT_PER_TRADE   = 20            # 每筆下單金額 (USDT)
LEVERAGE         = 5             # 合約槓桿倍數 (現貨請設 1)
STOP_LOSS_PCT    = 0.03          # 停損 3%
TAKE_PROFIT_PCT  = 0.06          # 停利 6%

# ── 掃描設定 ──────────────────────────────────────────────
SCAN_INTERVAL_SEC = 60           # 每幾秒掃描一次
WATCHLIST = [                    # 要監控的交易對
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "BNB-USDT",
    "DOGE-USDT",
    "XRP-USDT",
    "ADA-USDT",
    "AVAX-USDT",
]

# ── 賽克斯策略參數 ────────────────────────────────────────
VOLUME_SPIKE_MULTIPLIER = 3.0    # 成交量是20日均量幾倍才算爆量
RSI_OVERSOLD            = 35     # RSI 低於此值視為超賣
RSI_OVERBOUGHT          = 65     # RSI 高於此值視為超買
BREAKOUT_LOOKBACK       = 20     # 突破幾日高點
