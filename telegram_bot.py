"""
Telegram Bot 處理器
"""
import logging
import time
from telegram import (
    Update,
    InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    USDT_PER_TRADE, STOP_LOSS_PCT, TAKE_PROFIT_PCT, LEVERAGE,
    QUALITY_CONFIG,
)
import bingx_client as bingx

logger = logging.getLogger(__name__)

_pending: dict        = {}
_app: Application     = None
_on_order_placed      = None   # callback(symbol, timeframe, order_id)
_on_get_stats_cb      = None   # callback() -> dict

PENDING_TTL = 900   # 待確認訊號 15 分鐘後自動過期

# ── 持久選單鍵盤 ─────────────────────────────────────────
MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("📋 持倉"), KeyboardButton("💰 餘額")],
        [KeyboardButton("📊 狀態"), KeyboardButton("📅 日報")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

# 按鈕文字 → 處理函式對應
_MENU_MAP = {
    "📋 持倉": "_cmd_positions",
    "💰 餘額": "_cmd_balance",
    "📊 狀態": "_cmd_status",
    "📅 日報": "_cmd_report",
}


# ── 公開 API（供 main.py 呼叫）────────────────────────────

async def send_text(text: str) -> None:
    """推播純文字訊息（供排程日報使用）"""
    await _app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)


async def send_close_alert(symbol: str, side: str, qty: float, text: str) -> None:
    """推播持倉健康警示，附「立即平倉」按鈕"""
    key = f"closepos:{symbol}:{side}:{qty}"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔴 立即平倉", callback_data=key),
        InlineKeyboardButton("⏭ 忽略",     callback_data=f"ignorealert:{symbol}"),
    ]])
    await _app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=text,
        reply_markup=keyboard,
    )


# ── 內部工具 ──────────────────────────────────────────────

def _cleanup_pending() -> None:
    now = time.time()
    expired = [k for k, v in _pending.items() if now - v.get("ts", now) > PENDING_TTL]
    for k in expired:
        _pending.pop(k, None)
        logger.info(f"🕐 待確認訊號已過期：{k}")


# ── 訊號推播 ──────────────────────────────────────────────

