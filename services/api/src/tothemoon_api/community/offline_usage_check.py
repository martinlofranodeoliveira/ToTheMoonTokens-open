"""Synthetic offline usage checker for local mocks."""

_OFFLINE_PLAN = "offline-demo"
_MIN_COUNT = 0
_MAX_COUNT = 1000000
_CODE_INVALID_PAYLOAD = "INVALID_PAYLOAD"
_CODE_INVALID_REQUESTS_TODAY = "INVALID_REQUESTS_TODAY"
_CODE_INVALID_REQUESTS_MONTH = "INVALID_REQUESTS_MONTH"
_CODE_INVALID_PLAN = "INVALID_PLAN"
_CODE_INVALID_QUOTA = "INVALID_QUOTA"
_CODE_INVALID_QUOTA_LIMIT = "INVALID_QUOTA_LIMIT"
_KEY_TODAY = "requests_today"
_KEY_MONTH = "requests_month"
_KEY_PLAN = "plan"
_KEY_QUOTA = "quota"
_KEY_ACTIVE = "active_api_keys"
_KEY_SIMULATE = "simulate_order"
_KEY_AUDIT = "token_audit"


def _is_valid_count(value):
    return type(value) is int and _MIN_COUNT <= value and value <= _MAX_COUNT


def run_feature(payload):
    if type(payload) is not dict:
        return {
            "ok": False,
            "requests_today": 0,
            "requests_month": 0,
            "plan": _OFFLINE_PLAN,
            "quota": {_KEY_ACTIVE: 0, _KEY_SIMULATE: 0, _KEY_AUDIT: 0},
            "issues": [_CODE_INVALID_PAYLOAD]
        }
    issues = []
    if _KEY_TODAY not in payload:
        requests_today = 0
    else:
        candidate_today = payload[_KEY_TODAY]
        if _is_valid_count(candidate_today):
            requests_today = candidate_today
        else:
            requests_today = 0
            issues = issues + [_CODE_INVALID_REQUESTS_TODAY]
    if _KEY_MONTH not in payload:
        requests_month = 0
    else:
        candidate_month = payload[_KEY_MONTH]
        if _is_valid_count(candidate_month):
            requests_month = candidate_month
        else:
            requests_month = 0
            issues = issues + [_CODE_INVALID_REQUESTS_MONTH]
    if _KEY_PLAN not in payload:
        plan = _OFFLINE_PLAN
    else:
        candidate_plan = payload[_KEY_PLAN]
        if candidate_plan == _OFFLINE_PLAN:
            plan = candidate_plan
        else:
            plan = _OFFLINE_PLAN
            issues = issues + [_CODE_INVALID_PLAN]
    if _KEY_QUOTA not in payload:
        quota = {_KEY_ACTIVE: 0, _KEY_SIMULATE: 0, _KEY_AUDIT: 0}
    else:
        candidate_quota = payload[_KEY_QUOTA]
        if type(candidate_quota) is not dict:
            quota = {_KEY_ACTIVE: 0, _KEY_SIMULATE: 0, _KEY_AUDIT: 0}
            issues = issues + [_CODE_INVALID_QUOTA]
        else:
            if _KEY_SIMULATE in candidate_quota:
                simulate_value = candidate_quota[_KEY_SIMULATE]
            else:
                simulate_value = None
            if _KEY_AUDIT in candidate_quota:
                audit_value = candidate_quota[_KEY_AUDIT]
            else:
                audit_value = None
            if _KEY_ACTIVE in candidate_quota:
                active_value = candidate_quota[_KEY_ACTIVE]
            else:
                active_value = None
            if _is_valid_count(simulate_value) and _is_valid_count(audit_value) and _is_valid_count(active_value):
                quota = {_KEY_ACTIVE: active_value, _KEY_SIMULATE: simulate_value, _KEY_AUDIT: audit_value}
            else:
                quota = {_KEY_ACTIVE: 0, _KEY_SIMULATE: 0, _KEY_AUDIT: 0}
                issues = issues + [_CODE_INVALID_QUOTA_LIMIT]
    ok = issues == []
    return {
        "ok": ok,
        "requests_today": requests_today,
        "requests_month": requests_month,
        "plan": plan,
        "quota": quota,
        "issues": issues
    }
