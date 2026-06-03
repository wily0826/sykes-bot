"""
加密貨幣趨勢型態策略掃描引擎 v3

升級重點：
  1. EMA50 趨勢過濾  — 順勢交易
  2. 區間突破        — 取代 Gap and Go
  3. ATR 動態停損    — 根據品質動態設定 SL 距離
  4. MACD 動能確認   — 新增，判斷入場時機
  5. 訊號品質分級    — S / A+ / A，對應不同風報比與下單量

品質評分（滿分 6 分）：
  MACD 方向吻合   +2
  EMA20 方向吻合  +1
  量比 ≥ 3.0      +2 / ≥ 2.0 +1
  RSI 理想區間    +1

  S ≥ 5 | A+ 3-4 | A 0-2

四大型態 × 四個週期（15M / 30M / 1H / 4H）：
  LONG  — First Green Day、區間突破
  SHORT — Short the Pump、Bounce Failure
"""

import statistics


# ── 指標計算 ──────────────────────────────────────────────

def _ema(values: list, period: int) -> list:
    """指數移動平均（EMA）"""
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _rsi(closes: list, period: int = 14) -> float:
    """Wilder 平滑 RSI（與 TradingView 一致）"""
    if len(closes) < period + 2:
        return 50.0
    changes  = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    avg_gain = sum(max(c, 0) for c in changes[:period]) / period
    avg_loss = sum(max(-c, 0) for c in changes[:period]) / period
    for change in changes[period:]:
        avg_gain = (avg_gain * (period - 1) + max(change, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0)) / period
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


def _macd(closes: list, fast: int = 12, slow: int = 26, sig: int = 9):
    """
    MACD 指標
    返回 (macd_line, signal_line, histogram)
    任一無法計算時返回 (None, None, None)
    """
    if len(closes) < slow + sig + 1:
        return None, None, None
    ema_f    = _ema(closes, fast)
    ema_s    = _ema(closes, slow)
    offset   = len(ema_f) - len(ema_s)
    macd_arr = [f - s for f, s in zip(ema_f[offset:], ema_s)]
    sig_arr  = _ema(macd_arr, sig)
    hist     = macd_arr[-1] - sig_arr[-1]
    return macd_arr[-1], sig_arr[-1], hist


