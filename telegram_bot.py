"""
Telegram Bot 處理器
"""
import logging
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    USDT_PER_TRADE, STOP_LOSS_PCT, TAKE_PROFIT_PCT, LEVERAGE
)
import bingx_client as bingx

logger = logging.getLogger(__name__)

_pending: dict = {}
_app: Application = None
_on_order_placed = None  # callback(symbol, timeframe, order_id)

PENDING_TTL = 900   # 待確認訊號 15 分鐘後自動過期


def _cleanup_pending() -> None:
    """清除超過 15 分鐘的待確認訊號，防止舊訊號被誤觸"""
    now = time.time()
    expired = [k for k, v in _pending.items() if now - v.get("ts", now) > PENDING_TTL]
    for k in expired:
        _pending.pop(k, None)
        logger.info(f"🕐 待確認訊號已過期自動移除：{k}")


async def send_signal(signal: dict) -> None:
    """發送訊號通知到 Telegram"""
    _cleanup_pending()
    try:
        price = bingx.get_ticker(signal["symbol"])
    except Exception as e:
        logger.error(f"取得價格失敗：{e}")
        return

    if signal["signal"] == "LONG":
        sl = round(price * (1 - STOP_LOSS_PCT), 6)
        tp = round(price * (1 + TAKE_PROFIT_PCT), 6)
    else:
        sl = round(price * (1 + STOP_LOSS_PCT), 6)
        tp = round(price * (1 - TAKE_PROFIT_PCT), 6)

    key = f"{signal['symbol']}_{signal['signal']}_{signal['timeframe']}"
    _pending[key] = {**signal, "price": price, "sl": sl, "tp": tp, "ts": time.time()}

    stars     = "⭐" * signal["confidence"]
    direction = "做多 LONG" if signal["signal"] == "LONG" else "做空 SHORT"
    tf_label  = "波段(4H)" if signal["timeframe"] == "4H" else "短線(1H)"

    text = (
        f"{'━'*20}\n"
        f"📡 訊號觸發 — {tf_label}\n"
        f"{'━'*20}\n"
        f"幣對：{signal['symbol']}\n"
        f"方向：{direction}\n"
        f"型態：{signal['pattern']} {stars}\n\n"
        f"📊 {signal['reason']}\n\n"
        f"💰 進場價：{price}\n"
        f"🛑 停損：{sl}（-{STOP_LOSS_PCT*100:.0f}%）\n"
        f"🎯 停利：{tp}（+{TAKE_PROFIT_PCT*100:.0f}%）\n"
        f"💵 下單：{USDT_PER_TRADE} USDT x {LEVERAGE}倍\n"
        f"⏰ 有效期：15 分鐘\n"
        f"{'━'*20}"
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


async def _handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        action, key = query.data.split(":", 1)
    except ValueError:
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

    await query.edit_message_text(f"⏳ 正在下單 {signal['symbol']}...")
    try:
        order_id = bingx.place_order(
            symbol=signal["symbol"],
            side=signal["signal"],
            usdt_amount=USDT_PER_TRADE,
            stop_loss_price=signal["sl"],
            take_profit_price=signal["tp"],
        )
        if order_id:
            if _on_order_placed:
                _on_order_placed(signal["symbol"], signal["timeframe"], order_id)
            await query.edit_message_text(
                f"✅ 下單成功！\n"
                f"幣對：{signal['symbol']}\n"
                f"方向：{signal['signal']}\n"
                f"週期：{signal['timeframe']}\n"
                f"訂單ID：{order_id}"
            )
        else:
            await query.edit_message_text(
                "⚠️ 下單失敗\n請確認帳戶餘額是否足夠"
            )
    except Exception as e:
        logger.error(f"下單錯誤：{e}")
        await query.edit_message_text(f"❌ 下單錯誤：{e}")


async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 幣圈趨勢策略 Bot\n\n"
        "📈 波段策略（4H）：EMA + MACD + RSI + 量\n"
        "⚡ 短線策略（1H）：布林帶 + RSI + K線型態\n\n"
        f"監控：BTC / ETH / SOL\n"
        f"槓桿：{LEVERAGE}倍 | 每單：{USDT_PER_TRADE} USDT\n"
        f"停損：{STOP_LOSS_PCT*100:.0f}% | 停利：{TAKE_PROFIT_PCT*100:.0f}%\n\n"
        "指令：\n"
        "/balance — 查詢帳戶餘額\n"
        "/status  — 查詢 Bot 狀態"
    )


async def _cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        bal = bingx.get_balance()
        await update.message.reply_text(f"💰 帳戶可用餘額：{bal:.2f} USDT")
    except Exception as e:
        await update.message.reply_text(f"❌ 查詢失敗：{e}")


async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from config import WATCHLIST, SCAN_INTERVAL_SEC, MAX_OPEN_ORDERS
    _cleanup_pending()
    pending_count = len(_pending)
    await update.message.reply_text(
        f"🟢 Bot 運行中\n\n"
        f"監控幣對：{', '.join(WATCHLIST)}\n"
        f"掃描間隔：每 {SCAN_INTERVAL_SEC} 秒\n"
        f"持倉上限：{MAX_OPEN_ORDERS} 單\n"
        f"待確認訊號：{pending_count} 個"
    )


def build_app(on_order_placed=None) -> Application:
    global _app, _on_order_placed
    _on_order_placed = on_order_placed
    _app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    _app.add_handler(CommandHandler("start",   _cmd_start))
    _app.add_handler(CommandHandler("balance", _cmd_balance))
    _app.add_handler(CommandHandler("status",  _cmd_status))
    _app.add_handler(CallbackQueryHandler(_handle_callback))
    return _app
