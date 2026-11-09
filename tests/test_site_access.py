from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.middleware.site_access import SiteAccessMiddleware


def client_for_gate():
    app = FastAPI()
    app.add_middleware(SiteAccessMiddleware, settings=Settings(APP_ACCESS_CODE="888"))

    @app.get("/api/private")
    async def private():
        return {"secret": True}

    return TestClient(app, base_url="https://testserver")


def test_code_gate_and_secure_cookie():
    with client_for_gate() as client:
        assert client.get("/api/private").status_code == 401
        assert client.get("/openapi.json").status_code == 401
        assert client.get("/api/site-access").json() == {"required": True, "granted": False}
        assert client.post("/api/site-access", json={"code": "wrong"}).status_code == 401
        response = client.post("/api/site-access", json={"code": "888"})
        assert response.status_code == 200
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "Secure" in response.headers["set-cookie"]
        assert client.get("/api/private").json() == {"secret": True}
        assert client.get("/api/site-access").json()["granted"] is True
        client.delete("/api/site-access")
        assert client.get("/api/private").status_code == 401


def test_tampered_cookie_and_bounded_guesses():
    with client_for_gate() as client:
        client.cookies.set("audit_site_access", "9999999999.fake.bad")
        assert client.get("/api/private").status_code == 401
        for _ in range(10):
            assert client.post("/api/site-access", json={"code": "bad"}).status_code == 401
        assert client.post("/api/site-access", json={"code": "888"}).status_code == 429


def test_oversized_code_body_is_rejected():
    with client_for_gate() as client:
        assert client.post("/api/site-access", content="x" * 1025).status_code == 413


async def test_production_refuses_audit_without_sandbox(monkeypatch):
    from app.api.v1.routes import audits
    from app.schemas.audit import AuditCreateRequest
    from fastapi import BackgroundTasks

    monkeypatch.setattr(audits, "sandbox_readiness", AsyncMock(return_value={
        "sandbox_available": False, "sandbox_reason": "Docker unavailable",
    }))
    service = AsyncMock()
    response = await audits.create_audit(
        AuditCreateRequest(requester_id="site", repo="https://github.com/example/test.git",
                           audit_mode="full", llm_policy="disabled", api_document_markdown="API"),
        BackgroundTasks(), service, Settings(app_env="production"),
    )
    assert response.status_code == 503
    service.create.assert_not_awaited()
