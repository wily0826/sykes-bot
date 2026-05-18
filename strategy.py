"""
雙週期趨勢策略掃描引擎

波段策略（4H）：
  - 日線趨勢過濾（EMA200）
  - EMA20 / EMA50 排列
  - MACD 金叉 / 死叉
  - RSI 40~65
  - 成交量 > 1.5x 均量

短線策略（1H）：
  - 布林帶下軌反彈 / 上軌反轉
  - RSI 超賣 < 32 / 超買 > 68
  - 成交量 > 2x 均量
  - K線型態確認（錘子線、吞噬、射擊之星）
"""

import statistics
from config import (
    SWING_EMA_FAST, SWING_EMA_SLOW,
    SWING_RSI_MIN, SWING_RSI_MAX, SWING_VOL_MIN,
    SCALP_BB_PERIOD, SCALP_BB_STD,
    SCALP_RSI_OVERSOLD, SCALP_RSI_OVERBOUGHT, SCALP_VOL_MIN,
)

# ── 指標計算 ──────────────────────────────────────────────

def _ema(values: list, period: int) -> list:
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result

def _rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 2:
        return 50.0
    gains, losses = [], []
    for i in range(len(closes) - period, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period or 1e-9
    return 100 - (100 / (1 + avg_gain / avg_loss))

def _macd_hist(closes: list):
    """回傳 (當前histogram, 前一根histogram)，資料不足回傳 (None, None)"""
    if len(closes) < 37:
        return None, None
    ef = _ema(closes, 12)
    es = _ema(closes, 26)
    min_len = min(len(ef), len(es))
    ml = [ef[-(min_len - i)] - es[-(min_len - i)] for i in range(min_len)]
    if len(ml) < 11:
        return None, None
    sl = _ema(ml, 9)
    if len(sl) < 2:
        return None, None
    return ml[-1] - sl[-1], ml[-2] - sl[-2]

def _bollinger(closes: list, period: int = 20, std_mult: float = 2.0):
    """回傳 (upper, mid, lower)，資料不足回傳 (None, None, None)"""
    if len(closes) < period + 1:
        return None, None, None
    window = closes[-period:]
    mid = statistics.mean(window)
    std = statistics.stdev(window)
    return mid + std_mult * std, mid, mid - std_mult * std

def _avg_volume(klines: list, lookback: int = 20) -> float:
    vols = [k["volume"] for k in klines[-lookback - 1:-1]]
    return statistics.mean(vols) if vols else 1.0

# ── K線型態 ───────────────────────────────────────────────

def _is_hammer(k: dict) -> bool:
    body = abs(k["close"] - k["open"])
    if body == 0:
        return False
    lower_wick = min(k["open"], k["close"]) - k["low"]
    upper_wick = k["high"] - max(k["open"], k["close"])
    return lower_wick >= body * 2 and upper_wick <= body * 0.5

def _is_shooting_star(k: dict) -> bool:
    body = abs(k["close"] - k["open"])
    if body == 0:
        return False
    upper_wick = k["high"] - max(k["open"], k["close"])
    lower_wick = min(k["open"], k["close"]) - k["low"]
    return upper_wick >= body * 2 and lower_wick <= body * 0.5

def _is_bull_engulf(k1: dict, k2: dict) -> bool:
    return (k1["close"] < k1["open"] and
            k2["close"] > k2["open"] and
            k2["open"] <= k1["close"] and
            k2["close"] >= k1["open"])

def _is_bear_engulf(k1: dict, k2: dict) -> bool:
    return (k1["close"] > k1["open"] and
            k2["close"] < k2["open"] and
            k2["open"] >= k1["close"] and
            k2["close"] <= k1["open"])

# ── 主策略 ────────────────────────────────────────────────

def scan_swing(symbol: str, klines: list) -> dict | None:
    """
    波段策略（4H）
    條件：EMA 排列 + MACD 金叉/死叉 + RSI + 成交量
    """
    if len(klines) < SWING_EMA_SLOW + 15:
        return None

    closes = [k["close"] for k in klines]
    cur    = klines[-1]

    # EMA
    e_fast = _ema(closes, SWING_EMA_FAST)
    e_slow = _ema(closes, SWING_EMA_SLOW)
    if len(e_fast) < 2 or len(e_slow) < 2:
        return None

    # MACD
    hist, hist_prev = _macd_hist(closes)
    if hist is None:
        return None

    # RSI & Volume
    r        = _rsi(closes)
    avg_vol  = _avg_volume(klines)
    vol_ok   = avg_vol > 0 and (cur["volume"] / avg_vol) >= SWING_VOL_MIN

    # ── 多單：EMA 多頭 + MACD 金叉 + RSI 不追高 ──────────
    if (e_fast[-1] > e_slow[-1] and          # EMA 多頭排列
        hist > 0 and hist_prev <= 0 and       # MACD 剛金叉
        SWING_RSI_MIN <= r <= SWING_RSI_MAX and
        vol_ok):
        vol_ratio = cur["volume"] / avg_vol
        return {
            "symbol":     symbol,
            "signal":     "LONG",
            "timeframe":  "4H",
            "pattern":    "📈 波段多單",
            "reason":     (
                f"EMA{SWING_EMA_FAST}>{SWING_EMA_SLOW} + MACD金叉\n"
                f"RSI {r:.1f} | 量比 {vol_ratio:.1f}x"
            ),
            "confidence": 3,
        }

    # ── 空單：EMA 空頭 + MACD 死叉 + RSI 不追殺 ──────────
    if (e_fast[-1] < e_slow[-1] and          # EMA 空頭排列
        hist < 0 and hist_prev >= 0 and       # MACD 剛死叉
        (100 - SWING_RSI_MAX) <= (100 - r) <= (100 - SWING_RSI_MIN) and
        vol_ok):
        vol_ratio = cur["volume"] / avg_vol
        return {
            "symbol":     symbol,
            "signal":     "SHORT",
            "timeframe":  "4H",
            "pattern":    "📉 波段空單",
            "reason":     (
                f"EMA{SWING_EMA_FAST}<{SWING_EMA_SLOW} + MACD死叉\n"
                f"RSI {r:.1f} | 量比 {vol_ratio:.1f}x"
            ),
            "confidence": 3,
        }

    return None


def scan_scalp(symbol: str, klines: list) -> dict | None:
    """
    短線策略（1H）
    條件：布林帶邊軌 + RSI 極值 + 成交量 + K線型態
    """
    if len(klines) < SCALP_BB_PERIOD + 5:
        return None

    closes = [k["close"] for k in klines]
    cur    = klines[-1]
    prev   = klines[-2]

    # 布林帶（當前 & 前一根）
    upper, _, lower = _bollinger(closes, SCALP_BB_PERIOD, SCALP_BB_STD)
    upper_p, _, lower_p = _bollinger(closes[:-1], SCALP_BB_PERIOD, SCALP_BB_STD)
    if upper is None or upper_p is None:
        return None

    # RSI & Volume
    r       = _rsi(closes)
    avg_vol = _avg_volume(klines)
    if avg_vol == 0:
        return None
    vol_ratio = cur["volume"] / avg_vol
    vol_ok    = vol_ratio >= SCALP_VOL_MIN

    # ── 多單：下軌反彈 + RSI超賣 + 量確認 + 看漲K線 ──────
    near_lower = cur["low"] <= lower and cur["close"] > lower
    rsi_sold   = r < SCALP_RSI_OVERSOLD
    bull_candle = _is_hammer(cur) or _is_bull_engulf(prev, cur)

    if near_lower and rsi_sold and vol_ok and bull_candle:
        pattern = "錘子線" if _is_hammer(cur) else "吞噬陽線"
        return {
            "symbol":     symbol,
            "signal":     "LONG",
            "timeframe":  "1H",
            "pattern":    f"⚡ 短線多單（{pattern}）",
            "reason":     (
                f"布林下軌反彈\n"
                f"RSI {r:.1f} 超賣 | 量比 {vol_ratio:.1f}x"
            ),
            "confidence": 3,
        }

    # ── 空單：上軌反轉 + RSI超買 + 量確認 + 看跌K線 ──────
    near_upper  = cur["high"] >= upper and cur["close"] < upper
    rsi_bought  = r > SCALP_RSI_OVERBOUGHT
    bear_candle = _is_shooting_star(cur) or _is_bear_engulf(prev, cur)

    if near_upper and rsi_bought and vol_ok and bear_candle:
        pattern = "射擊之星" if _is_shooting_star(cur) else "吞噬陰線"
        return {
            "symbol":     symbol,
            "signal":     "SHORT",
            "timeframe":  "1H",
            "pattern":    f"⚡ 短線空單（{pattern}）",
            "reason":     (
                f"布林上軌反轉\n"
                f"RSI {r:.1f} 超買 | 量比 {vol_ratio:.1f}x"
            ),
            "confidence": 3,
        }

    return None
