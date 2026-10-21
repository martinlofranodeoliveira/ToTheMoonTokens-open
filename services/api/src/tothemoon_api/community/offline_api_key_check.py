"""Synthetic offline API key checker for local mocks.

Offline dashboard fixture helper only. It checks synthetic label,
scope and active shapes with normalized outputs and fixed issue
codes. It performs no fetching, storage, trading, payment, or advice
and makes no claim about real credentials, permissions, or production
enforcement.
"""

def run_feature(payload):
    """Check a synthetic api-key shape without I/O.

    Returns a dict with keys ok, label, scope, active and issues.
    Never mutates the input.
    """
    if not isinstance(payload, dict):
        return {"ok": False, "label": "", "scope": "read", "active": False, "issues": ["INVALID_PAYLOAD"]}
    issues = []
    if "label" in payload:
        raw_label = payload["label"]
    else:
        raw_label = None
    if isinstance(raw_label, str):
        stripped = raw_label.strip()
        if 1 <= len(stripped) <= 30:
            valid = True
            for ch in stripped:
                if ch == " " or ch == "_" or ch == "-":
                    continue
                if "A" <= ch <= "Z":
                    continue
                if "a" <= ch <= "z":
                    continue
                if "0" <= ch <= "9":
                    continue
                valid = False
                break
            if valid:
                result_label = stripped
            else:
                result_label = ""
                issues = issues + ["INVALID_LABEL"]
        else:
            result_label = ""
            issues = issues + ["INVALID_LABEL"]
    else:
        result_label = ""
        issues = issues + ["INVALID_LABEL"]
    if "scope" in payload:
        raw_scope = payload["scope"]
        if isinstance(raw_scope, str) and (raw_scope == "read" or raw_scope == "write" or raw_scope == "audit"):
            result_scope = raw_scope
        else:
            result_scope = "read"
            issues = issues + ["INVALID_SCOPE"]
    else:
        result_scope = "read"
    if "active" in payload:
        raw_active = payload["active"]
        if type(raw_active) is bool:
            result_active = raw_active
        else:
            result_active = False
            issues = issues + ["INVALID_ACTIVE"]
    else:
        result_active = False
    if issues == []:
        result_ok = True
    else:
        result_ok = False
    return {"ok": result_ok, "label": result_label, "scope": result_scope, "active": result_active, "issues": issues}
