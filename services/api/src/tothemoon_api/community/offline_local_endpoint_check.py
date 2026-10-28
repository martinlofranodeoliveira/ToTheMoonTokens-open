"""Synthetic offline local endpoint checker for local mocks."""
EXPECTED_API_HOST = "127.0.0.1"
EXPECTED_API_PORT = 8010
EXPECTED_WEB_PORT = 4173
CODE_INVALID_PAYLOAD = "INVALID_PAYLOAD"
CODE_INVALID_API_HOST = "INVALID_API_HOST"
CODE_INVALID_API_PORT = "INVALID_API_PORT"
CODE_INVALID_WEB_PORT = "INVALID_WEB_PORT"


def run_feature(payload):
    if not isinstance(payload, dict):
        return {"api_host": "", "api_port": 0, "web_port": 0, "issues": ["INVALID_PAYLOAD"], "ok": False}
    issues = []
    if "api_host" in payload and payload["api_host"] == EXPECTED_API_HOST:
        out_host = payload["api_host"]
    else:
        out_host = ""
        issues.append(CODE_INVALID_API_HOST)
    if "api_port" in payload and type(payload["api_port"]) is int and payload["api_port"] == EXPECTED_API_PORT:
        out_api_port = payload["api_port"]
    else:
        out_api_port = 0
        issues.append(CODE_INVALID_API_PORT)
    if "web_port" in payload and type(payload["web_port"]) is int and payload["web_port"] == EXPECTED_WEB_PORT:
        out_web_port = payload["web_port"]
    else:
        out_web_port = 0
        issues.append(CODE_INVALID_WEB_PORT)
    ok = len(issues) == 0
    return {"api_host": out_host, "api_port": out_api_port, "web_port": out_web_port, "issues": issues, "ok": ok}