async def send_signal(signal: dict) -> None:
    """發送訊號通知到 Telegram（含品質分級、確認/跳過按鈕）"""
    _cleanup_pending()
    try:
        price = bingx.get_ticker(signal["symbol"])
    except Exception as e:
        logger.error(f"取得價格失敗：{e}")
        return

    # ── 品質分級設定 ───────────────────────────────────────
    quality = signal.get("quality", "A")
    qconf   = QUALITY_CONFIG.get(quality, QUALITY_CONFIG["A"])
    atr     = signal.get("atr", signal.get("atr_sl", 0) / 2)   # 向後相容

    # ── 根據品質計算 SL / TP ───────────────────────────────
    if atr and atr > 0 and price > 0:
        sl_dist = atr * qconf["sl_atr"]
        tp_dist = sl_dist * qconf["tp_rr"]
        if signal["signal"] == "LONG":
            sl = round(price - sl_dist, 6)
            tp = round(price + tp_dist, 6)
        else:
            sl = round(price + sl_dist, 6)
            tp = round(price - tp_dist, 6)
        sl_pct = sl_dist / price * 100
        tp_pct = tp_dist / price * 100
        sl_tag = f"ATR×{qconf['sl_atr']}"
        tp_tag = f"1:{qconf['tp_rr']} RR"
    else:
        # fallback：固定百分比
        if signal["signal"] == "LONG":
            sl = round(price * (1 - STOP_LOSS_PCT), 6)
            tp = round(price * (1 + TAKE_PROFIT_PCT), 6)
        else:
            sl = round(price * (1 + STOP_LOSS_PCT), 6)
            tp = round(price * (1 - TAKE_PROFIT_PCT), 6)
        sl_pct = STOP_LOSS_PCT * 100
        tp_pct = TAKE_PROFIT_PCT * 100
        sl_tag = f"{sl_pct:.0f}%"
        tp_tag = f"{tp_pct:.0f}%"

    key = f"{signal['symbol']}_{signal['signal']}_{signal['timeframe']}"
    _pending[key] = {
        **signal, "price": price, "sl": sl, "tp": tp, "ts": time.time(),
        "usdt": qconf["usdt"], "leverage": qconf["leverage"],
    }

    # ── 品質標籤 ───────────────────────────────────────────
    quality_badge = {"S": "🏆 S 級", "A+": "⭐ A+ 級", "A": "📊 A 級"}.get(quality, "📊 A 級")
    tf_map = {"15M": "即時(15M)", "30M": "短線(30M)", "1H": "短線(1H)", "4H": "波段(4H)"}
    tf_label  = tf_map.get(signal["timeframe"], signal["timeframe"])
    direction = "做多 LONG 🔺" if signal["signal"] == "LONG" else "做空 SHORT 🔻"

    text = (
        f"{'━'*22}\n"
        f"📡 {tf_label} 訊號觸發\n"
        f"{'━'*22}\n"
        f"幣對：{signal['symbol']}\n"
        f"方向：{direction}\n"
        f"型態：{signal['pattern']}\n"
        f"品質：{quality_badge}\n"
        f"  └ {signal.get('q_reason', '')}\n\n"
        f"📊 {signal['reason']}\n\n"
        f"💰 進場價：{price}\n"
        f"🛑 停損：{sl}  ({sl_tag} / -{sl_pct:.1f}%)\n"
        f"🎯 停利：{tp}  ({tp_tag} / +{tp_pct:.1f}%)\n"
        f"💵 下單：{qconf['usdt']} USDT × {qconf['leverage']}倍\n"
        f"⏰ 有效期：15 分鐘\n"
        f"{'━'*22}"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 確認下單", callback_data=f"confirm:{key}"),
        InlineKeyboardButton("❌ 跳過",     callback_data=f"skip:{key}"),
    ]])

    await _app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=text,
        reply_markup=keyboard,
    )


# ── Callback 處理（確認/跳過/平倉按鈕）─────────────────

