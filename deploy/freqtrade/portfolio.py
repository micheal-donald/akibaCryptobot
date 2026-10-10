"""Read-only projection of a private Freqtrade API. No trading engine or DB."""

import asyncio
import base64
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
ROOT = Path(__file__).parent
_lock = threading.Lock()
_cached = None
_expires = 0.0


def upstream(path):
    credentials = json.loads(
        Path(os.getenv("FT_SECRET_FILE", "/run/secrets/freqtrade-api.json")).read_text()
    )["api_server"]
    auth = base64.b64encode(
        f"{credentials['username']}:{credentials['password']}".encode()
    ).decode()
    base = os.getenv("FT_API_URL", "http://engine:8080/api/v1")
    request = urllib.request.Request(
        base + path, headers={"Authorization": "Basic " + auth}
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def project(config, health, profit, positions, history, whitelist, locks, now):
    last = health.get("last_process_ts")
    # The health endpoint uses epoch seconds, unlike trade timestamps.
    age = now - last if last else None
    healthy = (
        config.get("dry_run") is True
        and config.get("state") == "running"
        and age is not None
        and 0 <= age < 90
    )
    trade_fields = (
        "trade_id",
        "pair",
        "is_open",
        "open_date",
        "close_date",
        "open_rate",
        "close_rate",
        "current_rate",
        "stake_amount",
        "profit_abs",
        "profit_pct",
        "close_profit_abs",
        "close_profit",
        "stop_loss_abs",
        "exit_reason",
    )

    def clean(trade):
        return {key: trade.get(key) for key in trade_fields}

    return {
        "engine": "freqtrade",
        "version": config.get("version"),
        "mode": "dry_run" if config.get("dry_run") is True else "unexpected_mode",
        "healthy": healthy,
        "state": config.get("state"),
        "updated_at": now,
        "heartbeat_age_seconds": age,
        "strategy": config.get("strategy"),
        "timeframe": config.get("timeframe"),
        "pairs": whitelist.get("whitelist", []),
        "initial_balance": 1000,
        "stake_amount": config.get("stake_amount"),
        "max_open_trades": config.get("max_open_trades"),
        "stoploss": config.get("stoploss"),
        "realized_pnl": profit.get("profit_closed_coin"),
        "total_pnl": profit.get("profit_all_coin"),
        "closed_trades": profit.get("closed_trade_count", 0),
        "winning_trades": profit.get("winning_trades", 0),
        "losing_trades": profit.get("losing_trades", 0),
        "positions": [clean(t) for t in positions],
        "history": [clean(t) for t in history.get("trades", []) if not t["is_open"]],
        "entry_locks": [
            {k: lock.get(k) for k in ("pair", "reason", "lock_end_time")}
            for lock in locks.get("locks", [])
        ],
    }


def snapshot():
    global _cached, _expires
    with _lock:
        if time.monotonic() < _expires:
            return _cached
        try:
            _cached = project(
                upstream("/show_config"),
                upstream("/health"),
                upstream("/profit"),
                upstream("/status"),
                upstream("/trades?limit=20&order_by_id=true"),
                upstream("/whitelist"),
                upstream("/locks"),
                time.time(),
            )
        except Exception:
            # Do not expose credentials, upstream URLs, or internal exceptions.
            _cached = {
                "engine": "freqtrade",
                "healthy": False,
                "state": "unavailable",
                "mode": "unknown",
                "updated_at": time.time(),
                "error": "Trading engine data is temporarily unavailable",
            }
        _expires = time.monotonic() + 15
        return _cached


@app.middleware("http")
async def read_only(request: Request, call_next):
    if request.method not in {"GET", "HEAD"}:
        return JSONResponse({"error": "public_dashboard_read_only"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    )
    return response


@app.get("/api/health")
async def health():
    data = await asyncio.to_thread(snapshot)
    return JSONResponse(
        {"status": "ok" if data["healthy"] else "degraded", "engine": "freqtrade"},
        status_code=200 if data["healthy"] else 503,
    )


@app.get("/api/status")
@app.get("/api/portfolio")
async def portfolio():
    data = await asyncio.to_thread(snapshot)
    return JSONResponse(data, status_code=200 if data["healthy"] else 503)


@app.get("/")
async def index():
    return FileResponse(ROOT / "public/index.html")


@app.get("/portfolio.js")
async def javascript():
    return FileResponse(ROOT / "public/portfolio.js", media_type="text/javascript")


@app.get("/portfolio.css")
async def stylesheet():
    return FileResponse(ROOT / "public/portfolio.css", media_type="text/css")
