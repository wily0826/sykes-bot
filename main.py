"""
主程式入口 — 賽克斯策略
"""
import asyncio
import json
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
_startup_done: bool = False   # ← 啟動保護旗標

# ── 狀態持久化 ─────────────────────────────────────────────
STATE_FILE = "state.json"


def _load_state() -> None:
    """從 state.json 載入上次的狀態，避免重啟後冷卻期與持倉記錄遺失"""
    global _sent_signals, _open_orders
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _sent_signals = data.get("sent_signals", {})
        _open_orders  = data.get("open_orders",  {})
        logger.info(
            f"📂 狀態載入：{len(_sent_signals)} 筆冷卻訊號，"
            f"{len(_open_orders)} 筆持倉記錄"
        )
    except FileNotFoundError:
        logger.info("📂 state.json 不存在，從空白狀態啟動")
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"📂 載入狀態失敗（使用空白狀態）：{e}")


def _save_state() -> None:
    """將目前狀態寫入 state.json"""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"sent_signals": _sent_signals, "open_orders": _open_orders},
                f, indent=2, ensure_ascii=False
            )
    except Exception as e:
        logger.warning(f"📂 儲存狀態失敗：{e}")


def _on_order_placed(symbol: str, timeframe: str, order_id: str) -> None:
    _open_orders[symbol] = timeframe
    _save_state()
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
        if closed:
            _save_state()
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
    global _startup_done

    for symbol in WATCHLIST:
        # 1H 短線
        if _can_open("1H", symbol):
            try:
                klines = bingx.get_klines(symbol, interval="1h", limit=60)
                logger.info(f"📊 1H {symbol} 取得 {len(klines)} 根K線")
                if not klines:
                    continue
                sig = strategy.scan_1h(symbol, klines)
                if sig:
                    key = f"{symbol}_{sig['signal']}_1H"
                    now = time.time()
                    last = _sent_signals.get(key, 0)
                    if now - last > SIGNAL_COOLDOWN:
                        if _startup_done:   # ← 啟動後第一輪不發訊號
                            logger.info(f"📡 短線訊號：{key} {sig['pattern']}")
                            await tg_module.send_signal(sig)
                        _sent_signals[key] = now
                        _save_state()
                    else:
                        logger.info(f"🔇 1H {symbol} 訊號冷卻中：{key}")
                else:
                    logger.info(f"🔍 1H {symbol} 無訊號")
            except Exception as e:
                logger.error(f"短線掃描 {symbol} 錯誤：{e}")

        # 4H 波段
        if _can_open("4H", symbol):
            try:
                klines = bingx.get_klines(symbol, interval="4h", limit=60)
                logger.info(f"📊 4H {symbol} 取得 {len(klines)} 根K線")
                if not klines:
                    continue
                sig = strategy.scan_4h(symbol, klines)
                if sig:
                    key = f"{symbol}_{sig['signal']}_4H"
                    now = time.time()
                    last = _sent_signals.get(key, 0)
                    if now - last > SIGNAL_COOLDOWN:
                        if _startup_done:
                            logger.info(f"📡 波段訊號：{key} {sig['pattern']}")
                            await tg_module.send_signal(sig)
                        _sent_signals[key] = now
                        _save_state()
                    else:
                        logger.info(f"🔇 4H {symbol} 訊號冷卻中：{key}")
                else:
                    logger.info(f"🔍 4H {symbol} 無訊號")
            except Exception as e:
                logger.error(f"波段掃描 {symbol} 錯誤：{e}")

    # 第一輪掃描完畢後設為啟動完成
    _startup_done = True

async def _scan_loop() -> None:
    logger.info(f"🔍 賽克斯策略掃描啟動：{', '.join(WATCHLIST)}")
    while True:
        await _sync_positions()
        await _scan_once()
        await asyncio.sleep(SCAN_INTERVAL_SEC)

async def main() -> None:
    # 1. 載入上次儲存的狀態（冷卻記錄 + 持倉記錄）
    _load_state()

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
            "🟢 First Green Day\n"
            "🚀 Gap and Go\n"
            "🔴 Short the Pump\n"
            "📉 Bounce Failure\n\n"
            f"監控：{' / '.join(WATCHLIST)}\n"
            "停損 5% | 停利 15% | 槓桿 3倍"
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
