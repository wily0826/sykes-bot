"""
賽克斯策略掃描引擎（加密貨幣版）

四大型態：
  1H 短線：
    - First Green Day  → 連跌後首根大量陽線，做多
    - Gap and Go       → 跳空高開持續上漲，做多
    - Short the Pump   → 炒作高峰反轉，做空
    - Bounce Failure   → 反彈失敗再破低，做空

  4H 波段：
    - First Green Day  → 同上，週期更長
    - Short the Pump   → 同上，週期更長
"""

import statistics


# ── 共用指標 ──────────────────────────────────────────────

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


def _avg_volume(klines: list, lookback: int = 20) -> float:
    vols = [k["volume"] for k in klines[-lookback - 1:-1]]
    return statistics.mean(vols) if vols else 1.0


def _is_valid(klines: list, min_len: int = 25) -> bool:
    """基本資料驗證"""
    if len(klines) < min_len:
        return False
    for k in klines[-5:]:
        if any(v <= 0 for v in [k["open"], k["high"], k["low"], k["close"]]):
            return False
    return True


# ── 四大型態 ──────────────────────────────────────────────

def _first_green_day(symbol: str, klines: list, timeframe: str) -> dict | None:
    """
    First Green Day — 做多
    條件：
      - 前 3 根都是陰線
      - 當根是陽線
      - 成交量 > 均量 2 倍
      - RSI < 65（不追高）
    """
    cur    = klines[-1]
    closes = [k["close"] for k in klines]

    if cur["close"] <= cur["open"]:
        return None

    prior_reds = all(
        klines[-i]["close"] < klines[-i]["open"]
        for i in range(2, 5)
    )
    if not prior_reds:
        return None

    avg_vol   = _avg_volume(klines)
    vol_ratio = cur["volume"] / avg_vol if avg_vol > 0 else 0
    if vol_ratio < 2.0:
        return None

    rsi = _rsi(closes)
    if rsi >= 65:
        return None

    return {
        "symbol":     symbol,
        "signal":     "LONG",
        "timeframe":  timeframe,
        "pattern":    "🟢 First Green Day",
        "reason":     f"連跌後首根大量陽線\nRSI {rsi:.1f} | 量比 {vol_ratio:.1f}x",
        "confidence": 3 if vol_ratio >= 3.0 else 2,
    }


def _gap_and_go(symbol: str, klines: list, timeframe: str) -> dict | None:
    """
    Gap and Go — 做多
    條件：
      - 當根開盤比前根收盤高出 1% 以上（跳空）
      - 當根是陽線（持續上漲）
      - 成交量 > 均量 2 倍
      - RSI < 70
    """
    cur    = klines[-1]
    prev   = klines[-2]
    closes = [k["close"] for k in klines]

    if prev["close"] <= 0:
        return None

    gap_pct = (cur["open"] - prev["close"]) / prev["close"]
    if gap_pct < 0.01:
        return None

    if cur["close"] <= cur["open"]:
        return None

    avg_vol   = _avg_volume(klines)
    vol_ratio = cur["volume"] / avg_vol if avg_vol > 0 else 0
    if vol_ratio < 2.0:
        return None

    rsi = _rsi(closes)
    if rsi >= 70:
        return None

    return {
        "symbol":     symbol,
        "signal":     "LONG",
        "timeframe":  timeframe,
        "pattern":    "🚀 Gap and Go",
        "reason":     f"跳空 {gap_pct*100:.1f}% 開盤持續上漲\nRSI {rsi:.1f} | 量比 {vol_ratio:.1f}x",
        "confidence": 3 if gap_pct >= 0.02 else 2,
    }


def _short_the_pump(symbol: str, klines: list, timeframe: str) -> dict | None:
    """
    Short the Pump — 做空
    條件：
      - 前 5 根最高點比 6 根前收盤高出 5% 以上（短期大漲）
      - 當根是陰線（反轉訊號）
      - RSI > 65（過熱）
    """
    cur    = klines[-1]
    closes = [k["close"] for k in klines]

    if cur["close"] >= cur["open"]:
        return None

    if len(klines) < 7:
        return None

    base_price = klines[-7]["close"]
    if base_price <= 0:
        return None

    rally_high = max(k["high"] for k in klines[-6:-1])
    rally_pct  = (rally_high - base_price) / base_price
    if rally_pct < 0.05:
        return None

    rsi = _rsi(closes)
    if rsi <= 65:
        return None

    avg_vol   = _avg_volume(klines)
    vol_ratio = cur["volume"] / avg_vol if avg_vol > 0 else 0

    return {
        "symbol":     symbol,
        "signal":     "SHORT",
        "timeframe":  timeframe,
        "pattern":    "🔴 Short the Pump",
        "reason":     f"漲幅 {rally_pct*100:.1f}% 後反轉陰線\nRSI {rsi:.1f} 過熱 | 量比 {vol_ratio:.1f}x",
        "confidence": 3 if rsi >= 75 else 2,
    }


def _bounce_failure(symbol: str, klines: list, timeframe: str) -> dict | None:
    """
    Bounce Failure — 做空
    條件：
      - 前 2 根有陽線（反彈）
      - 反彈高點未超過近 10 根最高點（失敗）
      - 當根是陰線（反轉）
      - RSI > 50
    """
    cur    = klines[-1]
    closes = [k["close"] for k in klines]

    if cur["close"] >= cur["open"]:
        return None

    if len(klines) < 12:
        return None

    prev_green  = klines[-2]["close"] > klines[-2]["open"]
    prev2_green = klines[-3]["close"] > klines[-3]["open"]
    if not (prev_green or prev2_green):
        return None

    recent_high = max(k["high"] for k in klines[-11:-3])
    bounce_high = max(klines[-2]["high"], klines[-3]["high"])
    if bounce_high >= recent_high:
        return None

    rsi = _rsi(closes)
    if rsi <= 50:
        return None

    avg_vol   = _avg_volume(klines)
    vol_ratio = cur["volume"] / avg_vol if avg_vol > 0 else 0

    return {
        "symbol":     symbol,
        "signal":     "SHORT",
        "timeframe":  timeframe,
        "pattern":    "📉 Bounce Failure",
        "reason":     f"反彈未創新高即反轉\nRSI {rsi:.1f} | 量比 {vol_ratio:.1f}x",
        "confidence": 3 if vol_ratio >= 1.5 else 2,
    }


# ── 主掃描函數 ────────────────────────────────────────────

def scan_1h(symbol: str, klines: list) -> dict | None:
    """1H 短線掃描：四大型態全部檢查"""
    if not _is_valid(klines, min_len=25):
        return None
    for fn in [_first_green_day, _gap_and_go, _short_the_pump, _bounce_failure]:
        result = fn(symbol, klines, "1H")
        if result:
            return result
    return None


def scan_4h(symbol: str, klines: list) -> dict | None:
    """4H 波段掃描：First Green Day + Short the Pump"""
    if not _is_valid(klines, min_len=25):
        return None
    for fn in [_first_green_day, _short_the_pump]:
        result = fn(symbol, klines, "4H")
        if result:
            return result
    return None
