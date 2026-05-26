"""
BingX API 客戶端
簽名方式完全參照 BingX 官方 Python 範例：
  1. 將 params 按 key 排序後拼成 query string（不 URL encode）
  2. 對 query string 做 HMAC-SHA256 產生簽名
  3. 把完整 URL（含 query string 和 signature）直接傳給 requests
  4. body 保持空白

這樣 requests 不會對 query string 做任何二次 encode，確保 BingX 驗簽成功。
"""
import hmac
import hashlib
import time
import logging
import requests
from config import BINGX_API_KEY, BINGX_API_SECRET, LEVERAGE

BASE_URL = "https://open-api.bingx.com"
logger   = logging.getLogger(__name__)


def _parse_param(params: dict) -> str:
    """
    將 params 按 key 排序後拼成 query string。
    完全照搬 BingX 官方 praseParam 函數邏輯。
    """
    sorted_keys = sorted(params)
    return "&".join(f"{k}={params[k]}" for k in sorted_keys)


def _get_sign(payload: str) -> str:
    """對 query string 做 HMAC-SHA256 簽名。"""
    return hmac.new(
        BINGX_API_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _headers() -> dict:
    return {"X-BX-APIKEY": BINGX_API_KEY}


def _send(method: str, path: str, params: dict) -> dict:
    """
    通用請求函數：
    - 加入 timestamp
    - 產生 query string
    - 簽名
    - 組合完整 URL
    - 送出請求（body 為空）
    """
    params = dict(params)
    params["timestamp"] = int(time.time() * 1000)
    query_string = _parse_param(params)
    signature    = _get_sign(query_string)
    url = f"{BASE_URL}{path}?{query_string}&signature={signature}"

    response = requests.request(
        method, url,
        headers=_headers(),
        data={},          # body 保持空白
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
    """取得 K 線資料"""
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
    """取得最新成交價"""
    data = _send("GET", "/openApi/swap/v2/quote/ticker", {"symbol": symbol})
    return float(data["data"]["lastPrice"])


def get_balance() -> float:
    """取得永續 U 本位帳戶可用 USDT"""
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
    """取得目前有持倉的幣對集合"""
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


def set_leverage(symbol: str) -> None:
    """多空兩邊都設定槓桿"""
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
    data  = _send("POST", "/openApi/swap/v2/trade/order", params)
    order = data.get("data", {}).get("order", {})
    return order.get("orderId")
