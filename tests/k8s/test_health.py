"""11장 앱 내부: GET /health (docs/k8s.md ④). DB·Redis·reset_db 를 쓰지 않는다."""
import pytest
from fastapi.testclient import TestClient


@pytest.mark.story("US-07", "k8s /health")
def test_health_returns_ok_without_any_headers():
    from backend.app.main import create_app

    client = TestClient(create_app())  # lifespan 을 열지 않는다
    r = client.get("/health")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok"}
