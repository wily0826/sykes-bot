"""
BingX API 客戶端
"""
import hmac
import hashlib
import time
import requests
from urllib.parse import urlencode
from config import BINGX_API_KEY, BINGX_API_SECRET, LEVERAGE

BASE_URL = "https://open-api.bingx.com"

def _sign(params: dict) -> str:
    query = urlencode(sorted(params.items()))
    return hmac.new(
        BINGX_API_SECRET.encode(), query.encode(), hashlib.sha256
    ).hexdigest()

def _headers() -> dict:
    return {"X-BX-APIKEY": BINGX_API_KEY}

def _get(path: str, params: dict = None) -> dict:
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign(params)
    r = requests.get(BASE_URL + path, params=params, headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def _post(path: str, params: dict = None) -> dict:
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign(params)
    r = requests.post(BASE_URL + path, params=params, headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def get_klines(symbol: str, interval: str = "1h", limit: int = 100) -> list:
    """取得K線資料"""
    data = _get("/openApi/swap/v3/quote/klines", {
        "symbol": symbol, "interval": interval, "limit": limit
    })
    result = []
    for k in data.get("data", []):
        try:
            result.append({
                "open":   float(k[1]),
                "high":   float(k[2]),
                "low":    float(k[3]),
                "close":  float(k[4]),
                "volume": float(k[5]),
            })
        except (IndexError, ValueError):
            continue
    return result

def get_ticker(symbol: str) -> float:
    """取得最新成交價"""
    data = _get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
    return float(data["data"]["lastPrice"])

def get_balance() -> float:
    """取得永續U本位帳戶可用 USDT"""
    try:
        data = _get("/openApi/swap/v2/user/balance")
        inner = data.get("data", {})
        if not isinstance(inner, dict):
            return 0.0
        bal = inner.get("balance", {})
        if isinstance(bal, dict):
            return float(bal.get("availableMargin", bal.get("balance", 0)))
        if isinstance(bal, list):
            for item in bal:
                if isinstance(item, dict) and item.get("asset") == "USDT":
                    return float(item.get("availableMargin", item.get("balance", 0)))
    except Exception:
        pass
    try:
        data2 = _get("/openApi/swap/v3/user/balance")
        inner2 = data2.get("data", {})
        if isinstance(inner2, dict):
            return float(inner2.get("availableMargin", inner2.get("balance", 0)))
    except Exception:
        pass
    return 0.0

def set_leverage(symbol: str) -> None:
    """多空兩邊都設定槓桿"""
    for side in ["LONG", "SHORT"]:
        try:
            _post("/openApi/swap/v2/trade/leverage", {
                "symbol": symbol, "side": side, "leverage": LEVERAGE,
            })
        except Exception:
            pass

def place_order(symbol: str, side: str, usdt_amount: float,
                stop_loss_price: float, take_profit_price: float):
    """下市價單，附帶停損停利"""
    price = get_ticker(symbol)
    qty   = round(usdt_amount * LEVERAGE / price, 4)
    if qty <= 0:
        raise ValueError(f"計算出的數量異常：{qty}")

    set_leverage(symbol)

    params = {
        "symbol":       symbol,
        "side":         "BUY" if side == "LONG" else "SELL",
        "positionSide": side,
        "type":         "MARKET",
        "quantity":     qty,
        "stopLoss":     (
            f'{{"type":"MARK_PRICE",'
            f'"stopPrice":{stop_loss_price},'
            f'"workingType":"MARK_PRICE"}}'
        ),
        "takeProfit":   (
            f'{{"type":"MARK_PRICE",'
            f'"stopPrice":{take_profit_price},'
            f'"workingType":"MARK_PRICE"}}'
        ),
    }
    data = _post("/openApi/swap/v2/trade/order", params)
    return data.get("data", {}).get("order", {}).get("orderId")
