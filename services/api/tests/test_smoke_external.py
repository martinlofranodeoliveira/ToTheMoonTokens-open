import pytest
from fastapi.testclient import TestClient

from tothemoon_api.main import app


@pytest.mark.skip(reason="manual smoke")
def test_arc_job_dashboard_smoke() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/arc/jobs",
        json={
            "task_id": "GH-40",
            "agent_id": "dev",
            "action": "complete",
            "evidence": {"pr": "1"},
        },
    )
    assert response.status_code == 200

    dashboard_response = client.get("/api/dashboard")
    assert dashboard_response.status_code == 200
    assert len(dashboard_response.json()["arc_jobs"]) >= 1
