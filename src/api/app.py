"""
FastAPI Application Factory for AkibaCryptoBot

Runs the trading bot and HTTP API server in a single asyncio event loop.
The bot is started as a background task during the FastAPI lifespan.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.dependencies import bot_state
from api.routes import status, trading, account, performance, risk, strategy
from api.websockets import router as ws_router

logger = logging.getLogger(__name__)


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
    bot = AkibaCryptoBot(config_path=config_path)

    if testnet:
        bot.config["exchange"]["sandbox"] = True

    # Override strategy if requested
    if strategy_override:
        try:
            bot.strategy_manager.add_strategy_by_name(strategy_override, bot.config)
            bot.strategy_manager.set_active_strategy(strategy_override)
            logger.info(f"Strategy override applied: {strategy_override}")
        except Exception as e:
            logger.error(f"Failed to load strategy {strategy_override}: {e}")

    # Store the bot instance globally for dependency injection
    bot_state["bot"] = bot

    # Launch the trading loop as a background task
    trading_task = asyncio.create_task(bot.start())
    logger.info("Trading bot started as background task")

    yield  # Server is running, bot is trading

    # Shutdown
    logger.info("Shutting down trading bot...")
    bot.stop()
    trading_task.cancel()
    try:
        await trading_task
    except asyncio.CancelledError:
        pass
    await bot.cleanup()
    logger.info("Trading bot shutdown complete")


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
    app.include_router(ws_router, tags=["WebSocket Streams"])

    # Mount the UI Static Files at the root
    import os
    from fastapi.staticfiles import StaticFiles

    ui_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui")
    if os.path.exists(ui_dir):
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")
    else:
        logger.warning(
            f"UI directory not found at {ui_dir}. Dashboard will not be available."
        )

    return app
