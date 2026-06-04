"""
主程式入口 — 賽克斯策略
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone

# 台灣時區 UTC+8（Railway 伺服器跑 UTC，需明確指定）
TZ_TAIPEI = timezone(timedelta(hours=8))

from config import (
    WATCHLIST, SCAN_INTERVAL_SEC,
    SIGNAL_COOLDOWN_15M, SIGNAL_COOLDOWN_30M, SIGNAL_COOLDOWN_1H, SIGNAL_COOLDOWN_4H,
    MAX_OPEN_ORDERS, MAX_SWING, MAX_SCALP, DAILY_REPORT_HOUR,
    MONITOR_INTERVAL_SEC, MAX_HOLD_HOURS, PROFIT_ALERT_PCT, ALERT_COOLDOWN,
    PARTIAL_CLOSE_AT_TP1, TP1_CLOSE_PCT, TP1_MONITOR_SEC,
)

# 各時間框架冷卻秒數對照表
_COOLDOWN = {
    "15M": SIGNAL_COOLDOWN_15M,
    "30M": SIGNAL_COOLDOWN_30M,
    "1H":  SIGNAL_COOLDOWN_1H,
    "4H":  SIGNAL_COOLDOWN_4H,
}
import bingx_client as bingx
import strategy
import telegram_bot as tg_module

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_sent_signals: dict = {}
_open_orders:  dict = {}   # {symbol: {"tf": "1H", "ts": epoch, "side": "LONG"}}
_startup_done: bool = False
_daily_stats:  dict = {}   # {"date": "YYYY-MM-DD", "signals": 0, "confirmed": 0}
_alert_sent:   dict = {}   # {symbol: epoch}  — 持倉警示去重

STATE_FILE = "state.json"


def _get_tf(entry) -> str:
    """相容舊格式（字串）與新格式（dict）的 timeframe 取值"""
    return entry.get("tf") if isinstance(entry, dict) else entry


# ── 日期工具 ───────────────────────────────────────────────

def _today() -> str:
    return datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d")


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

def _on_order_placed(
    symbol: str, timeframe: str, order_id: str, side: str = "LONG",
    *, entry_price: float = None, tp1: float = None, qty: float = None,
) -> None:
    """下單成功後更新持倉狀態 + 今日確認計數"""
    _open_orders[symbol] = {
        "tf":           timeframe,
        "ts":           time.time(),
        "side":         side,
        "entry":        entry_price,    # 進場價（保本點）
        "tp1":          tp1,            # 部分止盈觸發價（1:1 RR）
        "qty":          qty,            # 下單數量（估算）
        "partial_done": False,          # TP1 是否已觸發
    }
    _reset_daily_stats_if_needed()
    _daily_stats["confirmed"] = _daily_stats.get("confirmed", 0) + 1
    _save_state()
    tp1_str = f" | TP1={tp1}" if tp1 else ""
    logger.info(f"📋 持倉新增：{symbol} ({timeframe} {side}){tp1_str}")


def _can_open(timeframe: str, symbol: str) -> bool:
    if symbol in _open_orders:
        return False
    total  = len(_open_orders)
    swings = sum(1 for v in _open_orders.values() if _get_tf(v) == "4H")
    scalps = sum(1 for v in _open_orders.values() if _get_tf(v) in ("15M", "30M", "1H"))
    if total  >= MAX_OPEN_ORDERS: return False
    if timeframe == "4H" and swings >= MAX_SWING: return False
    if timeframe in ("15M", "30M", "1H") and scalps >= MAX_SCALP: return False
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
        # ── 資金費率（兩個週期共用，只抓一次）────────────────
        funding_rate = 0.0
        try:
            loop = asyncio.get_event_loop()
            funding_rate = await loop.run_in_executor(
                None, bingx.get_funding_rate, symbol
            )
            logger.info(f"💸 {symbol} 資金費率：{funding_rate*100:.4f}%")
        except Exception as e:
            logger.warning(f"取得資金費率失敗 {symbol}：{e}")

        # ── 共用：資金費率過濾輔助函式 ─────────────────────────
        def _funding_suppressed(direction: str) -> bool:
            if direction == "LONG"  and funding_rate > 0.001:
                logger.info(f"💸 {symbol} 資金費率偏高（{funding_rate*100:.4f}%），壓制 LONG")
                return True
            if direction == "SHORT" and funding_rate < -0.0005:
                logger.info(f"💸 {symbol} 資金費率偏低（{funding_rate*100:.4f}%），壓制 SHORT")
                return True
            return False

        # ── 共用：發送訊號輔助函式 ─────────────────────────────
        async def _maybe_send(sig: dict | None, label: str) -> None:
            if not sig or _funding_suppressed(sig["signal"]):
                logger.info(f"🔍 {label} {symbol} 無訊號")
                return
            key      = f"{symbol}_{sig['signal']}_{sig['timeframe']}"
            now      = time.time()
            last     = _sent_signals.get(key, 0)
            cooldown = _COOLDOWN.get(sig["timeframe"], SIGNAL_COOLDOWN_1H)
            if now - last > cooldown:
                if _startup_done:
                    logger.info(f"📡 {label} 訊號：{key} {sig['pattern']}")
                    await tg_module.send_signal(sig)
                    _daily_stats["signals"] = _daily_stats.get("signals", 0) + 1
                _sent_signals[key] = now
                _save_state()
            else:
                logger.info(f"🔇 {label} {symbol} 訊號冷卻中（剩餘 {(last+cooldown-now)/60:.0f} 分）")

        # 15M 即時短線
        if _can_open("15M", symbol):
            try:
                klines = bingx.get_klines(symbol, interval="15m", limit=60)
                logger.info(f"📊 15M {symbol} 取得 {len(klines)} 根K線")
                if klines:
                    await _maybe_send(strategy.scan_15m(symbol, klines), "15M")
            except Exception as e:
                logger.error(f"15M 掃描 {symbol} 錯誤：{e}")

        # 30M 短線
        if _can_open("30M", symbol):
            try:
                klines = bingx.get_klines(symbol, interval="30m", limit=60)
                logger.info(f"📊 30M {symbol} 取得 {len(klines)} 根K線")
                if klines:
                    await _maybe_send(strategy.scan_30m(symbol, klines), "30M")
            except Exception as e:
                logger.error(f"30M 掃描 {symbol} 錯誤：{e}")

        # 1H 短線
        if _can_open("1H", symbol):
            try:
                klines = bingx.get_klines(symbol, interval="1h", limit=60)
                logger.info(f"📊 1H {symbol} 取得 {len(klines)} 根K線")
                if klines:
                    await _maybe_send(strategy.scan_1h(symbol, klines), "1H")
            except Exception as e:
                logger.error(f"短線掃描 {symbol} 錯誤：{e}")

        # 4H 波段
        if _can_open("4H", symbol):
            try:
                klines = bingx.get_klines(symbol, interval="4h", limit=60)
                logger.info(f"📊 4H {symbol} 取得 {len(klines)} 根K線")
                if klines:
                    await _maybe_send(strategy.scan_4h(symbol, klines), "4H")
            except Exception as e:
                logger.error(f"波段掃描 {symbol} 錯誤：{e}")

    _startup_done = True


async def _scan_loop() -> None:
    logger.info(f"🔍 賽克斯策略掃描啟動：{', '.join(WATCHLIST)}")
    while True:
        await _sync_positions()
        await _scan_once()
        await asyncio.sleep(SCAN_INTERVAL_SEC)


# ── TP1 部分止盈 ────────────────────────────────────────────

async def _check_tp1_partial_close() -> None:
    """
    每 TP1_MONITOR_SEC 秒掃描一次，若持倉達到 TP1（1:1 RR）：
      1. 自動平倉 50%（TP1_CLOSE_PCT）
      2. 在進場價設定保本停損
      3. 推播 Telegram 通知
    同一持倉只觸發一次（partial_done flag）。
    """
    if not PARTIAL_CLOSE_AT_TP1:
        return

    # 找出有 tp1 且尚未觸發的持倉記錄
    candidates = {
        sym: info for sym, info in _open_orders.items()
        if isinstance(info, dict)
        and info.get("tp1") is not None
        and not info.get("partial_done", False)
    }
    if not candidates:
        return

    try:
        loop      = asyncio.get_event_loop()
        positions = await loop.run_in_executor(None, bingx.get_positions_detail)
    except Exception as e:
        logger.warning(f"TP1 監控取得持倉失敗：{e}")
        return

    pos_map = {p["symbol"]: p for p in positions}

    for sym, info in candidates.items():
        p = pos_map.get(sym)
        if not p:
            continue   # 持倉已關閉，等 _sync_positions 清理

        side      = info.get("side", "LONG")
        tp1       = info["tp1"]
        entry     = info.get("entry") or p["entry_price"]
        orig_qty  = info.get("qty")  or p["qty"]
        mark      = p["mark_price"]

        # 是否已達到 TP1
        tp1_hit = (side == "LONG"  and mark >= tp1) or \
                  (side == "SHORT" and mark <= tp1)
        if not tp1_hit:
            continue

        logger.info(f"🎯 {sym} TP1 達標！現價 {mark:.4f} | TP1 {tp1:.4f}")

        # ── 計算平倉量（50%）──────────────────────────────────
        decimal  = bingx.get_qty_decimal(sym)
        half_qty = round(orig_qty * TP1_CLOSE_PCT, decimal)
        if half_qty <= 0:
            logger.warning(f"⚠️ {sym} TP1 平倉量 {half_qty} 無效，跳過")
            continue

        # ── 平倉 50% ─────────────────────────────────────────
        try:
            ok = await loop.run_in_executor(
                None, bingx.close_position, sym, side, half_qty
            )
        except Exception as e:
            logger.error(f"TP1 平倉失敗 {sym}：{e}")
            try:
                await tg_module.send_text(
                    f"⚠️ {sym} TP1 自動平倉失敗：{e}\n請手動平倉約 {half_qty} 張"
                )
            except Exception:
                pass
            continue

        if not ok:
            logger.warning(f"⚠️ {sym} TP1 平倉回傳失敗，下次再試")
            continue

        # ── 標記已觸發（防止重複平倉）────────────────────────
        _open_orders[sym]["partial_done"] = True
        _save_state()

        # ── 在進場價設定保本停損 ──────────────────────────────
        be_ok = False
        try:
            be_ok = await loop.run_in_executor(
                None, bingx.place_breakeven_stop, sym, side, half_qty, entry
            )
        except Exception as e:
            logger.error(f"保本停損設定失敗 {sym}：{e}")

        # ── 推播通知 ─────────────────────────────────────────
        direction  = "LONG 🔺" if side == "LONG"  else "SHORT 🔻"
        be_status  = f"✅ 已移至進場價 {entry:.4f}（保本）" \
                     if be_ok else "⚠️ 移動停損失敗，請手動確認"
        text = (
            f"{'━'*22}\n"
            f"🎯 TP1 達標 — 部分止盈！\n"
            f"{'━'*22}\n"
            f"幣對：{sym}  方向：{direction}\n"
            f"TP1：{tp1:.4f}  現價：{mark:.4f}\n\n"
            f"✅ 已平倉 {TP1_CLOSE_PCT*100:.0f}%（{half_qty} 張）\n"
            f"🛡 停損：{be_status}\n\n"
            f"📈 剩餘 {(1-TP1_CLOSE_PCT)*100:.0f}% 繼續等待 TP2\n"
            f"{'━'*22}"
        )
        try:
            await tg_module.send_text(text)
        except Exception as e:
            logger.error(f"TP1 通知失敗：{e}")

        logger.info(
            f"✅ {sym} TP1 部分止盈完成：平倉 {half_qty}，保本停損 {'OK' if be_ok else 'FAIL'}"
        )


async def _tp1_monitor_loop() -> None:
    """每 TP1_MONITOR_SEC 秒掃描 TP1 部分止盈條件"""
    logger.info(f"🎯 TP1 監控啟動（間隔 {TP1_MONITOR_SEC}s）")
    while True:
        await asyncio.sleep(TP1_MONITOR_SEC)
        await _check_tp1_partial_close()


# ── 持倉監控 ───────────────────────────────────────────────

async def _check_position_health() -> None:
    """
    每 15 分鐘檢查所有持倉是否仍在合理範圍：
      1. 持倉超過各週期上限 → 推「持倉過久」警示
      2. 浮動盈利 > PROFIT_ALERT_PCT → 推「建議止盈」警示
    每個持倉同一警示最多每 4 小時發一次。
    """
    try:
        loop      = asyncio.get_event_loop()
        positions = await loop.run_in_executor(None, bingx.get_positions_detail)
    except Exception as e:
        logger.warning(f"持倉監控取得資料失敗：{e}")
        return

    now = time.time()
    for p in positions:
        symbol = p["symbol"]
        entry  = _open_orders.get(symbol, {})
        tf     = _get_tf(entry) or "1H"
        entry_ts = entry.get("ts", 0) if isinstance(entry, dict) else 0

        hold_h    = (now - entry_ts) / 3600 if entry_ts else 0
        margin    = (p["entry_price"] * p["qty"]) / p["leverage"] if p["leverage"] > 0 else 1
        pnl_pct   = p["unrealized_pnl"] / margin if margin > 0 else 0
        max_hold  = MAX_HOLD_HOURS.get(tf, 24)

        # 判斷是否需要警示
        reasons = []
        if entry_ts and hold_h > max_hold:
            reasons.append(f"⏰ 已持倉 {hold_h:.1f} 小時（{tf} 建議上限 {max_hold}h）")
        if pnl_pct > PROFIT_ALERT_PCT:
            pnl_sign = "+" if p["unrealized_pnl"] >= 0 else ""
            reasons.append(
                f"🎯 浮盈 {pnl_pct*100:.1f}%"
                f"（{pnl_sign}{p['unrealized_pnl']:.2f} USDT）已達止盈建議"
            )

        if not reasons:
            continue

        # 去重：同一持倉 4 小時內不重複推
        last_alert = _alert_sent.get(symbol, 0)
        if now - last_alert < ALERT_COOLDOWN:
            continue

        _alert_sent[symbol] = now
        side_label = "LONG 🔺" if p["side"] == "LONG" else "SHORT 🔻"
        text = (
            f"{'━'*20}\n"
            f"⚠️ 持倉健康提醒 — {symbol}\n"
            f"{'━'*20}\n"
            f"方向：{side_label} | 週期：{tf}\n"
            f"進場：{p['entry_price']:.4f}  現價：{p['mark_price']:.4f}\n\n"
            + "\n".join(reasons) +
            f"\n{'━'*20}"
        )
        await tg_module.send_close_alert(symbol, p["side"], p["qty"], text)
        logger.info(f"⚠️ 持倉警示：{symbol} ({', '.join(reasons[:1])})")


async def _monitor_positions_loop() -> None:
    """每 MONITOR_INTERVAL_SEC 秒執行一次持倉健康檢查"""
    logger.info("🔍 持倉監控啟動")
    while True:
        await asyncio.sleep(MONITOR_INTERVAL_SEC)
        await _check_position_health()


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
    """每天 DAILY_REPORT_HOUR 點（台灣時間）自動推播日報"""
    while True:
        now    = datetime.now(TZ_TAIPEI)
        target = now.replace(hour=DAILY_REPORT_HOUR, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait = (target - now).total_seconds()
        logger.info(
            f"📅 日報排程：{wait/3600:.1f} 小時後推播"
            f"（台灣時間 {target.strftime('%m/%d %H:%M')}）"
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
            "📐 型態：\n"
            "  🟢 First Green Day\n"
            "  💥 區間突破\n"
            "  🔴 Short the Pump\n"
            "  📉 Bounce Failure\n\n"
            "⏱ 週期：15M / 1H / 4H\n"
            f"👁 監控：{' / '.join(WATCHLIST)}\n"
            "🛡 EMA50 趨勢過濾 + 資金費率過濾\n"
            "📏 ATR 動態停損（2x ATR）\n\n"
            "⬇️ 使用下方按鈕操作 Bot"
        ),
        reply_markup=tg_module.MAIN_KB,   # ← 上線時就直接顯示鍵盤
    )

    try:
        # 掃描迴圈 + TP1 監控 + 持倉健康 + 日報排程同時執行
        await asyncio.gather(
            _scan_loop(),
            _tp1_monitor_loop(),
            _monitor_positions_loop(),
            _daily_report_loop(),
        )
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
