import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient
from google.genai import types

from tothemoon_api.agent_chat import GeminiMarketplaceAgent
from tothemoon_api.config import get_settings
from tothemoon_api.main import app
from tothemoon_api.models import (
    AgentChatRequest,
    AgentChatResponse,
    AgentToolEvent,
)
from tothemoon_api.payments import clear_payment_intents

client = TestClient(app)
PASSWORD = "correct horse battery staple"


def setup_function():
    clear_payment_intents()


def _auth_headers(prefix: str = "agent-chat") -> dict[str, str]:
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    signup = client.post("/api/v1/auth/signup", json={"email": email, "password": PASSWORD})
    assert signup.status_code == 201
    login = client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_agent_chat_route_returns_structured_payload(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    def fake_respond(self, request):
        assert request.message == "explain the approval queue"
        return AgentChatResponse(
            reply="Mode 1 runs paper automatically; Mode 2 waits for approval.",
            events=[
                AgentToolEvent(
                    name="bot_status",
                    args={},
                    response={"runtime_mode": "paper", "order_submission_enabled": False},
                )
            ],
            orders=[],
            settlement_auth_mode="manual",
            autonomous_payments_enabled=False,
        )

    monkeypatch.setattr("tothemoon_api.agent_chat.GeminiMarketplaceAgent.respond", fake_respond)

    response = client.post(
        "/api/agent/chat",
        json={"message": "explain the approval queue", "history": []},
        headers=_auth_headers("agent-route"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "Mode 1 runs paper automatically; Mode 2 waits for approval."
    assert payload["events"][0]["name"] == "bot_status"
    assert payload["orders"] == []
    assert payload["settlement_auth_mode"] == "manual"
    assert payload["autonomous_payments_enabled"] is False


def test_agent_chat_route_uses_org_gemini_credential(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "global-gemini-key")
    created = {}
    headers = _auth_headers("agent-org-gemini")
    saved = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        json={"api_key": "org-gemini-key-123456", "model": "gemini-3-pro-preview"},
        headers=headers,
    )
    assert saved.status_code == 200

    class FakeClient:
        def __init__(self, api_key=None, **kwargs):
            created["api_key"] = api_key
            created["kwargs"] = kwargs

    def fake_respond(self, request):
        assert self.gemini_model == "gemini-3-pro-preview"
        return AgentChatResponse(
            reply="Using account Gemini key.",
            events=[],
            orders=[],
            settlement_auth_mode="manual",
            autonomous_payments_enabled=False,
        )

    monkeypatch.setattr("tothemoon_api.agent_chat.genai.Client", FakeClient)
    monkeypatch.setattr("tothemoon_api.agent_chat.GeminiMarketplaceAgent.respond", fake_respond)

    response = client.post(
        "/api/agent/chat",
        json={"message": "status", "history": []},
        headers=headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "Using account Gemini key."
    assert payload["gemini"] == {
        "configured": True,
        "source": "organization",
        "model": "gemini-3-pro-preview",
        "key_hint": "org-ge...3456",
    }
    assert created == {"api_key": "org-gemini-key-123456", "kwargs": {}}
    assert "org-gemini-key-123456" not in response.text


def test_agent_chat_route_falls_back_to_global_gemini_credential(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "global-gemini-key")
    created = {}

    class FakeClient:
        def __init__(self, api_key=None, **kwargs):
            created["api_key"] = api_key
            created["kwargs"] = kwargs

    def fake_respond(self, request):
        assert self.gemini_model == "gemini-3.1-pro-preview"
        return AgentChatResponse(
            reply="Using fallback Gemini key.",
            events=[],
            orders=[],
            settlement_auth_mode="manual",
            autonomous_payments_enabled=False,
        )

    monkeypatch.setattr("tothemoon_api.agent_chat.genai.Client", FakeClient)
    monkeypatch.setattr("tothemoon_api.agent_chat.GeminiMarketplaceAgent.respond", fake_respond)

    response = client.post(
        "/api/agent/chat",
        json={"message": "status", "history": []},
        headers=_auth_headers("agent-global-gemini"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "Using fallback Gemini key."
    assert payload["gemini"] == {
        "configured": True,
        "source": "global",
        "model": "gemini-3.1-pro-preview",
    }
    assert created == {"api_key": "global-gemini-key", "kwargs": {}}
    assert "global-gemini-key" not in response.text


def test_agent_status_reports_org_gemini_credential_without_secret(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "global-gemini-key")
    headers = _auth_headers("agent-status-org")
    secret = "org-status-gemini-key-123456"
    saved = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        json={"api_key": secret, "model": "gemini-3-pro-preview"},
        headers=headers,
    )
    assert saved.status_code == 200

    response = client.get("/api/agent/status", headers=headers)

    assert response.status_code == 200
    assert secret not in response.text
    assert "global-gemini-key" not in response.text
    assert response.json()["gemini"] == {
        "configured": True,
        "source": "organization",
        "model": "gemini-3-pro-preview",
        "key_hint": "org-st...3456",
    }


def test_agent_status_reports_global_fallback_without_secret(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "global-status-gemini-key")

    response = client.get("/api/agent/status", headers=_auth_headers("agent-status-global"))

    assert response.status_code == 200
    assert "global-status-gemini-key" not in response.text
    assert response.json()["gemini"] == {
        "configured": True,
        "source": "global",
        "model": "gemini-3.1-pro-preview",
    }


def test_agent_status_reports_vertexai_without_project_identifier(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GEMINI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GEMINI_VERTEX_PROJECT", "private-vertex-project")
    monkeypatch.setenv("GEMINI_VERTEX_LOCATION", "us-central1")

    response = client.get("/api/agent/status", headers=_auth_headers("agent-status-vertex"))

    assert response.status_code == 200
    assert response.json()["gemini"] == {
        "configured": True,
        "source": "vertexai",
        "model": "gemini-3.1-pro-preview",
        "vertex_project_configured": True,
        "vertex_location": "us-central1",
    }
    assert "private-vertex-project" not in response.text


def test_agent_status_requires_authentication():
    response = client.get("/api/agent/status")

    assert response.status_code == 401


def test_agent_chat_route_does_not_use_other_org_gemini_credential(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "global-gemini-key")
    tenant_a_headers = _auth_headers("agent-org-a-gemini")
    tenant_b_headers = _auth_headers("agent-org-b-gemini")
    saved = client.post(
        "/api/v1/saas/provider-credentials/gemini",
        json={"api_key": "tenant-a-gemini-key-123456", "model": "gemini-3-pro-preview"},
        headers=tenant_a_headers,
    )
    assert saved.status_code == 200
    created = {}

    class FakeClient:
        def __init__(self, api_key=None, **kwargs):
            created["api_key"] = api_key
            created["kwargs"] = kwargs

    def fake_respond(self, request):
        assert self.gemini_model == "gemini-3.1-pro-preview"
        return AgentChatResponse(
            reply="Tenant B used fallback Gemini key.",
            events=[],
            orders=[],
            settlement_auth_mode="manual",
            autonomous_payments_enabled=False,
        )

    monkeypatch.setattr("tothemoon_api.agent_chat.genai.Client", FakeClient)
    monkeypatch.setattr("tothemoon_api.agent_chat.GeminiMarketplaceAgent.respond", fake_respond)

    response = client.post(
        "/api/agent/chat",
        json={"message": "status", "history": []},
        headers=tenant_b_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "Tenant B used fallback Gemini key."
    assert payload["gemini"]["source"] == "global"
    assert "key_hint" not in payload["gemini"]
    assert created == {"api_key": "global-gemini-key", "kwargs": {}}
    assert "tenant-a-gemini-key-123456" not in response.text


def test_agent_chat_route_requires_authentication(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    response = client.post(
        "/api/agent/chat",
        json={"message": "explain mode 1", "history": []},
    )

    assert response.status_code == 401


def test_gemini_agent_runs_manual_tool_call_loop_for_bot_status(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    calls = []

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            calls.append(
                {
                    "model": model,
                    "contents": contents,
                    "config": config,
                }
            )
            if len(calls) == 1:
                return SimpleNamespace(
                    function_calls=[
                        types.FunctionCall(
                            id="call-1",
                            name="bot_status",
                            args={},
                        )
                    ],
                    candidates=[
                        SimpleNamespace(
                            content=types.Content(
                                role="model",
                                parts=[
                                    types.Part.from_function_call(
                                        name="bot_status",
                                        args={},
                                    )
                                ],
                            )
                        )
                    ],
                    text=None,
                )
            return SimpleNamespace(
                function_calls=[],
                candidates=[],
                text="Mode 1 is paper-only and order submission is blocked.",
            )

    class FakeClient:
        def __init__(self, api_key):
            assert api_key == "test-gemini-key"
            self.models = FakeModels()

    monkeypatch.setattr("tothemoon_api.agent_chat.genai.Client", FakeClient)

    agent = GeminiMarketplaceAgent(settings=get_settings())
    response = agent.respond(AgentChatRequest(message="What is the bot status?", history=[]))

    assert response.reply == "Mode 1 is paper-only and order submission is blocked."
    assert response.events[0].name == "bot_status"
    assert response.events[0].response["order_submission_enabled"] is False
    assert len(calls) == 2
    assert calls[1]["contents"][-1].role == "tool"
    assert calls[0]["config"].automatic_function_calling.disable is True
    assert "Never suggest artifact purchases" in calls[0]["config"].system_instruction


def test_gemini_agent_blocks_checkout_tool_calls(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    calls = []

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            calls.append(
                {
                    "model": model,
                    "contents": contents,
                    "config": config,
                }
            )
            if len(calls) == 1:
                return SimpleNamespace(
                    function_calls=[
                        types.FunctionCall(
                            id="call-1",
                            name="create_checkout",
                            args={"artifact_id": "artifact_delivery_packet"},
                        )
                    ],
                    candidates=[
                        SimpleNamespace(
                            content=types.Content(
                                role="model",
                                parts=[
                                    types.Part.from_function_call(
                                        name="create_checkout",
                                        args={"artifact_id": "artifact_delivery_packet"},
                                    )
                                ],
                            )
                        )
                    ],
                    text=None,
                )
            return SimpleNamespace(
                function_calls=[],
                candidates=[],
                text="Checkout is not supported in the SaaS copilot.",
            )

    class FakeClient:
        def __init__(self, api_key):
            assert api_key == "test-gemini-key"
            self.models = FakeModels()

    monkeypatch.setattr("tothemoon_api.agent_chat.genai.Client", FakeClient)

    agent = GeminiMarketplaceAgent(settings=get_settings())
    response = agent.respond(AgentChatRequest(message="Create checkout", history=[]))

    assert response.reply == "Checkout is not supported in the SaaS copilot."
    assert response.events[0].name == "create_checkout"
    assert "Unknown or blocked tool" in response.events[0].response["error"]
    assert response.orders == []
    assert "Never suggest artifact purchases" in calls[0]["config"].system_instruction


def test_gemini_agent_can_use_vertexai(monkeypatch):
    created = {}

    class FakeClient:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr("tothemoon_api.agent_chat.genai.Client", FakeClient)

    settings = get_settings()
    settings.gemini_api_key = ""
    settings.gemini_use_vertexai = True
    settings.gemini_vertex_project = "demo-project"
    settings.gemini_vertex_location = "us-central1"

    GeminiMarketplaceAgent(settings=settings)

    assert created == {
        "vertexai": True,
        "project": "demo-project",
        "location": "us-central1",
    }
