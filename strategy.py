"""
加密貨幣趨勢型態策略掃描引擎

升級重點：
  1. EMA50 趨勢過濾  — 順勢交易，LONG 只在多頭，SHORT 只在空頭
  2. 區間突破        — 取代 Gap and Go（加密市場適用）
  3. ATR 動態停損    — 根據波動自動計算停損距離（2x ATR）
  4. 資金費率過濾    — 在 main.py 執行，strategy 只負責技術面
  5. 15M 短線掃描    — 更細粒度掃描，訊號不再只出現在整點

五大掃描週期：
  15M 即時短線：
    - First Green Day   → 連跌後首根大量陽線（EMA50 多頭）
    - 區間突破          → 窄幅整理後向上突破（EMA50 多頭）
    - Short the Pump    → 炒作高峰反轉（EMA50 空頭）
    - Bounce Failure    → 反彈失敗再破低（EMA50 空頭）

  1H 短線：（同四型態，參數更寬鬆）

  4H 波段：
    - First Green Day   → 同上，週期更長
    - Short the Pump    → 同上，週期更長

各週期參數差異：
  週期   FGD連跌  FGD量比  整理幅度  STP漲幅  BF_RSI
  15M    3 根     2.0x    <2.5%    2.0%    >52
  1H     2 根     1.5x    <6.0%    3.5%    >50
  4H     2 根     1.5x    —        3.5%    —
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
    """Wilder 平滑 RSI（標準實作，與 TradingView 一致）"""
    if len(closes) < period + 2:
        return 50.0
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    avg_gain = sum(max(c, 0) for c in changes[:period]) / period
    avg_loss = sum(max(-c, 0) for c in changes[:period]) / period
    for change in changes[period:]:
        avg_gain = (avg_gain * (period - 1) + max(change, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0)) / period
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


def _atr(klines: list, period: int = 14) -> float:
    """Average True Range（平均真實波動幅度）"""
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


def _is_valid(klines: list, min_len: int = 30) -> bool:
    if len(klines) < min_len:
        return False
    for k in klines[-5:]:
        if any(v <= 0 for v in [k["open"], k["high"], k["low"], k["close"]]):
            return False
    return True


# ── 型態一：First Green Day ───────────────────────────────

def _first_green_day(
    symbol: str, klines: list, timeframe: str,
    *,
    consec_reds: int   = 2,    # 需要幾根連跌（15M 用 3，1H/4H 用 2）
    vol_min:     float = 1.5,  # 最小量比（15M 用 2.0，1H/4H 用 1.5）
    rsi_max:     float = 68,   # RSI 上限（15M 用 65，1H/4H 用 68）
) -> dict | None:
    """
    First Green Day — 做多
    條件：
      - 在 EMA50 上方（多頭趨勢）
      - 前 N 根連跌 + 當根量能陽線
      - RSI 未超買
    """
    closes = [k["close"] for k in klines]
    cur    = klines[-1]

    # ① 趨勢過濾：EMA50 多頭
    ema50 = _ema(closes, 50)
    if not ema50 or closes[-1] <= ema50[-1]:
        return None

    # ② 當根必須是陽線
    if cur["close"] <= cur["open"]:
        return None

    # ③ 前 N 根連續陰線
    if len(klines) < consec_reds + 2:
        return None
    if not all(klines[-i]["close"] < klines[-i]["open"] for i in range(2, consec_reds + 2)):
        return None

    # ④ 量能確認
    avg_vol   = _avg_volume(klines)
    vol_ratio = cur["volume"] / avg_vol if avg_vol > 0 else 0
    if vol_ratio < vol_min:
        return None

    # ⑤ RSI 未超買
    rsi = _rsi(closes)
    if rsi >= rsi_max:
        return None

    atr = _atr(klines)
    return {
        "symbol":     symbol,
        "signal":     "LONG",
        "timeframe":  timeframe,
        "pattern":    "🟢 First Green Day",
        "reason":     f"連跌後首根大量陽線（EMA50 多頭）\nRSI {rsi:.1f} | 量比 {vol_ratio:.1f}x",
        "confidence": 3 if vol_ratio >= 3.0 else 2,
        "atr_sl":     round(atr * 2.0, 6),
    }


# ── 型態二：區間突破（取代 Gap and Go）───────────────────

def _consolidation_breakout(
    symbol: str, klines: list, timeframe: str,
    *,
    max_range_pct: float = 0.06,   # 最大整理幅度（15M 用 0.025，1H 用 0.06）
    vol_min:       float = 1.5,    # 最小量比（15M 用 2.0，1H 用 1.5）
) -> dict | None:
    """
    區間突破 — 做多（加密貨幣適用）
    條件：
      - 在 EMA50 上方（多頭趨勢）
      - 前 10 根窄幅震盪（幅度 < max_range_pct）
      - 當根向上突破，收盤站穩
      - 量能確認 + RSI < 70
    """
    if len(klines) < 15:
        return None

    closes   = [k["close"] for k in klines]
    cur      = klines[-1]
    lookback = klines[-11:-1]   # 前 10 根（不含當根）

    # ① 趨勢過濾：EMA50 多頭
    ema50 = _ema(closes, 50)
    if not ema50 or closes[-1] <= ema50[-1]:
        return None

    # ② 計算前 10 根震盪區間
    consol_high = max(k["high"] for k in lookback)
    consol_low  = min(k["low"]  for k in lookback)
    if consol_low <= 0:
        return None
    range_pct = (consol_high - consol_low) / consol_low

    # ③ 區間夠窄
    if range_pct > max_range_pct:
        return None

    # ④ 當根突破並收在上方（必須是陽線）
    if cur["close"] <= consol_high or cur["close"] <= cur["open"]:
        return None

    # ⑤ 量能確認
    avg_vol   = _avg_volume(klines)
    vol_ratio = cur["volume"] / avg_vol if avg_vol > 0 else 0
    if vol_ratio < vol_min:
        return None

    # ⑥ RSI 未超買
    rsi = _rsi(closes)
    if rsi >= 70:
        return None

    atr = _atr(klines)
    return {
        "symbol":     symbol,
        "signal":     "LONG",
        "timeframe":  timeframe,
        "pattern":    "💥 區間突破",
        "reason":     f"震盪 {range_pct*100:.1f}% 後向上突破（EMA50 多頭）\nRSI {rsi:.1f} | 量比 {vol_ratio:.1f}x",
        "confidence": 3 if vol_ratio >= 3.0 else 2,
        "atr_sl":     round(atr * 2.0, 6),
    }


# ── 型態三：Short the Pump ───────────────────────────────

def _short_the_pump(
    symbol: str, klines: list, timeframe: str,
    *,
    min_rally_pct: float = 0.035,  # 最小漲幅門檻（15M 用 0.02，1H/4H 用 0.035）
    rsi_min:       float = 62,     # RSI 下限（15M 用 60，1H/4H 用 62）
) -> dict | None:
    """
    Short the Pump — 做空
    條件：
      - 在 EMA50 下方（空頭趨勢）
      - 近 5 根急漲 > min_rally_pct
      - 當根陰線反轉
      - RSI > rsi_min（過熱）
    """
    closes = [k["close"] for k in klines]
    cur    = klines[-1]

    # ① 趨勢過濾：EMA50 空頭
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

    atr = _atr(klines)
    return {
        "symbol":     symbol,
        "signal":     "SHORT",
        "timeframe":  timeframe,
        "pattern":    "🔴 Short the Pump",
        "reason":     f"漲幅 {rally_pct*100:.1f}% 後反轉（EMA50 空頭）\nRSI {rsi:.1f} 過熱 | 量比 {vol_ratio:.1f}x",
        "confidence": 3 if rsi >= 75 else 2,
        "atr_sl":     round(atr * 2.0, 6),
    }


# ── 型態四：Bounce Failure ───────────────────────────────

def _bounce_failure(
    symbol: str, klines: list, timeframe: str,
    *,
    rsi_min: float = 50,   # RSI 下限（15M 用 52，1H 用 50）
    vol_min: float = 0.8,  # 最小量比
) -> dict | None:
    """
    Bounce Failure — 做空
    條件：
      - 在 EMA50 下方（空頭趨勢）
      - 前 2 根有陽線（反彈），但反彈高點未創新高
      - 當根陰線確認失敗
      - RSI > rsi_min（仍偏高）
      - 成交量達門檻（賣壓確認）
    """
    closes = [k["close"] for k in klines]
    cur    = klines[-1]

    # ① 趨勢過濾：EMA50 空頭
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

    atr = _atr(klines)
    return {
        "symbol":     symbol,
        "signal":     "SHORT",
        "timeframe":  timeframe,
        "pattern":    "📉 Bounce Failure",
        "reason":     f"反彈未創新高即反轉（EMA50 空頭）\nRSI {rsi:.1f} | 量比 {vol_ratio:.1f}x",
        "confidence": 3 if vol_ratio >= 1.5 else 2,
        "atr_sl":     round(atr * 2.0, 6),
    }


# ── 主掃描函數 ────────────────────────────────────────────

def scan_15m(symbol: str, klines: list) -> dict | None:
    """
    15 分鐘即時掃描 — 4 型態，條件比 1H 嚴格（避免 15m 雜訊過多）
    FGD: 連跌 3 根，量比 2.0x，RSI < 65
    CB : 整理幅度 < 2.5%，量比 2.0x
    STP: 漲幅 > 2.0%，RSI > 60
    BF : RSI > 52
    """
    if not _is_valid(klines, min_len=30):
        return None
    checks = [
        (_first_green_day,        {"consec_reds": 3, "vol_min": 2.0, "rsi_max": 65}),
        (_consolidation_breakout, {"max_range_pct": 0.025, "vol_min": 2.0}),
        (_short_the_pump,         {"min_rally_pct": 0.02,  "rsi_min": 60}),
        (_bounce_failure,         {"rsi_min": 52, "vol_min": 0.8}),
    ]
    for fn, kw in checks:
        result = fn(symbol, klines, "15M", **kw)
        if result:
            return result
    return None


def scan_1h(symbol: str, klines: list) -> dict | None:
    """
    1H 短線掃描 — 4 型態（條件比 15M 寬鬆，比 4H 細）
    """
    if not _is_valid(klines, min_len=30):
        return None
    checks = [
        (_first_green_day,        {"consec_reds": 2, "vol_min": 1.5, "rsi_max": 68}),
        (_consolidation_breakout, {"max_range_pct": 0.06, "vol_min": 1.5}),
        (_short_the_pump,         {"min_rally_pct": 0.035, "rsi_min": 62}),
        (_bounce_failure,         {"rsi_min": 50, "vol_min": 0.8}),
    ]
    for fn, kw in checks:
        result = fn(symbol, klines, "1H", **kw)
        if result:
            return result
    return None


def scan_4h(symbol: str, klines: list) -> dict | None:
    """
    4H 波段掃描 — 2 型態
    """
    if not _is_valid(klines, min_len=30):
        return None
    checks = [
        (_first_green_day, {"consec_reds": 2, "vol_min": 1.5, "rsi_max": 68}),
        (_short_the_pump,  {"min_rally_pct": 0.035, "rsi_min": 62}),
    ]
    for fn, kw in checks:
        result = fn(symbol, klines, "4H", **kw)
        if result:
            return result
    return None
