"""
主程式入口 — 雙週期趨勢策略
"""
import asyncio
import logging
import time

from config import (
    WATCHLIST, SCAN_INTERVAL_SEC, SIGNAL_COOLDOWN,
    MAX_OPEN_ORDERS, MAX_SWING, MAX_SCALP
)
import bingx_client as bingx
import strategy
import telegram_bot as tg_module

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_sent_signals: dict = {}   # {key: timestamp}
_open_orders:  dict = {}   # {key: timeframe}

def _on_order_placed(symbol: str, timeframe: str, order_id: str) -> None:
    key = f"{symbol}_{order_id}"
    _open_orders[key] = timeframe
    logger.info(f"📋 持倉新增：{key} ({timeframe})")

def _can_open(timeframe: str, symbol: str) -> bool:
    """檢查是否可以開新單"""
    # 同一幣對已有持倉就不開
    for k in _open_orders:
        if symbol in k:
            return False
    total  = len(_open_orders)
    swings = sum(1 for tf in _open_orders.values() if tf == "4H")
    scalps = sum(1 for tf in _open_orders.values() if tf == "1H")
    if total  >= MAX_OPEN_ORDERS: return False
    if timeframe == "4H" and swings >= MAX_SWING: return False
    if timeframe == "1H" and scalps >= MAX_SCALP: return False
    return True

async def _scan_once() -> None:
    """掃描一輪所有幣對"""
    for symbol in WATCHLIST:
        # 波段策略（4H）
        if _can_open("4H", symbol):
            try:
                klines = bingx.get_klines(symbol, interval="4h", limit=80)
                sig = strategy.scan_swing(symbol, klines)
                if sig:
                    key = f"{symbol}_{sig['signal']}_4H"
                    if time.time() - _sent_signals.get(key, 0) > SIGNAL_COOLDOWN:
                        logger.info(f"📡 波段訊號：{key}")
                        await tg_module.send_signal(sig)
                        _sent_signals[key] = time.time()
            except Exception as e:
                logger.error(f"波段掃描 {symbol} 錯誤：{e}")

        # 短線策略（1H）
        if _can_open("1H", symbol):
            try:
                klines = bingx.get_klines(symbol, interval="1h", limit=60)
                sig = strategy.scan_scalp(symbol, klines)
                if sig:
                    key = f"{symbol}_{sig['signal']}_1H"
                    if time.time() - _sent_signals.get(key, 0) > SIGNAL_COOLDOWN:
                        logger.info(f"📡 短線訊號：{key}")
                        await tg_module.send_signal(sig)
                        _sent_signals[key] = time.time()
            except Exception as e:
                logger.error(f"短線掃描 {symbol} 錯誤：{e}")

async def _scan_loop() -> None:
    logger.info(f"🔍 掃描啟動：{', '.join(WATCHLIST)}")
    while True:
        await _scan_once()
        await asyncio.sleep(SCAN_INTERVAL_SEC)

async def main() -> None:
    app = tg_module.build_app(on_order_placed=_on_order_placed)

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    await app.bot.send_message(
        chat_id=tg_module.TELEGRAM_CHAT_ID,
        text=(
            "🚀 幣圈趨勢策略 Bot 已上線！\n\n"
            "📈 波段策略（4H）：EMA + MACD + RSI\n"
            "⚡ 短線策略（1H）：布林帶 + RSI + K線\n\n"
            f"監控：{' / '.join(WATCHLIST)}\n"
            "停損 5% | 停利 15% | 槓桿 3倍\n\n"
            "輸入 /status 查看狀態"
        )
    )

    try:
        await _scan_loop()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
