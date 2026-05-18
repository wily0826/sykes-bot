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

def _headers():
    return {"X-BX-APIKEY": BINGX_API_KEY}

def _get(path, params=None):
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign(params)
    r = requests.get(BASE_URL + path, params=params, headers=_headers(), timeout=10)
    return r.json()

def _post(path, params=None):
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign(params)
    r = requests.post(BASE_URL + path, params=params, headers=_headers(), timeout=10)
    return r.json()

# ── K線資料 ───────────────────────────────────────────────
def get_klines(symbol: str, interval="1h", limit=100):
    """取得K線，回傳 list of dict {open,high,low,close,volume}"""
    path = "/openApi/swap/v3/quote/klines"
    data = _get(path, {"symbol": symbol, "interval": interval, "limit": limit})
    result = []
    for k in data.get("data", []):
        result.append({
            "open":   float(k[1]),
            "high":   float(k[2]),
            "low":    float(k[3]),
            "close":  float(k[4]),
            "volume": float(k[5]),
        })
    return result

def get_ticker(symbol: str) -> float:
    """取得最新成交價"""
    path = "/openApi/swap/v2/quote/ticker"
    data = _get(path, {"symbol": symbol})
    return float(data["data"]["lastPrice"])

# ── 帳戶 ──────────────────────────────────────────────────
def get_balance() -> float:
    """取得 USDT 可用餘額"""
    data = _get("/openApi/swap/v2/user/balance")
    for asset in data.get("data", {}).get("balance", []):
        if asset.get("asset") == "USDT":
            return float(asset.get("availableMargin", 0))
    return 0.0

# ── 下單 ──────────────────────────────────────────────────
def set_leverage(symbol: str):
    _post("/openApi/swap/v2/trade/leverage", {
        "symbol": symbol,
        "side": "LONG",
        "leverage": LEVERAGE,
    })

def place_order(symbol: str, side: str, usdt_amount: float,
                stop_loss_price: float, take_profit_price: float):
    """
    side: LONG / SHORT
    回傳 order_id 或 None
    """
    price = get_ticker(symbol)
    qty   = round(usdt_amount * LEVERAGE / price, 4)

    set_leverage(symbol)

    params = {
        "symbol":           symbol,
        "side":             "BUY" if side == "LONG" else "SELL",
        "positionSide":     side,
        "type":             "MARKET",
        "quantity":         qty,
        "stopLoss":         f'{{"type":"MARK_PRICE","stopPrice":{stop_loss_price},"workingType":"MARK_PRICE"}}',
        "takeProfit":       f'{{"type":"MARK_PRICE","stopPrice":{take_profit_price},"workingType":"MARK_PRICE"}}',
    }
    data = _post("/openApi/swap/v2/trade/order", params)
    order = data.get("data", {}).get("order", {})
    return order.get("orderId")

def close_position(symbol: str, side: str):
    """平倉"""
    params = {
        "symbol":       symbol,
        "side":         "SELL" if side == "LONG" else "BUY",
        "positionSide": side,
        "type":         "MARKET",
        "quantity":     0,
        "closePosition": "true",
    }
    return _post("/openApi/swap/v2/trade/order", params)
