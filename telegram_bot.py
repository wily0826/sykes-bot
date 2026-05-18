"""
Telegram Bot 處理器
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes
)
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, USDT_PER_TRADE, STOP_LOSS_PCT, TAKE_PROFIT_PCT
import bingx_client as bingx

logger = logging.getLogger(__name__)

_pending: dict = {}
_app: Application = None

async def send_signal(signal: dict):
    price = bingx.get_ticker(signal["symbol"])
    if signal["signal"] == "LONG":
        sl_price = round(price * (1 - STOP_LOSS_PCT), 6)
        tp_price = round(price * (1 + TAKE_PROFIT_PCT), 6)
    else:
        sl_price = round(price * (1 + STOP_LOSS_PCT), 6)
        tp_price = round(price * (1 - TAKE_PROFIT_PCT), 6)

    stars = "⭐" * signal["confidence"]
    key = f"{signal['symbol']}_{signal['signal']}"
    _pending[key] = {**signal, "price": price, "sl": sl_price, "tp": tp_price}

    direction = "🟢 做多 LONG" if signal["signal"] == "LONG" else "🔴 做空 SHORT"
    text = (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📡 *賽克斯訊號觸發*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"幣對：`{signal['symbol']}`\n"
        f"方向：{direction}\n"
        f"型態：{signal['pattern']} {stars}\n\n"
        f"📊 {signal['reason']}\n\n"
        f"💰 進場價：`{price}`\n"
        f"🛑 停損：`{sl_price}` \\(-{STOP_LOSS_PCT*100:.0f}%\\)\n"
        f"🎯 停利：`{tp_price}` \\(\\+{TAKE_PROFIT_PCT*100:.0f}%\\)\n"
        f"💵 下單金額：`{USDT_PER_TRADE} USDT`\n"
        f"━━━━━━━━━━━━━━━━━━"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ 確認下單", callback_data=f"confirm:{key}"),
            InlineKeyboardButton("❌ 跳過", callback_data=f"skip:{key}"),
        ]
    ])

    await _app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=text,
        parse_mode="MarkdownV2",
        reply_markup=keyboard,
    )

async def _handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, key = query.data.split(":", 1)
    signal = _pending.pop(key, None)

    if not signal:
        await query.edit_message_text("⚠️ 此訊號已過期或已處理。")
        return

    if action == "skip":
        await query.edit_message_text(f"❌ 已跳過 {signal['symbol']} {signal['signal']} 訊號。")
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
            await query.edit_message_text(
                f"✅ 下單成功！\n"
                f"幣對：{signal['symbol']}\n"
                f"方向：{signal['signal']}\n"
                f"訂單ID：{order_id}"
            )
        else:
            await query.edit_message_text("⚠️ 下單失敗，請檢查帳戶餘額或API設定。")
    except Exception as e:
        logger.error(f"下單錯誤: {e}")
        await query.edit_message_text(f"❌ 下單錯誤：{e}")

async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 賽克斯策略 Bot 已啟動！\n\n"
        "我會自動掃描市場，發現訊號時通知你。\n"
        "你確認後我才會下單，放心！\n\n"
        "指令：\n"
        "/balance - 查詢帳戶餘額\n"
        "/status - 查詢掃描狀態"
    )

async def _cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        bal = bingx.get_balance()
        await update.message.reply_text(f"💰 帳戶可用餘額：{bal:.2f} USDT")
    except Exception as e:
        await update.message.reply_text(f"❌ 查詢失敗：{e}")

async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from config import WATCHLIST, SCAN_INTERVAL_SEC
    await update.message.reply_text(
        f"🟢 Bot 運行中\n\n"
        f"監控幣對：{len(WATCHLIST)} 個\n"
        f"掃描間隔：每 {SCAN_INTERVAL_SEC} 秒\n"
        f"幣對清單：{', '.join(WATCHLIST)}"
    )

def build_app() -> Application:
    global _app
    _app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    _app.add_handler(CommandHandler("start", _cmd_start))
    _app.add_handler(CommandHandler("balance", _cmd_balance))
    _app.add_handler(CommandHandler("status", _cmd_status))
    _app.add_handler(CallbackQueryHandler(_handle_callback))
    return _app
