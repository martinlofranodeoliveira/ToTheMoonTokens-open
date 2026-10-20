from fastapi import FastAPI
from fastapi.testclient import TestClient

from tothemoon_api.demo_agent import clear_demo_jobs, router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def setup_function():
    clear_demo_jobs()


def test_demo_job_happy_path():
    response = client.post("/api/demo/jobs/request", json={"artifact_type": "market_intel_brief"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "REQUESTED"
    job_id = payload["id"]

    response = client.post(f"/api/demo/jobs/{job_id}/pay")
    assert response.status_code == 200
    assert response.json()["state"] == "PAID"

    response = client.post(f"/api/demo/jobs/{job_id}/execute")
    assert response.status_code == 200
    assert response.json()["state"] == "REVIEW_PENDING"

    response = client.post(f"/api/demo/jobs/{job_id}/review?approve=true")
    assert response.status_code == 200
    assert response.json()["state"] == "REVIEWED"

    response = client.post(f"/api/demo/jobs/{job_id}/deliver")
    assert response.status_code == 200
    assert response.json()["state"] == "DELIVERED"


def test_demo_job_requires_payment_before_execution():
    response = client.post("/api/demo/jobs/request", json={"artifact_type": "delivery_packet"})
    job_id = response.json()["id"]

    response = client.post(f"/api/demo/jobs/{job_id}/execute")
    assert response.status_code == 409
