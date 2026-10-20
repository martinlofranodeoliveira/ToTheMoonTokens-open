"""Synthetic offline quota guard evaluator."""

_ALLOWED_ACTIONS = ("simulate_order", "token_audit", "active_api_keys")


def run_feature(payload):
    if not isinstance(payload, dict):
        return {"ok": False, "allowed": False, "action": "unknown", "limit": 0, "used": 0, "requested": 1, "remaining": 0, "issues": ["INVALID_PAYLOAD"]}
    issues = []
    if "action" in payload:
        raw_action = payload["action"]
    else:
        raw_action = None
    if raw_action in _ALLOWED_ACTIONS:
        action_out = raw_action
    else:
        action_out = "unknown"
        issues = issues + ["INVALID_ACTION"]
    if "quota" in payload:
        raw_quota = payload["quota"]
    else:
        raw_quota = None
    limit = 0
    quota_ok = False
    if isinstance(raw_quota, dict):
        quota_value = None
        quota_found = False
        for k in raw_quota:
            if k is raw_action or k == raw_action:
                quota_value = raw_quota[k]
                quota_found = True
                break
        if quota_found and type(quota_value) is int and 0 <= quota_value <= 1000000:
            limit = quota_value
            quota_ok = True
        else:
            issues = issues + ["INVALID_QUOTA_LIMIT"]
    else:
        issues = issues + ["INVALID_QUOTA"]
    if "usage" in payload:
        raw_usage = payload["usage"]
    else:
        raw_usage = None
    used = 0
    usage_ok = False
    if isinstance(raw_usage, dict):
        usage_value = None
        usage_found = False
        for k in raw_usage:
            if k is raw_action or k == raw_action:
                usage_value = raw_usage[k]
                usage_found = True
                break
        if usage_found and type(usage_value) is int and 0 <= usage_value <= 1000000:
            used = usage_value
            usage_ok = True
        else:
            issues = issues + ["INVALID_USAGE_VALUE"]
    else:
        issues = issues + ["INVALID_USAGE"]
    if "requested" in payload:
        raw_requested = payload["requested"]
        if type(raw_requested) is int and 1 <= raw_requested <= 1000000:
            requested_out = raw_requested
        else:
            requested_out = 1
            issues = issues + ["INVALID_REQUESTED"]
    else:
        requested_out = 1
    if quota_ok and usage_ok:
        if limit > used:
            remaining = limit - used
        else:
            remaining = 0
    else:
        remaining = 0
    if issues == [] and used + requested_out <= limit:
        allowed = True
    else:
        allowed = False
    if issues == []:
        ok = True
    else:
        ok = False
    return {"ok": ok, "allowed": allowed, "action": action_out, "limit": limit, "used": used, "requested": requested_out, "remaining": remaining, "issues": issues}