def _atr(klines: list, period: int = 14) -> float:
    """Average True Range"""
    if len(klines) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(klines)):
        h  = klines[i]["high"]
        l  = klines[i]["low"]
        pc = klines[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period


def _avg_volume(klines: list, lookback: int = 20) -> float:
    vols = [k["volume"] for k in klines[-lookback - 1:-1]]
    return statistics.mean(vols) if vols else 1.0


def _is_valid(klines: list, min_len: int = 36) -> bool:
    """最少 36 根（MACD 需要 slow=26 + sig=9 + buffer）"""
    if len(klines) < min_len:
        return False
    for k in klines[-5:]:
        if any(v <= 0 for v in [k["open"], k["high"], k["low"], k["close"]]):
            return False
    return True


# ── 品質評分 ──────────────────────────────────────────────

def _quality(
    closes: list,
    signal_dir: str,
    vol_ratio: float,
    rsi: float,
) -> tuple[str, str]:
    """
    評估訊號品質，返回 (grade, reason_tags)

    計分：
      MACD 吻合  +2
      EMA20 吻合 +1
      量比≥3.0   +2 / ≥2.0 +1
      RSI 理想   +1
    """
    pts  = 0
    tags = []

    # MACD
    ml, sl, hist = _macd(closes)
    if ml is not None and sl is not None:
        if signal_dir == "LONG" and ml > sl:
            pts += 2
            tags.append("MACD✅")
        elif signal_dir == "SHORT" and ml < sl:
            pts += 2
            tags.append("MACD✅")
        else:
            tags.append("MACD❌")
    else:
        tags.append("MACD—")

    # EMA20
    ema20 = _ema(closes, 20)
    if ema20:
        if signal_dir == "LONG" and closes[-1] > ema20[-1]:
            pts += 1
            tags.append("EMA20✅")
        elif signal_dir == "SHORT" and closes[-1] < ema20[-1]:
            pts += 1
            tags.append("EMA20✅")
        else:
            tags.append("EMA20❌")

    # 量比
    if vol_ratio >= 3.0:
        pts += 2
        tags.append(f"量{vol_ratio:.1f}x🔥")
    elif vol_ratio >= 2.0:
        pts += 1
        tags.append(f"量{vol_ratio:.1f}x")
    else:
        tags.append(f"量{vol_ratio:.1f}x")

    # RSI 理想區間
    if signal_dir == "LONG" and 42 <= rsi <= 62:
        pts += 1
        tags.append("RSI理想")
    elif signal_dir == "SHORT" and 63 <= rsi <= 82:
        pts += 1
        tags.append("RSI理想")

    grade = "S" if pts >= 5 else ("A+" if pts >= 3 else "A")
    return grade, " | ".join(tags)


def _make_signal(symbol, signal_dir, timeframe, pattern, base_reason,
                 closes, klines, vol_ratio, rsi) -> dict:
    """統一組裝訊號 dict（含品質評分與 ATR）"""
    atr            = _atr(klines)
    grade, q_tags  = _quality(closes, signal_dir, vol_ratio, rsi)
    return {
        "symbol":     symbol,
        "signal":     signal_dir,
        "timeframe":  timeframe,
        "pattern":    pattern,
        "reason":     f"{base_reason}\nRSI {rsi:.1f} | 量比 {vol_ratio:.1f}x",
        "quality":    grade,
        "q_reason":   q_tags,
        "confidence": {"S": 3, "A+": 2, "A": 1}[grade],
        "atr":        round(atr, 6),
        "atr_sl":     round(atr * 2.0, 6),   # 向後相容
    }


# ── 型態一：First Green Day ───────────────────────────────

def _first_green_day(
    symbol: str, klines: list, timeframe: str,
    *, consec_reds: int = 2, vol_min: float = 1.5, rsi_max: float = 68,
) -> dict | None:
    """連跌後首根大量陽線 — 做多"""
    closes = [k["close"] for k in klines]
    cur    = klines[-1]

    ema50 = _ema(closes, 50)
    if not ema50 or closes[-1] <= ema50[-1]:
        return None
    if cur["close"] <= cur["open"]:
        return None
    if len(klines) < consec_reds + 2:
        return None
    if not all(klines[-i]["close"] < klines[-i]["open"] for i in range(2, consec_reds + 2)):
        return None

    avg_vol   = _avg_volume(klines)
    vol_ratio = cur["volume"] / avg_vol if avg_vol > 0 else 0
    if vol_ratio < vol_min:
        return None

    rsi = _rsi(closes)
    if rsi >= rsi_max:
        return None

    return _make_signal(
        symbol, "LONG", timeframe, "🟢 First Green Day",
        "連跌後首根大量陽線（EMA50 多頭）",
        closes, klines, vol_ratio, rsi,
    )


# ── 型態二：區間突破 ──────────────────────────────────────

def _consolidation_breakout(
    symbol: str, klines: list, timeframe: str,
    *, max_range_pct: float = 0.06, vol_min: float = 1.5,
) -> dict | None:
    """窄幅整理後向上突破 — 做多"""
    if len(klines) < 15:
        return None

    closes   = [k["close"] for k in klines]
    cur      = klines[-1]
    lookback = klines[-11:-1]

    ema50 = _ema(closes, 50)
    if not ema50 or closes[-1] <= ema50[-1]:
        return None

    consol_high = max(k["high"] for k in lookback)
    consol_low  = min(k["low"]  for k in lookback)
    if consol_low <= 0:
        return None
    range_pct = (consol_high - consol_low) / consol_low
    if range_pct > max_range_pct:
        return None
    if cur["close"] <= consol_high or cur["close"] <= cur["open"]:
        return None

    avg_vol   = _avg_volume(klines)
    vol_ratio = cur["volume"] / avg_vol if avg_vol > 0 else 0
    if vol_ratio < vol_min:
        return None

    rsi = _rsi(closes)
    if rsi >= 70:
        return None

    return _make_signal(
        symbol, "LONG", timeframe, "💥 區間突破",
        f"震盪 {range_pct*100:.1f}% 後向上突破（EMA50 多頭）",
        closes, klines, vol_ratio, rsi,
    )


# ── 型態三：Short the Pump ───────────────────────────────

def _short_the_pump(
    symbol: str, klines: list, timeframe: str,
    *, min_rally_pct: float = 0.035, rsi_min: float = 62,
) -> dict | None:
    """急漲後反轉陰線 — 做空"""
    closes = [k["close"] for k in klines]
    cur    = klines[-1]

    ema50 = _ema(closes, 50)
    if not ema50 or closes[-1] >= ema50[-1]:
        return None
    if cur["close"] >= cur["open"]:
        return None
    if len(klines) < 7:
        return None

    base_price = klines[-7]["close"]
    if base_price <= 0:
        return None
    rally_high = max(k["high"] for k in klines[-6:-1])
    rally_pct  = (rally_high - base_price) / base_price
    if rally_pct < min_rally_pct:
        return None

    rsi = _rsi(closes)
    if rsi <= rsi_min:
        return None

    avg_vol   = _avg_volume(klines)
    vol_ratio = cur["volume"] / avg_vol if avg_vol > 0 else 0

    return _make_signal(
        symbol, "SHORT", timeframe, "🔴 Short the Pump",
        f"漲幅 {rally_pct*100:.1f}% 後反轉（EMA50 空頭）",
        closes, klines, vol_ratio, rsi,
    )


# ── 型態四：Bounce Failure ───────────────────────────────

def _bounce_failure(
    symbol: str, klines: list, timeframe: str,
    *, rsi_min: float = 50, vol_min: float = 0.8,
) -> dict | None:
    """反彈未創新高即反轉 — 做空"""
    closes = [k["close"] for k in klines]
    cur    = klines[-1]

    ema50 = _ema(closes, 50)
    if not ema50 or closes[-1] >= ema50[-1]:
        return None
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
    if rsi <= rsi_min:
        return None

    avg_vol   = _avg_volume(klines)
    vol_ratio = cur["volume"] / avg_vol if avg_vol > 0 else 0
    if vol_ratio < vol_min:
        return None

    return _make_signal(
        symbol, "SHORT", timeframe, "📉 Bounce Failure",
        "反彈未創新高即反轉（EMA50 空頭）",
        closes, klines, vol_ratio, rsi,
    )


# ── 主掃描函數 ────────────────────────────────────────────

def _run_checks(symbol, klines, timeframe, checks):
    if not _is_valid(klines, min_len=36):
        return None
    for fn, kw in checks:
        result = fn(symbol, klines, timeframe, **kw)
        if result:
            return result
    return None


def scan_15m(symbol: str, klines: list) -> dict | None:
    """15M — 條件最嚴（雜訊多，門檻高）"""
    return _run_checks(symbol, klines, "15M", [
        (_first_green_day,        {"consec_reds": 3, "vol_min": 2.0, "rsi_max": 65}),
        (_consolidation_breakout, {"max_range_pct": 0.025, "vol_min": 2.0}),
        (_short_the_pump,         {"min_rally_pct": 0.02,  "rsi_min": 60}),
        (_bounce_failure,         {"rsi_min": 52, "vol_min": 0.8}),
    ])


def scan_30m(symbol: str, klines: list) -> dict | None:
    """30M — 中等條件"""
    return _run_checks(symbol, klines, "30M", [
        (_first_green_day,        {"consec_reds": 2, "vol_min": 1.8, "rsi_max": 66}),
        (_consolidation_breakout, {"max_range_pct": 0.04, "vol_min": 1.8}),
        (_short_the_pump,         {"min_rally_pct": 0.025, "rsi_min": 61}),
        (_bounce_failure,         {"rsi_min": 51, "vol_min": 0.8}),
    ])


def scan_1h(symbol: str, klines: list) -> dict | None:
    """1H — 條件較寬"""
    return _run_checks(symbol, klines, "1H", [
        (_first_green_day,        {"consec_reds": 2, "vol_min": 1.5, "rsi_max": 68}),
        (_consolidation_breakout, {"max_range_pct": 0.06, "vol_min": 1.5}),
        (_short_the_pump,         {"min_rally_pct": 0.035, "rsi_min": 62}),
        (_bounce_failure,         {"rsi_min": 50, "vol_min": 0.8}),
    ])


def scan_4h(symbol: str, klines: list) -> dict | None:
    """4H — 波段，只跑兩個型態"""
    return _run_checks(symbol, klines, "4H", [
        (_first_green_day, {"consec_reds": 2, "vol_min": 1.5, "rsi_max": 68}),
        (_short_the_pump,  {"min_rally_pct": 0.035, "rsi_min": 62}),
    ])
