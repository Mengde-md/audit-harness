from fastapi.testclient import TestClient

from app.main import app


def test_validation_errors_use_common_response_shape():
    with TestClient(app) as client:
        response = client.post("/api/v1/audits", json={})

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == 20002
    assert payload["msg"] == "请求参数校验失败"
    assert "errors" in payload["data"]


def test_request_id_is_returned():
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/audits/not-found",
            headers={"X-Request-ID": "request-test-123"},
        )

    assert response.headers["X-Request-ID"] == "request-test-123"
    assert response.json() == {
        "code": 20005,
        "msg": "资源不存在",
        "data": None,
    }


def test_business_routes_are_exposed():
    paths = set(app.openapi()["paths"])
    assert paths == {
        "/api/v1/audits",
        "/api/v1/audits/{audit_id}",
        "/api/v1/audits/{audit_id}/artifacts",
        "/api/v1/audits/{audit_id}/artifacts/{artifact_type}",
        "/api/v1/audits/{audit_id}/report",
    }
