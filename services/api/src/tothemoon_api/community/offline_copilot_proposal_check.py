"""Synthetic Offline Copilot Proposal Checker for Local Mocks.

Local offline helper that validates synthetic ``copilot_proposals`` shapes
from offline dashboard fixtures. It performs no fetching, storage, trading,
payment, or advice and makes no claim about real strategies, performance,
or production enforcement. Total, deterministic and pure with no I/O.
"""

import math

_ALLOWED_STRATEGIES = ("momentum", "mean-reversion", "breakout")
_ALLOWED_STATUSES = ("draft", "proposed", "approved", "rejected")


def run_feature(payload):
    """Validate a synthetic copilot proposal payload.

    Returns dict with keys ok, title, strategy, confidence, status, issues.
    Never mutates input and performs no I/O.
    """
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "title": "",
            "strategy": "unknown",
            "confidence": 0,
            "status": "draft",
            "issues": ["INVALID_PAYLOAD"],
        }
    issues = []
    if "title" in payload:
        raw_title = payload["title"]
    else:
        raw_title = None
    title_out = ""
    if isinstance(raw_title, str):
        stripped = raw_title.strip()
        if 1 <= len(stripped) <= 80:
            valid_chars = True
            for ch in stripped:
                if "A" <= ch <= "Z":
                    continue
                if "a" <= ch <= "z":
                    continue
                if "0" <= ch <= "9":
                    continue
                if ch == " ":
                    continue
                if ch == "_":
                    continue
                if ch == "-":
                    continue
                if ch == ".":
                    continue
                valid_chars = False
                break
            if valid_chars:
                title_out = stripped
            else:
                issues.append("INVALID_TITLE")
        else:
            issues.append("INVALID_TITLE")
    else:
        issues.append("INVALID_TITLE")
    if "strategy" in payload:
        raw_strategy = payload["strategy"]
        if isinstance(raw_strategy, str) and raw_strategy in _ALLOWED_STRATEGIES:
            strategy_out = raw_strategy
        else:
            strategy_out = "unknown"
            issues.append("INVALID_STRATEGY")
    else:
        strategy_out = "unknown"
    if "confidence" in payload:
        raw_confidence = payload["confidence"]
        if isinstance(raw_confidence, bool):
            confidence_out = 0
            issues.append("INVALID_CONFIDENCE")
        elif isinstance(raw_confidence, int):
            if 0 <= raw_confidence <= 100 and math.isfinite(raw_confidence):
                confidence_out = raw_confidence
            else:
                confidence_out = 0
                issues.append("INVALID_CONFIDENCE")
        elif isinstance(raw_confidence, float):
            if 0 <= raw_confidence <= 100 and math.isfinite(raw_confidence):
                confidence_out = raw_confidence
            else:
                confidence_out = 0
                issues.append("INVALID_CONFIDENCE")
        else:
            confidence_out = 0
            issues.append("INVALID_CONFIDENCE")
    else:
        confidence_out = 0
    if "status" in payload:
        raw_status = payload["status"]
        if isinstance(raw_status, str) and raw_status in _ALLOWED_STATUSES:
            status_out = raw_status
        else:
            status_out = "draft"
            issues.append("INVALID_STATUS")
    else:
        status_out = "draft"
    ok = len(issues) == 0
    return {
        "ok": ok,
        "title": title_out,
        "strategy": strategy_out,
        "confidence": confidence_out,
        "status": status_out,
        "issues": issues,
    }
