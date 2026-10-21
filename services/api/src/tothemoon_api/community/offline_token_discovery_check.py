"""Synthetic Offline Token Discovery Checker for Local Mocks.

Helps local developers and test authors exercise synthetic token_discoveries
shape decisions from offline dashboard fixtures without network,
credentials, or live services. Provides a single deterministic check for
symbol, chain, risk_score and flagged with normalized outputs and fixed
issue codes.

Performs no fetching, storage, trading, payment, or advice and makes no
claim about real tokens, prices, risk, or production enforcement.
Independently testable; application and UI integration are out of scope.
"""
import math
_ALLOWED_CHAINS = ("ethereum", "solana", "base")
_ALLOWED_SYMBOL_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
def run_feature(payload):
    """Validate a synthetic offline token discovery payload.

    Returns dict with ok, symbol, chain, risk_score, flagged, issues.
    Total, deterministic and pure with no I/O; preserves input.
    """
    if not isinstance(payload, dict):
        return {"ok": False, "symbol": "", "chain": "unknown", "risk_score": 0, "flagged": False, "issues": ["INVALID_PAYLOAD"]}
    issues = []
    if "symbol" in payload:
        symbol_raw = payload["symbol"]
    else:
        symbol_raw = None
    if isinstance(symbol_raw, str):
        stripped = symbol_raw.strip()
        if 1 <= len(stripped) <= 20:
            valid = True
            for ch in stripped:
                if ch not in _ALLOWED_SYMBOL_CHARS:
                    valid = False
                    break
            if valid:
                symbol_out = stripped.upper()
            else:
                symbol_out = ""
                issues = issues + ["INVALID_SYMBOL"]
        else:
            symbol_out = ""
            issues = issues + ["INVALID_SYMBOL"]
    else:
        symbol_out = ""
        issues = issues + ["INVALID_SYMBOL"]
    if "chain" not in payload:
        chain_out = "unknown"
    else:
        chain_raw = payload["chain"]
        if chain_raw in _ALLOWED_CHAINS:
            chain_out = chain_raw
        else:
            chain_out = "unknown"
            issues = issues + ["INVALID_CHAIN"]
    if "risk_score" not in payload:
        risk_out = 0
    else:
        risk_raw = payload["risk_score"]
        if isinstance(risk_raw, bool):
            risk_out = 0
            issues = issues + ["INVALID_RISK_SCORE"]
        elif isinstance(risk_raw, int):
            if 0 <= risk_raw <= 100:
                risk_out = risk_raw
            else:
                risk_out = 0
                issues = issues + ["INVALID_RISK_SCORE"]
        elif isinstance(risk_raw, float):
            if math.isfinite(risk_raw) and 0 <= risk_raw <= 100:
                risk_out = risk_raw
            else:
                risk_out = 0
                issues = issues + ["INVALID_RISK_SCORE"]
        else:
            risk_out = 0
            issues = issues + ["INVALID_RISK_SCORE"]
    if "flagged" not in payload:
        flagged_out = False
    else:
        flagged_raw = payload["flagged"]
        if isinstance(flagged_raw, bool):
            flagged_out = flagged_raw
        else:
            flagged_out = False
            issues = issues + ["INVALID_FLAGGED"]
    ok = len(issues) == 0
    return {"ok": ok, "symbol": symbol_out, "chain": chain_out, "risk_score": risk_out, "flagged": flagged_out, "issues": issues}
