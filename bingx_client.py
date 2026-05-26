"""
BingX API 客戶端
簽名方式：原始字串拼接（不做 URL encode），符合 BingX 官方規範
"""
import hmac
import hashlib
import time
import logging
import requests
from config import BINGX_API_KEY, BINGX_API_SECRET, LEVERAGE

BASE_URL = "https://open-api.bingx.com"
logger   = logging.getLogger(__name__)


def _sign(params: dict) -> str:
    """
    BingX 簽名規則：
    將所有參數按 key 排序後，用 & 拼接成 key=value 字串（不做 URL encode），
    再用 HMAC-SHA256 + API Secret 產生簽名。
    """
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(
        BINGX_API_SECRET.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _headers() -> dict:
    return {"X-BX-APIKEY": BINGX_API_KEY}


def _get(path: str, params: dict = None) -> dict:
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign(params)
    r = requests.get(BASE_URL + path, params=params,
                     headers=_headers(), timeout=10)
    data = r.json()
    if isinstance(data, dict) and data.get("code", 0) != 0:
        logger.error(f"BingX GET 錯誤 [{path}] code={data.get('code')} msg={data.get('msg')}")
        raise Exception(f"API錯誤 code={data.get('code')} msg={data.get('msg')}")
    return data


def _post(path: str, params: dict = None) -> dict:
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign(params)
    r = requests.post(BASE_URL + path, params=params,
                      headers=_headers(), timeout=10)
    data = r.json()
    if isinstance(data, dict) and data.get("code", 0) != 0:
        logger.error(f"BingX POST 錯誤 [{path}] code={data.get('code')} msg={data.get('msg')}")
        raise Exception(f"API錯誤 code={data.get('code')} msg={data.get('msg')}")
    return data


def get_klines(symbol: str, interval: str = "1h", limit: int = 100) -> list:
    data = _get("/openApi/swap/v3/quote/klines", {
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
    data = _get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
    return float(data["data"]["lastPrice"])


def get_balance() -> float:
    try:
        data  = _get("/openApi/swap/v2/user/balance")
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
        data2  = _get("/openApi/swap/v3/user/balance")
        inner2 = data2.get("data", {})
        if isinstance(inner2, dict):
            return float(inner2.get("availableMargin",
                                    inner2.get("balance", 0)))
    except Exception:
        pass
    return 0.0


def get_open_positions() -> set:
    try:
        data      = _get("/openApi/swap/v2/user/positions")
        positions = data.get("data", [])
        if not isinstance(positions, list):
            return set()
        return {
            p["symbol"] for p in positions
            if isinstance(p, dict) and float(p.get("positionAmt", 0)) != 0
        }
    except Exception:
        return set()


def set_leverage(symbol: str) -> None:
    for side in ["LONG", "SHORT"]:
        try:
            _post("/openApi/swap/v2/trade/leverage", {
                "symbol": symbol, "side": side, "leverage": LEVERAGE,
            })
        except Exception as e:
            logger.warning(f"設定槓桿失敗 {symbol} {side}：{e}")


def place_order(symbol: str, side: str, usdt_amount: float,
                stop_loss_price: float, take_profit_price: float):
    price = get_ticker(symbol)
    qty   = round(usdt_amount * LEVERAGE / price, 4)
    if qty <= 0:
        raise ValueError(f"數量異常：{qty}")

    set_leverage(symbol)

    params = {
        "symbol":       symbol,
        "side":         "BUY" if side == "LONG" else "SELL",
        "positionSide": side,
        "type":         "MARKET",
        "quantity":     qty,
        "stopLoss":     f'{{"type":"MARK_PRICE","stopPrice":{stop_loss_price},"workingType":"MARK_PRICE"}}',
        "takeProfit":   f'{{"type":"MARK_PRICE","stopPrice":{take_profit_price},"workingType":"MARK_PRICE"}}',
    }
    data  = _post("/openApi/swap/v2/trade/order", params)
    order = data.get("data", {}).get("order", {})
    return order.get("orderId")
