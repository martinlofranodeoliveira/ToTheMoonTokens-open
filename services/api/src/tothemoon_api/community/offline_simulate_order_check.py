"""Synthetic offline simulate_order checker for local mocks.

Deterministic pure JSON core for offline dashboard fixtures.
Performs no fetching, storage, trading, payment, or advice and makes
no claim about real orders, prices, or production enforcement.
Application and UI integration are outside this change.
"""

import math

_MAX_VALUE = 1000000
_MAX_SYMBOL_LEN = 20


def run_feature(payload):
    if not isinstance(payload, dict):
        return {"symbol": "", "side": "buy", "quantity": 0, "price_usd": 0, "issues": ["INVALID_PAYLOAD"], "ok": False}
    issues = []
    if "symbol" not in payload:
        symbol = ""
        issues.append("INVALID_SYMBOL")
    else:
        raw_symbol = payload["symbol"]
        if not isinstance(raw_symbol, str):
            symbol = ""
            issues.append("INVALID_SYMBOL")
        else:
            stripped = raw_symbol.strip()
            if len(stripped) < 1 or len(stripped) > _MAX_SYMBOL_LEN:
                symbol = ""
                issues.append("INVALID_SYMBOL")
            else:
                valid = True
                for ch in stripped:
                    if ("A" <= ch <= "Z") or ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "." or ch == "_" or ch == "-":
                        continue
                    valid = False
                    break
                if valid:
                    symbol = stripped.upper()
                else:
                    symbol = ""
                    issues.append("INVALID_SYMBOL")
    if "side" not in payload:
        side = "buy"
    else:
        raw_side = payload["side"]
        if raw_side == "buy" or raw_side == "sell":
            side = raw_side
        else:
            side = "buy"
            issues.append("INVALID_SIDE")
    if "quantity" not in payload:
        quantity = 0
    else:
        raw_quantity = payload["quantity"]
        if isinstance(raw_quantity, bool):
            quantity = 0
            issues.append("INVALID_QUANTITY")
        elif isinstance(raw_quantity, int) and 0 <= raw_quantity <= _MAX_VALUE:
            quantity = raw_quantity
        else:
            quantity = 0
            issues.append("INVALID_QUANTITY")
    if "price_usd" not in payload:
        price_usd = 0
    else:
        raw_price = payload["price_usd"]
        if isinstance(raw_price, bool):
            price_usd = 0
            issues.append("INVALID_PRICE")
        elif isinstance(raw_price, int) and 0 <= raw_price <= _MAX_VALUE:
            price_usd = raw_price
        elif isinstance(raw_price, float) and math.isfinite(raw_price) and 0 <= raw_price <= _MAX_VALUE:
            price_usd = raw_price
        else:
            price_usd = 0
            issues.append("INVALID_PRICE")
    ok = len(issues) == 0
    return {"symbol": symbol, "side": side, "quantity": quantity, "price_usd": price_usd, "issues": issues, "ok": ok}
