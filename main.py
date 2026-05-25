"""
主程式入口 — 賽克斯策略
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

_sent_signals: dict = {}
_open_orders:  dict = {}

def _on_order_placed(symbol: str, timeframe: str, order_id: str) -> None:
    _open_orders[symbol] = timeframe
    logger.info(f"📋 持倉新增：{symbol} ({timeframe})")

async def _sync_positions() -> None:
    try:
        open_symbols = await asyncio.get_event_loop().run_in_executor(
            None, bingx.get_open_positions
        )
        closed = [s for s in list(_open_orders) if s not in open_symbols]
        for s in closed:
            del _open_orders[s]
            logger.info(f"📋 持倉移除：{s}")
    except Exception as e:
        logger.warning(f"同步持倉失敗：{e}")

def _can_open(timeframe: str, symbol: str) -> bool:
    if symbol in _open_orders:
        return False
    total  = len(_open_orders)
    swings = sum(1 for tf in _open_orders.values() if tf == "4H")
    scalps = sum(1 for tf in _open_orders.values() if tf == "1H")
    if total  >= MAX_OPEN_ORDERS: return False
    if timeframe == "4H" and swings >= MAX_SWING: return False
    if timeframe == "1H" and scalps >= MAX_SCALP: return False
    return True

async def _scan_once() -> None:
    for symbol in WATCHLIST:
        # ── 1H 短線：四大型態 ─────────────────────────────
        if _can_open("1H", symbol):
            try:
                klines = bingx.get_klines(symbol, interval="1h", limit=60)
                if not klines:
                    logger.warning(f"⚠️ 1H {symbol} 取得 0 根K線")
                    continue
                sig = strategy.scan_1h(symbol, klines)
                if sig:
                    key = f"{symbol}_{sig['signal']}_1H"
                    if time.time() - _sent_signals.get(key, 0) > SIGNAL_COOLDOWN:
                        logger.info(f"📡 短線訊號：{key} {sig['pattern']}")
                        await tg_module.send_signal(sig)
                        _sent_signals[key] = time.time()
            except Exception as e:
                logger.error(f"短線掃描 {symbol} 錯誤：{e}")

        # ── 4H 波段：FGD + Short the Pump ────────────────
        if _can_open("4H", symbol):
            try:
                klines = bingx.get_klines(symbol, interval="4h", limit=60)
                if not klines:
                    logger.warning(f"⚠️ 4H {symbol} 取得 0 根K線")
                    continue
                sig = strategy.scan_4h(symbol, klines)
                if sig:
                    key = f"{symbol}_{sig['signal']}_4H"
                    if time.time() - _sent_signals.get(key, 0) > SIGNAL_COOLDOWN:
                        logger.info(f"📡 波段訊號：{key} {sig['pattern']}")
                        await tg_module.send_signal(sig)
                        _sent_signals[key] = time.time()
            except Exception as e:
                logger.error(f"波段掃描 {symbol} 錯誤：{e}")

async def _scan_loop() -> None:
    logger.info(f"🔍 賽克斯策略掃描啟動：{', '.join(WATCHLIST)}")
    while True:
        await _sync_positions()
        await _scan_once()
        await asyncio.sleep(SCAN_INTERVAL_SEC)

async def main() -> None:
    try:
        app = tg_module.build_app(on_order_placed=_on_order_placed)
    except TypeError:
        app = tg_module.build_app()

    await app.initialize()
    await app.start()
    await asyncio.sleep(3)
    await app.updater.start_polling(drop_pending_updates=True)

    await app.bot.send_message(
        chat_id=tg_module.TELEGRAM_CHAT_ID,
        text=(
            "🚀 賽克斯策略 Bot 已上線！\n\n"
            "🟢 First Green Day — 連跌後爆量陽線做多\n"
            "🚀 Gap and Go — 跳空高開持續做多\n"
            "🔴 Short the Pump — 炒作高峰做空\n"
            "📉 Bounce Failure — 反彈失敗做空\n\n"
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
