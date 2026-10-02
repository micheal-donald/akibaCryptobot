"""
FastAPI Application Factory for AkibaCryptoBot

Runs the trading bot and HTTP API server in a single asyncio event loop.
The bot is started as a background task during the FastAPI lifespan.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from api.dependencies import bot_state
from api.routes import (
    status,
    trading,
    account,
    performance,
    risk,
    strategy,
    model,
    market,
)
from api.websockets import router as ws_router

logger = logging.getLogger(__name__)
BOT_SHUTDOWN_TIMEOUT_SECONDS = 90.0


def supervise_trading_task(app: FastAPI, task: asyncio.Task) -> None:
    """Stop the API process when its trading loop unexpectedly exits."""
    if getattr(app.state, "shutting_down", False):
        return
    logger.error(
        "Trading loop exited; requesting server shutdown for supervisor recovery"
    )
    shutdown_callback = getattr(app.state, "request_shutdown", None)
    if callable(shutdown_callback):
        shutdown_callback()


def _compute_asset_version(ui_dir: str) -> str:
    """Version string stamped into asset URLs for cache busting.

    Prefers the deployed git SHA (written by the deploy scripts); falls
    back to the newest asset mtime so local dev edits also bust caches.
    """
    repo_root = os.path.dirname(os.path.dirname(ui_dir))
    sha_file = os.path.join(repo_root, ".deploy", "current_sha")
    try:
        with open(sha_file, encoding="utf-8") as f:
            sha = f.read().strip()
        if sha:
            return sha[:7]
    except OSError:
        pass

    latest_mtime = 0
    for root, _, files in os.walk(ui_dir):
        for name in files:
            try:
                latest_mtime = max(
                    latest_mtime, int(os.path.getmtime(os.path.join(root, name)))
                )
            except OSError:
                continue
    return str(latest_mtime)


async def shutdown_trading_bot(
    bot,
    trading_task: asyncio.Task,
    *,
    timeout_seconds: float = BOT_SHUTDOWN_TIMEOUT_SECONDS,
) -> None:
    """Stop the bot cooperatively before falling back to task cancellation."""

    logger.info("Shutting down trading bot...")
    bot.stop()

    try:
        await asyncio.wait_for(asyncio.shield(trading_task), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        logger.warning(
            "Trading bot did not stop within %.1fs, cancelling task",
            timeout_seconds,
        )
        trading_task.cancel()
        try:
            await trading_task
        except asyncio.CancelledError:
            pass
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error("Trading bot exited with error during shutdown: %s", exc)

    logger.info("Trading bot shutdown complete")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the trading bot lifecycle alongside the FastAPI server.
    Bot starts when the server boots; shuts down gracefully on exit.
    """
    from bot import AkibaCryptoBot

    # Retrieve config path from app state (set by start_bot.py)
    config_path = getattr(app.state, "config_path", "config/config.json")
    strategy_override = getattr(app.state, "strategy_override", None)
    testnet = getattr(app.state, "testnet", False)

    # Initialize the bot
    # Uvicorn owns process signals in API mode; do not replace its graceful
    # shutdown handlers with handlers that only stop the trading loop.
    bot = AkibaCryptoBot(config_path=config_path, register_signal_handlers=False)

    if testnet:
        bot.config["exchange"]["sandbox"] = True

    # Override strategy if requested (skip if already active from config)
    if strategy_override and strategy_override != bot.strategy_manager.active_strategy:
        try:
            bot.strategy_manager.add_strategy_by_name(strategy_override, bot.config)
            bot.strategy_manager.set_active_strategy(strategy_override)
            logger.info(f"Strategy override applied: {strategy_override}")
        except Exception as e:
            logger.error(f"Failed to load strategy {strategy_override}: {e}")
    elif strategy_override:
        logger.info(
            f"Strategy '{strategy_override}' already active from config, skipping redundant reload"
        )

    # Store the bot instance globally for dependency injection
    bot_state["bot"] = bot

    # Launch the trading loop as a background task
    trading_task = asyncio.create_task(bot.start())
    app.state.trading_task = trading_task
    app.state.shutting_down = False
    trading_task.add_done_callback(lambda task: supervise_trading_task(app, task))
    logger.info("Trading bot started as background task")

    try:
        yield  # Server is running, bot is trading
    finally:
        app.state.shutting_down = True
        await shutdown_trading_bot(bot, trading_task)
        bot_state["bot"] = None


def create_app(
    config_path: str = "config/config.json",
    strategy_override: str = None,
    testnet: bool = False,
) -> FastAPI:
    """
    Create and configure the FastAPI application.
    """
    app = FastAPI(
        title="AkibaCryptoBot API",
        description="REST & WebSocket API for the AkibaCryptoBot HFT trading engine",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Store startup config on the app for the lifespan to read
    app.state.config_path = config_path
    app.state.strategy_override = strategy_override
    app.state.testnet = testnet

    # CORS — allow all origins for local/dev usage
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register route modules
    app.include_router(status.router, prefix="/api", tags=["Status"])
    app.include_router(account.router, prefix="/api", tags=["Account"])
    app.include_router(trading.router, prefix="/api", tags=["Trading"])
    app.include_router(performance.router, prefix="/api", tags=["Performance"])
    app.include_router(risk.router, prefix="/api", tags=["Risk Management"])
    app.include_router(strategy.router, prefix="/api", tags=["Strategy"])
    app.include_router(model.router, prefix="/api", tags=["Model Health"])
    app.include_router(market.router, prefix="/api", tags=["Market Data"])
    app.include_router(ws_router, tags=["WebSocket Streams"])

    # Mount the UI Static Files at the root
    import os
    from fastapi.staticfiles import StaticFiles

    ui_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui")
    if os.path.exists(ui_dir):
        asset_version = _compute_asset_version(ui_dir)
        index_path = os.path.join(ui_dir, "index.html")

        # Serve index.html ourselves: stamp asset URLs with the deploy
        # version (cache busting) and forbid caching of the HTML shell so
        # browsers always pick up new asset URLs after a deploy.
        @app.get("/", include_in_schema=False)
        async def serve_index():
            with open(index_path, encoding="utf-8") as f:
                html = f.read().replace("__ASSET_VERSION__", asset_version)
            return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")
    else:
        logger.warning(
            f"UI directory not found at {ui_dir}. Dashboard will not be available."
        )

    return app