async def _handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data   = query.data
    parts  = data.split(":", 1)
    action = parts[0]

    # ── 忽略持倉警示 ─────────────────────────────────────
    if action == "ignorealert":
        await query.edit_message_text("⏭ 已忽略此次警示")
        return

    # ── 立即平倉（來自持倉健康警示）────────────────────────
    if action == "closepos":
        # callback_data 格式：closepos:BTC-USDT:LONG:0.001
        try:
            _, symbol, side, qty_str = data.split(":")
            qty = float(qty_str)
        except ValueError:
            await query.edit_message_text("❌ 平倉參數解析錯誤")
            return
        await query.edit_message_text(f"⏳ 正在平倉 {symbol} {side}...")
        try:
            ok = bingx.close_position(symbol, side, qty)
            if ok:
                await query.edit_message_text(
                    f"✅ 平倉成功！\n幣對：{symbol}\n方向：{side}\n數量：{qty}"
                )
            else:
                await query.edit_message_text("⚠️ 平倉失敗，請至交易所手動確認")
        except Exception as e:
            logger.error(f"平倉錯誤：{e}")
            await query.edit_message_text(f"❌ 平倉錯誤：{e}")
        return

    # ── 確認 / 跳過訊號（原有邏輯）──────────────────────────
    try:
        key = parts[1]
    except IndexError:
        return

    signal = _pending.pop(key, None)
    if not signal:
        await query.edit_message_text("⚠️ 此訊號已過期或已處理。")
        return

    if action == "skip":
        await query.edit_message_text(
            f"❌ 已跳過\n{signal['symbol']} {signal['signal']} ({signal['timeframe']})"
        )
        return

    # ── 下單前先查餘額 ────────────────────────────────────
    try:
        balance = bingx.get_balance()
        usdt_need = signal.get("usdt", USDT_PER_TRADE)
        if balance < usdt_need:
            await query.edit_message_text(
                f"⚠️ 餘額不足，無法下單\n"
                f"可用：{balance:.2f} USDT | 需要：{usdt_need} USDT\n"
                f"請先入金或減少持倉後重試"
            )
            return
    except Exception as e:
        logger.warning(f"查詢餘額失敗（繼續嘗試下單）：{e}")

    order_usdt = signal.get("usdt", USDT_PER_TRADE)
    order_lev  = signal.get("leverage", LEVERAGE)
    quality    = signal.get("quality", "A")
    await query.edit_message_text(
        f"⏳ 正在下單 {signal['symbol']}（{quality} 級｜{order_usdt} USDT × {order_lev}倍）..."
    )
    try:
        order_id = bingx.place_order(
            symbol=signal["symbol"],
            side=signal["signal"],
            usdt_amount=order_usdt,
            stop_loss_price=signal["sl"],
            take_profit_price=signal["tp"],
            leverage=order_lev,
        )
        if order_id:
            if _on_order_placed:
                _on_order_placed(
                    signal["symbol"], signal["timeframe"],
                    order_id, signal.get("signal", "LONG"),
                )
            await query.edit_message_text(
                f"✅ 下單成功！\n"
                f"幣對：{signal['symbol']}\n"
                f"方向：{signal['signal']}\n"
                f"品質：{quality} 級\n"
                f"下單：{order_usdt} USDT × {order_lev}倍\n"
                f"訂單ID：{order_id}"
            )
        else:
            await query.edit_message_text("⚠️ 下單失敗\n請確認帳戶餘額是否足夠")
    except Exception as e:
        logger.error(f"下單錯誤：{e}")
        await query.edit_message_text(f"❌ 下單錯誤：{e}")


# ── 選單按鈕文字處理 ─────────────────────────────────────

