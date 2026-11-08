# Synthetic offline trace link check for local mocks.
# No fetching, storage, trading, payment, settlement, wallet, advice.
# Total, deterministic, pure helper with no I/O.
_PREFIX = "lab_tests/test_"
_SUFFIX = ".py"
_INVALID_PAYLOAD = "INVALID_PAYLOAD"
_INVALID_REQUIREMENT = "INVALID_REQUIREMENT"
_INVALID_OBJECTIVE = "INVALID_OBJECTIVE"
_INVALID_VERSION = "INVALID_VERSION"
_INVALID_TEST_PATH = "INVALID_TEST_PATH"
_INVALID_DECLARED_RESULT = "INVALID_DECLARED_RESULT"
_INVALID_RESULT_COUNTS = "INVALID_RESULT_COUNTS"
_UNEXPECTED_FAILURE = "UNEXPECTED_FAILURE"
_MIN_PATH_LEN = 15
_MAX_PATH_LEN = 80
_MAX_MIDDLE_LEN = 60
_MAX_OBJECTIVE_LEN = 200
_MAX_VERSION_LEN = 32
_MAX_COUNT = 1000000
def _is_count(value):
    if isinstance(value, bool):
        return False
    if not isinstance(value, int):
        return False
    if value < 0:
        return False
    if value > _MAX_COUNT:
        return False
    return True
def _is_test_path(value):
    if not isinstance(value, str):
        return False
    n = len(value)
    if n < _MIN_PATH_LEN:
        return False
    if n > _MAX_PATH_LEN:
        return False
    if n < 19:
        return False
    if value[0:15] != _PREFIX:
        return False
    if value[n - 3:n] != _SUFFIX:
        return False
    middle_len = n - 18
    if middle_len < 1:
        return False
    if middle_len > _MAX_MIDDLE_LEN:
        return False
    i = 15
    end = n - 3
    while i < end:
        ch = value[i]
        ok = False
        if "a" <= ch and ch <= "z":
            ok = True
        if "0" <= ch and ch <= "9":
            ok = True
        if ch == "_":
            ok = True
        if not ok:
            return False
        i = i + 1
    return True
def run_feature(payload):
    if not isinstance(payload, dict):
        return {"ok": False, "linked": False, "objective": "", "test_path": "", "passed": 0, "failed": 0, "issues": [_INVALID_PAYLOAD]}
    issues = []
    requirement = payload.get("requirement")
    objective_out = ""
    if not isinstance(requirement, dict):
        issues.append(_INVALID_REQUIREMENT)
    else:
        raw_objective = requirement.get("objective")
        if isinstance(raw_objective, str):
            objective_out = raw_objective.strip()
        else:
            objective_out = ""
        if not isinstance(raw_objective, str):
            issues.append(_INVALID_OBJECTIVE)
        else:
            s = raw_objective.strip()
            if len(s) < 1:
                issues.append(_INVALID_OBJECTIVE)
            else:
                if len(s) > _MAX_OBJECTIVE_LEN:
                    issues.append(_INVALID_OBJECTIVE)
        raw_version = requirement.get("version")
        if not isinstance(raw_version, str):
            issues.append(_INVALID_VERSION)
        else:
            sv = raw_version.strip()
            if len(sv) < 1:
                issues.append(_INVALID_VERSION)
            else:
                if len(sv) > _MAX_VERSION_LEN:
                    issues.append(_INVALID_VERSION)
    raw_path = payload.get("test_path")
    path_out = ""
    if isinstance(raw_path, str):
        path_out = raw_path
    else:
        path_out = ""
    if not _is_test_path(raw_path):
        issues.append(_INVALID_TEST_PATH)
    declared = payload.get("declared_result")
    passed_out = 0
    failed_out = 0
    if not isinstance(declared, dict):
        issues.append(_INVALID_DECLARED_RESULT)
    else:
        raw_passed = declared.get("passed")
        raw_failed = declared.get("failed")
        passed_ok = _is_count(raw_passed)
        failed_ok = _is_count(raw_failed)
        if passed_ok:
            passed_out = raw_passed
        else:
            passed_out = 0
        if failed_ok:
            failed_out = raw_failed
        else:
            failed_out = 0
        if not passed_ok:
            issues.append(_INVALID_RESULT_COUNTS)
        else:
            if not failed_ok:
                issues.append(_INVALID_RESULT_COUNTS)
            else:
                if raw_failed != 0:
                    issues.append(_UNEXPECTED_FAILURE)
    linked = False
    ok = False
    if len(issues) == 0:
        linked = True
        ok = True
    return {"ok": ok, "linked": linked, "objective": objective_out, "test_path": path_out, "passed": passed_out, "failed": failed_out, "issues": issues}
