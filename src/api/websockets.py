"""
WebSocket streams for real-time data push to browser clients.
"""

import asyncio
import logging
from datetime import datetime
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.dependencies import bot_state

router = APIRouter()
logger = logging.getLogger(__name__)


@router.websocket("/ws/prices")
async def ws_prices(websocket: WebSocket):
    """
    Stream real-time price ticks from the Binance exchange cache.
    Pushes updated prices every second.
    """
    await websocket.accept()
    logger.info("WebSocket client connected to /ws/prices")

    try:
        while True:
            bot = bot_state.get("bot")
            if bot and bot.data_exchange:
                prices = bot.data_exchange.realtime_prices
                if prices:
                    await websocket.send_json(
                        {
                            "type": "prices",
                            "timestamp": datetime.utcnow().isoformat(),
                            "data": prices,
                        }
                    )
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected from /ws/prices")
    except Exception as e:
        logger.error(f"WebSocket /ws/prices error: {e}")


@router.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    """
    Stream bot status updates every 5 seconds.
    Includes active trades count, circuit breaker state, and uptime.
    """
    await websocket.accept()
    logger.info("WebSocket client connected to /ws/status")

    try:
        while True:
            bot = bot_state.get("bot")
            if bot:
                status_info = bot.get_status_info()
                status_info["circuit_breaker_active"] = bot.circuit_breaker_active
                status_info["circuit_breaker_reason"] = bot.circuit_breaker_reason
                status_info["strategy"] = bot.strategy_manager.active_strategy
                status_info["timestamp"] = datetime.utcnow().isoformat()

                await websocket.send_json({"type": "status", "data": status_info})

            await asyncio.sleep(5)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected from /ws/status")
    except Exception as e:
        logger.error(f"WebSocket /ws/status error: {e}")
