"""Read-only boundary for the public portfolio deployment."""

from starlette.responses import JSONResponse


PUBLIC_READ_PATHS = frozenset(
    f"/api/{path}"
    for path in (
        "health",
        "status",
        "balance",
        "config",
        "trades",
        "metrics",
        "equity-curve",
        "trades/history",
        "strategy/performance",
        "low-latency",
        "risk",
        "correlations",
        "heatmap",
        "circuit-breaker",
        "strategy",
        "model/status",
        "model/train/status",
        "ohlcv",
    )
)


class PublicReadOnlyMiddleware:
    """Reject writes before dependencies run, even with an operator token.

    Explicit read paths keep future GET control endpoints private. Host headers
    and client-supplied headers cannot bypass this deployment-wide boundary.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] == "websocket":
            if path not in {"/ws/prices", "/ws/status"}:
                await send({"type": "websocket.close", "code": 1008})
                return
        elif scope["type"] == "http":
            method = scope["method"]
            allowed = method in {"GET", "HEAD", "OPTIONS"}
            if path.startswith("/api/"):
                allowed = allowed and path in PUBLIC_READ_PATHS
            if not allowed:
                response = JSONResponse(
                    {"detail": "public_dashboard_read_only"}, status_code=403
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
