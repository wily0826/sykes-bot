"""
雙週期趨勢策略掃描引擎（放寬版）

波段策略（4H）：
  - EMA20 / EMA50 排列
  - MACD 在正區間（不必剛金叉，更容易觸發）
  - RSI 40~65
  - 成交量 > 1.5x 均量

短線策略（1H）：
  - 布林帶下軌反彈 / 上軌反轉
  - RSI 超賣 < 38 / 超買 > 62（放寬）
  - 成交量 > 2x 均量
  - K線型態確認
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
    """回傳 (當前histogram, 前一根histogram)"""
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
    """回傳 (upper, mid, lower)"""
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
    """波段策略（4H）"""
    if len(klines) < SWING_EMA_SLOW + 15:
        return None

    closes = [k["close"] for k in klines]
    cur    = klines[-1]

    e_fast = _ema(closes, SWING_EMA_FAST)
    e_slow = _ema(closes, SWING_EMA_SLOW)
    if len(e_fast) < 2 or len(e_slow) < 2:
        return None

    hist, hist_prev = _macd_hist(closes)
    if hist is None:
        return None

    r       = _rsi(closes)
    avg_vol = _avg_volume(klines)
    if avg_vol == 0:
        return None
    vol_ratio = cur["volume"] / avg_vol
    vol_ok    = vol_ratio >= SWING_VOL_MIN

    # ── 多單：EMA多頭 + MACD正區間 + RSI合理 + 成交量 ───
    if (e_fast[-1] > e_slow[-1] and
        hist > 0 and                        # MACD 在正區間即可（不必剛金叉）
        SWING_RSI_MIN <= r <= SWING_RSI_MAX and
        vol_ok):
        return {
            "symbol":     symbol,
            "signal":     "LONG",
            "timeframe":  "4H",
            "pattern":    "📈 波段多單",
            "reason":     (
                f"EMA{SWING_EMA_FAST}>{SWING_EMA_SLOW} + MACD多頭\n"
                f"RSI {r:.1f} | 量比 {vol_ratio:.1f}x"
            ),
            "confidence": 3 if (hist > hist_prev and hist_prev > 0) else 2,
        }

    # ── 空單：EMA空頭 + MACD負區間 + RSI合理 + 成交量 ───
    if (e_fast[-1] < e_slow[-1] and
        hist < 0 and                        # MACD 在負區間即可（不必剛死叉）
        (100 - SWING_RSI_MAX) <= r <= (100 - SWING_RSI_MIN) and
        vol_ok):
        return {
            "symbol":     symbol,
            "signal":     "SHORT",
            "timeframe":  "4H",
            "pattern":    "📉 波段空單",
            "reason":     (
                f"EMA{SWING_EMA_FAST}<{SWING_EMA_SLOW} + MACD空頭\n"
                f"RSI {r:.1f} | 量比 {vol_ratio:.1f}x"
            ),
            "confidence": 3 if (hist < hist_prev and hist_prev < 0) else 2,
        }

    return None


def scan_scalp(symbol: str, klines: list) -> dict | None:
    """短線策略（1H）"""
    if len(klines) < SCALP_BB_PERIOD + 5:
        return None

    closes = [k["close"] for k in klines]
    cur    = klines[-1]
    prev   = klines[-2]

    upper, _, lower = _bollinger(closes, SCALP_BB_PERIOD, SCALP_BB_STD)
    upper_p, _, lower_p = _bollinger(closes[:-1], SCALP_BB_PERIOD, SCALP_BB_STD)
    if upper is None or upper_p is None:
        return None

    r       = _rsi(closes)
    avg_vol = _avg_volume(klines)
    if avg_vol == 0:
        return None
    vol_ratio = cur["volume"] / avg_vol
    vol_ok    = vol_ratio >= SCALP_VOL_MIN

    # ── 多單：下軌反彈 + RSI超賣 + 量確認 + 看漲K線 ──────
    near_lower  = cur["low"] <= lower and cur["close"] > lower
    rsi_sold    = r < SCALP_RSI_OVERSOLD   # 放寬至 38
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
    rsi_bought  = r > SCALP_RSI_OVERBOUGHT  # 放寬至 62
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
