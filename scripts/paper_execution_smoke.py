"""Testnet-only buy, durable restart, and close using an isolated audit database.

Uses virtual Binance Spot Testnet funds. Run with the production paper bot
stopped. The test database is separate from portfolio/strategy history.
"""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from adapters.binance.execution_gateway import BinanceExecutionGateway
from adapters.binance.user_data_gateway import BinanceUserDataGateway
from adapters.sqlite.runtime_store import SQLiteRuntimeStore
from domain.events import OrderIntentPayload
from exchange.binance_client import BinanceClient
from runtime.app import RuntimeApp
from runtime.config import RuntimeConfig
from services.recovery_service import RestartRecoveryService


class EmptyLegacyStore:
    async def load_open_trades(self):
        return {}


def make_app(client, path):
    app = RuntimeApp.build_phase4(
        RuntimeConfig(
            symbols=("BNB/USDT",),
            kline_intervals=("15m",),
            deployment_profile="paper_runtime_primary",
            sandbox_execution=True,
        ),
        execution_gateway=BinanceExecutionGateway(client=client),
        user_stream_gateway=BinanceUserDataGateway(
            api_key=client.api_key, api_secret=client.secret_key, testnet=True
        ),
        runtime_store=SQLiteRuntimeStore(str(path)),
        max_open_positions=1,
    )
    app.recovery_service = RestartRecoveryService(
        name="recovery",
        trade_store=EmptyLegacyStore(),
        runtime_store=app.runtime_store,
        execution_gateway=app.execution_service.execution_gateway,
        execution_service=app.execution_service,
        position_service=app.position_service,
    )
    return app


async def wait_for(predicate, timeout=30):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.1)

    await asyncio.wait_for(poll(), timeout)


async def flush(app):
    await app.event_bus.join()
    assert await app.persistence_service.wait_until_drained(10)


async def run(path):
    if path.exists():
        raise RuntimeError("Use a new smoke database; refusing to reuse test history")
    client = BinanceClient(sandbox=True)
    assert client.sandbox and "testnet" in str(client.exchange.urls["api"])
    app = make_app(client, path)
    try:
        await app.start()
        ticker = await client.get_ticker("BNB/USDT")
        price = float(ticker["ask"] or ticker["last"])
        quantity = float(client.exchange.amount_to_precision("BNB/USDT", 15 / price))
        valid, quantity = client.validate_and_adjust_order("BNB/USDT", quantity, price)
        assert valid
        entry = OrderIntentPayload(
            intent_id="smoke-buy-" + uuid4().hex[:16],
            symbol="BNB/USDT",
            side="buy",
            order_type="market",
            quantity=quantity,
            limit_price=price,
            reason="isolated_testnet_smoke",
        )
        submitted = await app.submit_order_intent(entry)
        assert submitted is not None
        await wait_for(
            lambda: app.position_service.get_position_for_symbol("BNB/USDT") is not None
        )
        position = app.position_service.get_position_for_symbol("BNB/USDT")
        assert position.open_qty > 0
        position_id, open_qty = position.position_id, position.open_qty
        await flush(app)
        assert len(await app.runtime_store.fetch_runtime_fills()) > 0
        stream_events = app.get_telemetry_snapshot()["user_stream"]["events_received"]
        await app.stop()

        app = make_app(client, path)
        await app.start()
        recovered = app.position_service.get_position_state(position_id)
        assert recovered is not None and abs(recovered.open_qty - open_qty) < 1e-12
        assert app.recovery_report.processed_trades == 1
        ticker = await client.get_ticker("BNB/USDT")
        exit_price = float(ticker["bid"] or ticker["last"])
        await app.submit_order_intent(
            OrderIntentPayload(
                intent_id="smoke-exit-" + uuid4().hex[:16],
                symbol="BNB/USDT",
                side="sell",
                order_type="market",
                quantity=open_qty,
                limit_price=exit_price,
                reduce_only=True,
                reason="isolated_testnet_smoke_close",
            )
        )
        await wait_for(lambda: recovered.open_qty == 0)
        await flush(app)
        rows = await app.runtime_store.fetch_runtime_positions()
        assert len(rows) == 1 and rows[0]["status"] == "closed"
        result = {
            "sandbox": True,
            "database": str(path),
            "buy_order": submitted.exchange_order_id,
            "filled_quantity": open_qty,
            "restart_recovered": True,
            "closed": True,
            "websocket_events_before_restart": stream_events,
            "fill_rows": len(await app.runtime_store.fetch_runtime_fills()),
        }
        path.with_suffix(".result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    finally:
        await app.stop()
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    asyncio.run(run(parser.parse_args().database))
