"""Synthetic Offline Dashboard Checker for Local Mocks.

Helps local developers exercise synthetic dashboard shape decisions from
offline fixtures without network, credentials, or live services. Single
deterministic check for email, org_id and requests_this_month with
normalized outputs and fixed issue codes.

Performs no fetching, storage, trading, payment, or advice and makes no
claim about real accounts, activity, or production enforcement. Total,
deterministic and pure with no I/O, environment, time, randomness,
callbacks or dependencies, and preserves input.
"""
_DEFAULT_EMAIL = "synthetic@example.invalid"
_INVALID_PAYLOAD = "INVALID_PAYLOAD"
_INVALID_EMAIL = "INVALID_EMAIL"
_INVALID_ORG_ID = "INVALID_ORG_ID"
_INVALID_REQUESTS_THIS_MONTH = "INVALID_REQUESTS_THIS_MONTH"


def run_feature(payload):
    """Validate synthetic dashboard payload and return normalized result."""
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "email": "",
            "org_id": 0,
            "requests_this_month": 0,
            "issues": [_INVALID_PAYLOAD],
        }
    issues = []
    if "email" not in payload:
        email_out = _DEFAULT_EMAIL
    else:
        raw_email = payload["email"]
        if type(raw_email) is not str:
            issues = issues + [_INVALID_EMAIL]
            email_out = ""
        else:
            stripped = raw_email.strip()
            if len(stripped) < 5 or len(stripped) > 80:
                issues = issues + [_INVALID_EMAIL]
                email_out = ""
            else:
                at_count = 0
                at_pos = 0
                pos = 0
                for ch in stripped:
                    if ch == "@":
                        at_count = at_count + 1
                        at_pos = pos
                    pos = pos + 1
                if at_count != 1:
                    issues = issues + [_INVALID_EMAIL]
                    email_out = ""
                else:
                    has_dot = False
                    i = at_pos + 1
                    n = len(stripped)
                    while i < n:
                        if stripped[i] == ".":
                            has_dot = True
                        i = i + 1
                    if not has_dot:
                        issues = issues + [_INVALID_EMAIL]
                        email_out = ""
                    else:
                        email_out = stripped
    if "org_id" not in payload:
        org_out = 0
    else:
        raw_org = payload["org_id"]
        if type(raw_org) is not int:
            issues = issues + [_INVALID_ORG_ID]
            org_out = 0
        elif raw_org < 0 or raw_org > 1000000:
            issues = issues + [_INVALID_ORG_ID]
            org_out = 0
        else:
            org_out = raw_org
    if "requests_this_month" not in payload:
        req_out = 0
    else:
        raw_req = payload["requests_this_month"]
        if type(raw_req) is not int:
            issues = issues + [_INVALID_REQUESTS_THIS_MONTH]
            req_out = 0
        elif raw_req < 0 or raw_req > 1000000:
            issues = issues + [_INVALID_REQUESTS_THIS_MONTH]
            req_out = 0
        else:
            req_out = raw_req
    ok = len(issues) == 0
    return {
        "ok": ok,
        "email": email_out,
        "org_id": org_out,
        "requests_this_month": req_out,
        "issues": issues,
    }
