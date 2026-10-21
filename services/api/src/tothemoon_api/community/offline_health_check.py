"""Synthetic Offline Health Checker for Local Mocks.

Pure deterministic helper for offline dashboard fixtures. Performs no
fetching, storage, trading, payment, or advice and makes no claim about
real health, availability, or production enforcement. Independently
testable; application integration is outside this change.
"""


def run_feature(payload):
    """Validate synthetic offline health shape without I/O."""
    if not isinstance(payload, dict):
        return {"ok": False, "healthy": False, "mode": "synthetic", "provider_status": "offline", "issues": ["INVALID_PAYLOAD"]}
    issues = []
    healthy = True
    mode = "synthetic"
    provider_status = "offline"
    if "healthy" in payload:
        healthy_value = payload["healthy"]
        if healthy_value is True:
            healthy = True
        else:
            if healthy_value is False:
                healthy = False
            else:
                healthy = False
                issues = issues + ["INVALID_HEALTHY"]
    if "mode" in payload:
        mode_value = payload["mode"]
        if mode_value == "synthetic":
            mode = mode_value
        else:
            mode = "synthetic"
            issues = issues + ["INVALID_MODE"]
    if "provider_status" in payload:
        provider_value = payload["provider_status"]
        if provider_value == "offline":
            provider_status = provider_value
        else:
            if provider_value == "degraded":
                provider_status = provider_value
            else:
                provider_status = "offline"
                issues = issues + ["INVALID_PROVIDER_STATUS"]
    if issues == []:
        ok = True
    else:
        ok = False
    return {"ok": ok, "healthy": healthy, "mode": mode, "provider_status": provider_status, "issues": issues}
