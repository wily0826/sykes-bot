"""
主程式入口 — 賽克斯策略
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timedelta

from config import (
    WATCHLIST, SCAN_INTERVAL_SEC, SIGNAL_COOLDOWN,
    MAX_OPEN_ORDERS, MAX_SWING, MAX_SCALP, DAILY_REPORT_HOUR
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
_startup_done: bool = False
_daily_stats:  dict = {}   # {"date": "YYYY-MM-DD", "signals": 0, "confirmed": 0}

STATE_FILE = "state.json"


# ── 日期工具 ───────────────────────────────────────────────

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _reset_daily_stats_if_needed() -> None:
    """跨日時自動重置當日統計"""
    global _daily_stats
    if _daily_stats.get("date") != _today():
        _daily_stats = {"date": _today(), "signals": 0, "confirmed": 0}
        _save_state()


# ── 狀態持久化 ─────────────────────────────────────────────

def _load_state() -> None:
    """從 state.json 載入上次的狀態"""
    global _sent_signals, _open_orders, _daily_stats
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _sent_signals = data.get("sent_signals", {})
        _open_orders  = data.get("open_orders",  {})
        _daily_stats  = data.get("daily_stats",  {})
        logger.info(
            f"📂 狀態載入：{len(_sent_signals)} 筆冷卻訊號，"
            f"{len(_open_orders)} 筆持倉記錄"
        )
    except FileNotFoundError:
        logger.info("📂 state.json 不存在，從空白狀態啟動")
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"📂 載入狀態失敗（使用空白狀態）：{e}")
    _reset_daily_stats_if_needed()


def _save_state() -> None:
    """將目前狀態寫入 state.json"""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "sent_signals": _sent_signals,
                    "open_orders":  _open_orders,
                    "daily_stats":  _daily_stats,
                },
                f, indent=2, ensure_ascii=False
            )
    except Exception as e:
        logger.warning(f"📂 儲存狀態失敗：{e}")


def _get_daily_stats() -> dict:
    """供 telegram_bot 查詢當日統計（callback 用）"""
    _reset_daily_stats_if_needed()
    return _daily_stats.copy()


# ── 持倉管理 ───────────────────────────────────────────────

def _on_order_placed(symbol: str, timeframe: str, order_id: str) -> None:
    """下單成功後更新持倉狀態 + 今日確認計數"""
    _open_orders[symbol] = timeframe
    _reset_daily_stats_if_needed()
    _daily_stats["confirmed"] = _daily_stats.get("confirmed", 0) + 1
    _save_state()
    logger.info(f"📋 持倉新增：{symbol} ({timeframe})")


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


# ── 掃描主迴圈 ─────────────────────────────────────────────

async def _scan_once() -> None:
    global _startup_done
    _reset_daily_stats_if_needed()

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
                        if _startup_done:
                            logger.info(f"📡 短線訊號：{key} {sig['pattern']}")
                            await tg_module.send_signal(sig)
                            _daily_stats["signals"] = _daily_stats.get("signals", 0) + 1
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
                            _daily_stats["signals"] = _daily_stats.get("signals", 0) + 1
                        _sent_signals[key] = now
                        _save_state()
                    else:
                        logger.info(f"🔇 4H {symbol} 訊號冷卻中：{key}")
                else:
                    logger.info(f"🔍 4H {symbol} 無訊號")
            except Exception as e:
                logger.error(f"波段掃描 {symbol} 錯誤：{e}")

    _startup_done = True


async def _scan_loop() -> None:
    logger.info(f"🔍 賽克斯策略掃描啟動：{', '.join(WATCHLIST)}")
    while True:
        await _sync_positions()
        await _scan_once()
        await asyncio.sleep(SCAN_INTERVAL_SEC)


# ── 每日報告 ───────────────────────────────────────────────

async def _send_daily_report() -> None:
    """生成並推播每日盈虧報告"""
    try:
        loop      = asyncio.get_event_loop()
        stats     = _get_daily_stats()
        pnl       = await loop.run_in_executor(None, bingx.get_today_pnl)
        balance   = await loop.run_in_executor(None, bingx.get_balance)
        positions = await loop.run_in_executor(None, bingx.get_positions_detail)

        pnl_sign  = "+" if pnl >= 0 else ""
        pnl_emoji = "🟢" if pnl >= 0 else "🔴"

        text = "\n".join([
            f"{'━'*22}",
            f"📅 每日盈虧報告 — {stats['date']}",
            f"{'━'*22}",
            f"{pnl_emoji} 已實現盈虧：{pnl_sign}{pnl:.2f} USDT",
            f"{'━'*22}",
            f"📡 訊號觸發：{stats.get('signals', 0)} 次",
            f"✅ 確認下單：{stats.get('confirmed', 0)} 次",
            f"📋 當前持倉：{len(positions)} 筆",
            f"💰 可用餘額：{balance:.2f} USDT",
            f"{'━'*22}",
        ])
        await tg_module.send_text(text)
        logger.info("📅 每日報告已推播")
    except Exception as e:
        logger.error(f"推播每日報告失敗：{e}")


async def _daily_report_loop() -> None:
    """每天 DAILY_REPORT_HOUR 點自動推播日報"""
    while True:
        now    = datetime.now()
        target = now.replace(hour=DAILY_REPORT_HOUR, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait = (target - now).total_seconds()
        logger.info(
            f"📅 日報排程：{wait/3600:.1f} 小時後推播"
            f"（{target.strftime('%m/%d %H:%M')}）"
        )
        await asyncio.sleep(wait)
        await _send_daily_report()


# ── 啟動入口 ───────────────────────────────────────────────

async def main() -> None:
    # 1. 載入上次儲存的狀態
    _load_state()

    # 2. 建立 Telegram Bot（傳入回呼）
    app = tg_module.build_app(
        on_order_placed=_on_order_placed,
        on_get_stats=_get_daily_stats,
    )

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
            "停損 5% | 停利 15% | 槓桿 3倍\n\n"
            "⬇️ 使用下方按鈕操作 Bot"
        )
    )

    try:
        # 掃描迴圈 + 日報排程同時執行
        await asyncio.gather(
            _scan_loop(),
            _daily_report_loop(),
        )
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
