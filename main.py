"""
主程式入口
- 啟動掃描循環（背景執行緒）
- 啟動 Telegram Bot（主執行緒）
"""

import asyncio
import logging
import time
import threading

from config import WATCHLIST, SCAN_INTERVAL_SEC
import bingx_client as bingx
import strategy
import telegram_bot as tg_module

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# 防止同一訊號短時間內重複發送
_sent_signals: dict = {}   # {symbol_signal: timestamp}
SIGNAL_COOLDOWN = 3600     # 同一訊號 1 小時內不重複發送

async def _scan_loop(app):
    """持續掃描所有監控幣對"""
    logger.info("🔍 掃描引擎啟動，監控幣對：" + ", ".join(WATCHLIST))
    while True:
        for symbol in WATCHLIST:
            try:
                klines = bingx.get_klines(symbol, interval="1h", limit=60)
                signal = strategy.scan(symbol, klines)
                if signal:
                    key = f"{symbol}_{signal['signal']}"
                    last_sent = _sent_signals.get(key, 0)
                    if time.time() - last_sent > SIGNAL_COOLDOWN:
                        logger.info(f"📡 訊號觸發：{key} — {signal['pattern']}")
                        await tg_module.send_signal(signal)
                        _sent_signals[key] = time.time()
            except Exception as e:
                logger.error(f"掃描 {symbol} 時發生錯誤：{e}")

        await asyncio.sleep(SCAN_INTERVAL_SEC)

async def main():
    app = tg_module.build_app()
    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        # 傳送啟動通知
        await app.bot.send_message(
            chat_id=tg_module.TELEGRAM_CHAT_ID,
            text=(
                "🚀 *賽克斯策略 Bot 已上線！*\n\n"
                f"監控幣對：{len(WATCHLIST)} 個\n"
                f"掃描間隔：每 {SCAN_INTERVAL_SEC} 秒\n\n"
                "發現訊號時我會立即通知你 👋\n"
                "輸入 /balance 可查詢帳戶餘額"
            ),
            parse_mode="Markdown"
        )

        # 啟動掃描
        await _scan_loop(app)

if __name__ == "__main__":
    asyncio.run(main())
