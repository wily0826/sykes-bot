"""
BingX API 客戶端
簽名方式完全參照 BingX 官方 Python 範例
"""
import hmac
import hashlib
import time
import logging
import requests
from config import BINGX_API_KEY, BINGX_API_SECRET, LEVERAGE

BASE_URL = "https://open-api.bingx.com"
logger   = logging.getLogger(__name__)

# 各幣對最小下單量與精度（BingX 永續合約規格）
_MIN_QTY: dict = {
    "BTC-USDT":  0.001,
    "ETH-USDT":  0.01,
    "SOL-USDT":  0.1,
    "BNB-USDT":  0.01,
    "XRP-USDT":  1.0,
    "AVAX-USDT": 0.1,
}
_QTY_DECIMAL: dict = {
    "BTC-USDT":  3,
    "ETH-USDT":  2,
    "SOL-USDT":  1,
    "BNB-USDT":  2,
    "XRP-USDT":  0,
    "AVAX-USDT": 1,
}


def _parse_param(params: dict) -> str:
    sorted_keys = sorted(params)
    return "&".join(f"{k}={params[k]}" for k in sorted_keys)


def _get_sign(payload: str) -> str:
    return hmac.new(
        BINGX_API_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _headers() -> dict:
    return {"X-BX-APIKEY": BINGX_API_KEY}


def _send(method: str, path: str, params: dict) -> dict:
    params = dict(params)
    params["timestamp"] = int(time.time() * 1000)
    query_string = _parse_param(params)
    signature    = _get_sign(query_string)
    url = f"{BASE_URL}{path}?{query_string}&signature={signature}"

    response = requests.request(
        method, url,
        headers=_headers(),
        data={},
        timeout=10,
    )
    data = response.json()
    if isinstance(data, dict) and data.get("code", 0) != 0:
        logger.error(
            f"BingX API 錯誤 [{method} {path}] "
            f"code={data.get('code')} msg={data.get('msg')}"
        )
        raise Exception(f"API錯誤 code={data.get('code')} msg={data.get('msg')}")
    return data


def get_klines(symbol: str, interval: str = "1h", limit: int = 100) -> list:
    data = _send("GET", "/openApi/swap/v3/quote/klines", {
        "symbol": symbol, "interval": interval, "limit": limit,
    })
    result = []
    for k in data.get("data", []):
        try:
            if isinstance(k, (list, tuple)):
                result.append({
                    "open":   float(k[1]),
                    "high":   float(k[2]),
                    "low":    float(k[3]),
                    "close":  float(k[4]),
                    "volume": float(k[5]),
                })
            elif isinstance(k, dict):
                result.append({
                    "open":   float(k.get("open",   k.get("o", 0))),
                    "high":   float(k.get("high",   k.get("h", 0))),
                    "low":    float(k.get("low",    k.get("l", 0))),
                    "close":  float(k.get("close",  k.get("c", 0))),
                    "volume": float(k.get("volume", k.get("v", 0))),
                })
        except (IndexError, ValueError, KeyError, TypeError):
            continue
    return result


def get_ticker(symbol: str) -> float:
    data = _send("GET", "/openApi/swap/v2/quote/ticker", {"symbol": symbol})
    return float(data["data"]["lastPrice"])


def get_balance() -> float:
    try:
        data  = _send("GET", "/openApi/swap/v2/user/balance", {})
        inner = data.get("data", {})
        if not isinstance(inner, dict):
            return 0.0
        bal = inner.get("balance", {})
        if isinstance(bal, dict):
            return float(bal.get("availableMargin", bal.get("balance", 0)))
        if isinstance(bal, list):
            for item in bal:
                if isinstance(item, dict) and item.get("asset") == "USDT":
                    return float(item.get("availableMargin",
                                          item.get("balance", 0)))
    except Exception:
        pass
    try:
        data2  = _send("GET", "/openApi/swap/v3/user/balance", {})
        inner2 = data2.get("data", {})
        if isinstance(inner2, dict):
            return float(inner2.get("availableMargin",
                                    inner2.get("balance", 0)))
    except Exception:
        pass
    return 0.0


def get_open_positions() -> set:
    try:
        data      = _send("GET", "/openApi/swap/v2/user/positions", {})
        positions = data.get("data", [])
        if not isinstance(positions, list):
            return set()
        return {
            p["symbol"] for p in positions
            if isinstance(p, dict) and float(p.get("positionAmt", 0)) != 0
        }
    except Exception:
        return set()


def get_positions_detail() -> list:
    """取得所有持倉詳情（含浮動盈虧、進場價、現價）"""
    try:
        data      = _send("GET", "/openApi/swap/v2/user/positions", {})
        positions = data.get("data", [])
        if not isinstance(positions, list):
            return []
        result = []
        for p in positions:
            if not isinstance(p, dict):
                continue
            amt = float(p.get("positionAmt", 0) or 0)
            if amt == 0:
                continue
            result.append({
                "symbol":         p.get("symbol", ""),
                "side":           p.get("positionSide", "LONG"),
                "qty":            abs(amt),
                "entry_price":    float(p.get("entryPrice",      0) or 0),
                "mark_price":     float(p.get("markPrice",       0) or 0),
                "unrealized_pnl": float(p.get("unrealizedProfit", 0) or 0),
                "leverage":       int(float(p.get("leverage", 1) or 1)),
            })
        return result
    except Exception as e:
        logger.error(f"取得持倉詳情失敗：{e}")
        return []


def set_leverage(symbol: str) -> None:
    for side in ["LONG", "SHORT"]:
        try:
            _send("POST", "/openApi/swap/v2/trade/leverage", {
                "symbol": symbol, "side": side, "leverage": LEVERAGE,
            })
        except Exception as e:
            logger.warning(f"設定槓桿失敗 {symbol} {side}：{e}")


def place_order(symbol: str, side: str, usdt_amount: float,
                stop_loss_price: float, take_profit_price: float):
    """下市價單，附帶停損停利"""
    price   = get_ticker(symbol)
    decimal = _QTY_DECIMAL.get(symbol, 4)
    qty     = round(usdt_amount * LEVERAGE / price, decimal)
    min_qty = _MIN_QTY.get(symbol, 0.001)
    if qty <= 0:
        raise ValueError(f"數量異常：{qty}")
    if qty < min_qty:
        needed = round(min_qty * price / LEVERAGE, 2)
        raise ValueError(
            f"{symbol} 下單量 {qty} 低於最小限制 {min_qty}，"
            f"至少需要 {needed} USDT 本金"
        )

    set_leverage(symbol)

    # ✅ 修正：stopLoss type 用 STOP_MARKET，takeProfit type 用 TAKE_PROFIT_MARKET
    params = {
        "symbol":       symbol,
        "side":         "BUY" if side == "LONG" else "SELL",
        "positionSide": side,
        "type":         "MARKET",
        "quantity":     qty,
        "stopLoss":     f'{{"type":"STOP_MARKET","stopPrice":{stop_loss_price},"workingType":"MARK_PRICE"}}',
        "takeProfit":   f'{{"type":"TAKE_PROFIT_MARKET","stopPrice":{take_profit_price},"workingType":"MARK_PRICE"}}',
    }
    data  = _send("POST", "/openApi/swap/v2/trade/order", params)
    order = data.get("data", {}).get("order", {})
    return order.get("orderId")
