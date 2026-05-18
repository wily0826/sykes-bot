"""
賽克斯策略掃描引擎
偵測以下型態：
  1. First Green Day   — 連跌後首根大量陽線
  2. Volume Breakout   — 爆量突破N日高點
  3. Short the Pump    — 過度拉升後放空機會
"""

import statistics
from config import (
    VOLUME_SPIKE_MULTIPLIER,
    RSI_OVERSOLD,
    RSI_OVERBOUGHT,
    BREAKOUT_LOOKBACK,
)

def _rsi(closes: list, period=14) -> float:
    if len(closes) < period + 2:
        return 50.0
    gains, losses = [], []
    for i in range(len(closes) - period, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 1e-9
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def _avg_volume(klines: list, lookback=20) -> float:
    vols = [k["volume"] for k in klines[-lookback - 1:-1]]
    return statistics.mean(vols) if vols else 1

def scan(symbol: str, klines: list):
    if len(klines) < BREAKOUT_LOOKBACK + 5:
        return None

    # 確認資料完整
    for k in klines:
        if any(v == 0 for v in [k["open"], k["high"], k["low"], k["close"]]):
            return None

    closes  = [k["close"]  for k in klines]
    highs   = [k["high"]   for k in klines]
    lows    = [k["low"]    for k in klines]

    cur  = klines[-1]
    prev = klines[-2]
    rsi  = _rsi(closes)
    avg_vol = _avg_volume(klines)
    vol_ratio = cur["volume"] / avg_vol if avg_vol > 0 else 0

    is_green  = cur["close"] > cur["open"]
    is_red    = cur["close"] < cur["open"]
    vol_spike = vol_ratio >= VOLUME_SPIKE_MULTIPLIER

    recent_highs = highs[-BREAKOUT_LOOKBACK - 1:-1]
    recent_lows  = lows[-BREAKOUT_LOOKBACK - 1:-1]
    if not recent_highs or not recent_lows:
        return None

    recent_high = max(recent_highs)
    recent_low  = min(recent_lows)

    # ── 型態 1：First Green Day ───────────────────────────
    prior_reds = all(
        klines[-i]["close"] < klines[-i]["open"]
        for i in range(2, 5)
    )
    if prior_reds and is_green and vol_spike and rsi < 50:
        return {
            "symbol":     symbol,
            "signal":     "LONG",
            "pattern":    "🟢 First Green Day",
            "reason":     (
                f"連跌後首根大量陽線\n"
                f"成交量 {vol_ratio:.1f}x 均量 | RSI {rsi:.1f}"
            ),
            "confidence": 3 if rsi < RSI_OVERSOLD else 2,
        }

    # ── 型態 2：Volume Breakout ───────────────────────────
    if is_green and vol_spike and cur["close"] > recent_high:
        return {
            "symbol":     symbol,
            "signal":     "LONG",
            "pattern":    "🚀 Volume Breakout",
            "reason":     (
                f"爆量突破 {BREAKOUT_LOOKBACK} 日高點 {recent_high:.4f}\n"
                f"成交量 {vol_ratio:.1f}x 均量 | RSI {rsi:.1f}"
            ),
            "confidence": 3 if vol_ratio > VOLUME_SPIKE_MULTIPLIER * 1.5 else 2,
        }

    # ── 型態 3：Short the Pump ────────────────────────────
    prior_greens = all(
        klines[-i]["close"] > klines[-i]["open"]
        for i in range(2, 5)
    )
    vol_shrink = prev["volume"] > 0 and cur["volume"] < prev["volume"] * 0.6
    if prior_greens and is_red and rsi > RSI_OVERBOUGHT and vol_shrink:
        return {
            "symbol":     symbol,
            "signal":     "SHORT",
            "pattern":    "🔴 Short the Pump",
            "reason":     (
                f"連漲後量縮反轉，動能衰竭\n"
                f"RSI {rsi:.1f} 過熱 | 量縮至前根 {cur['volume']/prev['volume']:.0%}"
            ),
            "confidence": 3 if rsi > 75 else 2,
        }

    return None