async def _handle_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理下方持久按鈕的文字點擊"""
    text = update.message.text
    if   text == "📋 持倉": await _cmd_positions(update, context)
    elif text == "💰 餘額": await _cmd_balance(update, context)
    elif text == "📊 狀態": await _cmd_status(update, context)
    elif text == "📅 日報": await _cmd_report(update, context)


# ── 指令處理 ──────────────────────────────────────────────

async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from config import WATCHLIST
    pairs = " / ".join(s.replace("-USDT", "") for s in WATCHLIST)
    await update.message.reply_text(
        "🤖 幣圈趨勢策略 Bot\n\n"
        "📈 波段策略（4H）：EMA + MACD + RSI + 量\n"
        "⚡ 短線策略（1H）：布林帶 + RSI + K線型態\n\n"
        f"監控：{pairs}\n"
        f"槓桿：{LEVERAGE}倍 | 每單：{USDT_PER_TRADE} USDT\n"
        f"停損：{STOP_LOSS_PCT*100:.0f}% | 停利：{TAKE_PROFIT_PCT*100:.0f}%\n\n"
        "⬇️ 使用下方按鈕操作",
        reply_markup=MAIN_KB,
    )


async def _cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """查詢當前持倉與浮動盈虧"""
    try:
        positions = bingx.get_positions_detail()
    except Exception as e:
        await update.message.reply_text(f"❌ 查詢失敗：{e}")
        return

    if not positions:
        await update.message.reply_text("📋 目前沒有持倉")
        return

    lines     = [f"{'━'*22}", "📋 當前持倉", f"{'━'*22}"]
    total_pnl = 0.0

    for p in positions:
        pnl        = p["unrealized_pnl"]
        total_pnl += pnl
        pnl_sign   = "+" if pnl >= 0 else ""
        pnl_emoji  = "🟢" if pnl >= 0 else "🔴"
        side_label = "LONG  🔺" if p["side"] == "LONG" else "SHORT 🔻"
        margin     = (p["entry_price"] * p["qty"]) / p["leverage"] if p["leverage"] > 0 else 0
        pct        = (pnl / margin * 100) if margin > 0 else 0
        pct_sign   = "+" if pct >= 0 else ""

        lines += [
            f"\n{pnl_emoji} {p['symbol']}  {side_label}",
            f"  數量：{p['qty']}  |  槓桿：{p['leverage']}x",
            f"  進場：{p['entry_price']:.4f}  →  現價：{p['mark_price']:.4f}",
            f"  浮動盈虧：{pnl_sign}{pnl:.2f} USDT（{pct_sign}{pct:.1f}%）",
        ]

    total_sign  = "+" if total_pnl >= 0 else ""
    total_emoji = "🟢" if total_pnl >= 0 else "🔴"
    lines += [
        f"\n{'━'*22}",
        f"{total_emoji} 合計浮動盈虧：{total_sign}{total_pnl:.2f} USDT",
    ]
    await update.message.reply_text("\n".join(lines))


async def _cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        bal = bingx.get_balance()
        await update.message.reply_text(f"💰 帳戶可用餘額：{bal:.2f} USDT")
    except Exception as e:
        await update.message.reply_text(f"❌ 查詢失敗：{e}")


async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from config import WATCHLIST, SCAN_INTERVAL_SEC, MAX_OPEN_ORDERS
    _cleanup_pending()
    await update.message.reply_text(
        f"🟢 Bot 運行中\n\n"
        f"監控幣對：{', '.join(WATCHLIST)}\n"
        f"掃描間隔：每 {SCAN_INTERVAL_SEC} 秒\n"
        f"持倉上限：{MAX_OPEN_ORDERS} 單\n"
        f"待確認訊號：{len(_pending)} 個"
    )


async def _cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """手動觸發今日盈虧報告"""
    try:
        pnl       = bingx.get_today_pnl()
        balance   = bingx.get_balance()
        positions = bingx.get_positions_detail()
        stats     = _on_get_stats_cb() if _on_get_stats_cb else {}

        pnl_sign  = "+" if pnl >= 0 else ""
        pnl_emoji = "🟢" if pnl >= 0 else "🔴"

        text = "\n".join([
            f"{'━'*22}",
            f"📅 今日盈虧報告 — {stats.get('date', '—')}",
            f"{'━'*22}",
            f"{pnl_emoji} 已實現盈虧：{pnl_sign}{pnl:.2f} USDT",
            f"{'━'*22}",
            f"📡 訊號觸發：{stats.get('signals', 0)} 次",
            f"✅ 確認下單：{stats.get('confirmed', 0)} 次",
            f"📋 當前持倉：{len(positions)} 筆",
            f"💰 可用餘額：{balance:.2f} USDT",
            f"{'━'*22}",
        ])
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ 查詢失敗：{e}")


# ── 建立 App ──────────────────────────────────────────────

def build_app(on_order_placed=None, on_get_stats=None) -> Application:
    global _app, _on_order_placed, _on_get_stats_cb
    _on_order_placed = on_order_placed
    _on_get_stats_cb = on_get_stats

    _app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # 指令處理器
    _app.add_handler(CommandHandler("start",     _cmd_start))
    _app.add_handler(CommandHandler("positions", _cmd_positions))
    _app.add_handler(CommandHandler("balance",   _cmd_balance))
    _app.add_handler(CommandHandler("status",    _cmd_status))
    _app.add_handler(CommandHandler("report",    _cmd_report))

    # 下方持久按鈕（文字訊息）— 必須在 CommandHandler 後面
    _app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        _handle_menu_button,
    ))

    # Inline 按鈕回呼（確認/跳過）
    _app.add_handler(CallbackQueryHandler(_handle_callback))

    return _app
