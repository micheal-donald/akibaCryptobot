"""Authenticated Spot WS API subscription replacing retired listenKey streams."""

import asyncio
import hashlib
import hmac
import json
import logging

import websockets

logger = logging.getLogger(__name__)


class SpotUserStream:
    def __init__(self, api_key, api_secret, testnet, on_event):
        self.api_key = api_key
        self.api_secret = api_secret
        self.on_event = on_event
        self.url = (
            "wss://ws-api.testnet.binance.vision/ws-api/v3"
            if testnet
            else "wss://ws-api.binance.com/ws-api/v3"
        )
        self.connected = False
        self.reconnects = 0
        self.events_received = 0
        self.ready = asyncio.Event()

    def subscription_request(self, timestamp):
        params = {"apiKey": self.api_key, "timestamp": timestamp, "recvWindow": 10000}
        payload = "&".join(f"{key}={params[key]}" for key in sorted(params))
        params["signature"] = hmac.new(
            self.api_secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return {
            "id": "subscribe",
            "method": "userDataStream.subscribe.signature",
            "params": params,
        }

    def get_status(self):
        return {
            "connected": self.connected,
            "reconnects": self.reconnects,
            "events_received": self.events_received,
            "transport": "spot_ws_api",
        }

    async def run(self):
        while True:
            try:
                async with websockets.connect(
                    self.url, open_timeout=10, ping_interval=20, ping_timeout=20
                ) as socket:
                    await socket.send(json.dumps({"id": "time", "method": "time"}))
                    response = json.loads(await asyncio.wait_for(socket.recv(), 15))
                    timestamp = response["result"]["serverTime"]
                    await socket.send(json.dumps(self.subscription_request(timestamp)))
                    response = json.loads(await asyncio.wait_for(socket.recv(), 15))
                    if response.get("status") != 200:
                        code = response.get("error", {}).get("code")
                        raise RuntimeError(
                            f"Spot user subscription rejected (code={code})"
                        )
                    self.connected = True
                    self.ready.set()
                    logger.info("Spot WS API user subscription established")
                    async for frame in socket:
                        message = json.loads(frame)
                        event = message.get("event")
                        if not isinstance(event, dict):
                            continue
                        if event.get("e") in {
                            "serverShutdown",
                            "eventStreamTerminated",
                        }:
                            break
                        self.events_received += 1
                        await self.on_event(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Never log subscription requests or raw authenticated responses.
                logger.warning("Spot user stream interrupted (%s)", type(exc).__name__)
            finally:
                self.connected = False
            self.reconnects += 1
            await asyncio.sleep(5)
