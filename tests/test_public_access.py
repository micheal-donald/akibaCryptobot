"""Verify the public boundary without starting the trading bot."""

import httpx
import pytest

from api.app import create_app


@pytest.mark.asyncio
async def test_public_dashboard_rejects_controls_before_bot_access(monkeypatch):
    monkeypatch.setenv("AKIBA_PUBLIC_READ_ONLY", "true")
    monkeypatch.setenv("AKIBA_OPERATOR_TOKEN", "valid-token")
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in (
            "/api/trading/pause",
            "/api/trading/resume",
            "/api/strategy/switch",
            "/api/model/train",
            "/api/trading/live/approve",
            "/api/trading/live/rollback",
            "/api/future-control",
        ):
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                response = await client.request(
                    method, path, headers={"X-Operator-Token": "valid-token"}
                )
                assert response.status_code == 403
                assert response.json()["detail"] == "public_dashboard_read_only"
        for path in ("/api/reconcile", "/api/reconcile/", "/api/future-control"):
            assert (await client.get(path)).status_code == 403
        assert (await client.get("/api/health")).status_code == 200
        page = await client.get("/")
        assert "PUBLIC · READ ONLY" in page.text
        assert '<select disabled id="strategy-select"' in page.text
        assert (await client.get("/js/app.js")).status_code == 200
        assert (await client.get("/openapi.json")).status_code == 404


@pytest.mark.asyncio
async def test_private_development_keeps_control_routes(monkeypatch):
    monkeypatch.delenv("AKIBA_PUBLIC_READ_ONLY", raising=False)
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/openapi.json")).status_code == 200
        assert "PUBLIC · READ ONLY" not in (await client.get("/")).text


@pytest.mark.asyncio
async def test_public_websocket_boundary():
    from api.public_access import PublicReadOnlyMiddleware

    forwarded = []
    sent = []

    async def downstream(scope, receive, send):
        forwarded.append(scope["path"])

    async def send(message):
        sent.append(message)

    middleware = PublicReadOnlyMiddleware(downstream)
    for path in ("/ws/status", "/ws/prices", "/ws/control"):
        await middleware({"type": "websocket", "path": path}, None, send)
    assert forwarded == ["/ws/status", "/ws/prices"]
    assert sent == [{"type": "websocket.close", "code": 1008}]
