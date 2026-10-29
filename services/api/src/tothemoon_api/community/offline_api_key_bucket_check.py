# Synthetic offline API key bucket scan checker for local mocks.
# Models bucket counting only; selector alone never authenticates.
_MIN_BUCKET = 0
_MAX_BUCKET = 1000000
_INVALID_PAYLOAD = "INVALID_PAYLOAD"
_INVALID_SELECTOR_BUCKET = "INVALID_SELECTOR_BUCKET"
_INVALID_LEGACY_BUCKET = "INVALID_LEGACY_BUCKET"
_INVALID_INCLUDE_LEGACY = "INVALID_INCLUDE_LEGACY"


def _is_valid_bucket(value):
    if type(value) is not int:
        return False
    if value < _MIN_BUCKET:
        return False
    if value > _MAX_BUCKET:
        return False
    return True


def _is_valid_include_legacy(value):
    if type(value) is bool:
        return True
    return False


def run_feature(payload):
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "selector_bucket": 0,
            "legacy_bucket": 0,
            "include_legacy": False,
            "hash_checks": 0,
            "selector_authenticates": False,
            "issues": [_INVALID_PAYLOAD]
        }
    issues = []
    if "selector_bucket" not in payload:
        selector_bucket = 0
    else:
        raw_selector = payload["selector_bucket"]
        if _is_valid_bucket(raw_selector):
            selector_bucket = raw_selector
        else:
            selector_bucket = 0
            issues = issues + [_INVALID_SELECTOR_BUCKET]
    if "legacy_bucket" not in payload:
        legacy_bucket = 0
    else:
        raw_legacy = payload["legacy_bucket"]
        if _is_valid_bucket(raw_legacy):
            legacy_bucket = raw_legacy
        else:
            legacy_bucket = 0
            issues = issues + [_INVALID_LEGACY_BUCKET]
    if "include_legacy" not in payload:
        include_legacy = False
    else:
        raw_include = payload["include_legacy"]
        if _is_valid_include_legacy(raw_include):
            include_legacy = raw_include
        else:
            include_legacy = False
            issues = issues + [_INVALID_INCLUDE_LEGACY]
    if include_legacy:
        hash_checks = selector_bucket + legacy_bucket
    else:
        hash_checks = selector_bucket
    ok = len(issues) == 0
    return {
        "ok": ok,
        "selector_bucket": selector_bucket,
        "legacy_bucket": legacy_bucket,
        "include_legacy": include_legacy,
        "hash_checks": hash_checks,
        "selector_authenticates": False,
        "issues": issues
    }
