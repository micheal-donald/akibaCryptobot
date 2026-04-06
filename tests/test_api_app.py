import asyncio
from datetime import datetime, timezone

import pytest

from api.app import shutdown_trading_bot
from api.routes.status import get_status
from api.routes.trading import get_active_trades
from operations.training_jobs import TrainingJobRecord, TrainingJobSnapshot
from runtime.view_models import (
    ProjectedPositionView,
    ProjectionSource,
    RuntimeProjection,
)


class FakeBot:
    def __init__(self):
        self.stop_calls = 0
        self.stop_event = asyncio.Event()

    def stop(self):
        self.stop_calls += 1
        self.stop_event.set()


@pytest.mark.asyncio
async def test_shutdown_trading_bot_waits_for_cooperative_exit():
    bot = FakeBot()
    cleanup_calls = 0

    async def run_bot():
        nonlocal cleanup_calls
        try:
            await bot.stop_event.wait()
        finally:
            cleanup_calls += 1

    trading_task = asyncio.create_task(run_bot())

    await shutdown_trading_bot(bot, trading_task, timeout_seconds=0.1)

    assert bot.stop_calls == 1
    assert trading_task.done() is True
    assert cleanup_calls == 1


@pytest.mark.asyncio
async def test_shutdown_trading_bot_cancels_when_task_does_not_exit():
    bot = FakeBot()

    async def run_forever():
        while True:
            await asyncio.sleep(1)

    trading_task = asyncio.create_task(run_forever())

    await shutdown_trading_bot(bot, trading_task, timeout_seconds=0.01)

    assert bot.stop_calls == 1
    assert trading_task.cancelled() is True


NOW = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)


def make_projection(
    *,
    source: ProjectionSource,
    runtime_mode: str = "legacy",
    active_positions: tuple[ProjectedPositionView, ...] = (),
    open_order_count: int = 0,
    pending_exit_count: int = 0,
    rejected_order_count: int = 0,
) -> RuntimeProjection:
    return RuntimeProjection(
        source=source,
        runtime_mode=runtime_mode,
        runtime_enabled=runtime_mode != "legacy",
        runtime_is_running=runtime_mode != "legacy",
        runtime_is_healthy=True,
        runtime_owns_entries=runtime_mode in {"runtime_entries", "runtime_primary"},
        runtime_owns_exits=runtime_mode == "runtime_primary",
        active_position_count=len(active_positions),
        protected_position_count=sum(
            1 for position in active_positions if position.status == "protected"
        ),
        pending_exit_count=pending_exit_count,
        open_order_count=open_order_count,
        rejected_order_count=rejected_order_count,
        positions=active_positions,
        orders=(),
    )


def make_projected_position(symbol: str = "BTC/USDT") -> ProjectedPositionView:
    return ProjectedPositionView(
        position_id="pos-1",
        symbol=symbol,
        side="buy",
        status="open",
        open_qty=0.25,
        requested_qty=0.25,
        avg_entry_price=65000.0,
        avg_exit_price=None,
        stop_loss=64000.0,
        take_profit=67000.0,
        trailing_stop=None,
        entry_order_ids=("entry-1",),
        protective_order_ids=(),
        fees_paid=0.0,
        opened_at=NOW,
        closed_at=None,
        updated_at=NOW,
    )


class FakeStatusBot:
    def __init__(
        self,
        projection: RuntimeProjection,
        *,
        configured_deployment_profile: str | None = None,
        trading_mode: str = "paper",
        native_shadow_report: dict[str, object] | None = None,
        training_snapshot: TrainingJobSnapshot | None = None,
    ):
        self._projection = projection
        self._configured_deployment_profile = configured_deployment_profile
        self._native_shadow_report = native_shadow_report
        self._training_snapshot = training_snapshot or TrainingJobSnapshot(
            current_job=None,
            last_job=None,
            can_start_training=True,
            active_job_count=0,
        )
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = ""
        self.config = {"exchange": {"sandbox": trading_mode != "live"}}
        self.strategy_manager = type(
            "StrategyManager", (), {"active_strategy": "ensemble"}
        )()
        self._active_trades = [{"symbol": "BTC/USDT"}]
        self._trading_mode = trading_mode

    def get_status_info(self):
        return {
            "active_trades": len(self._active_trades),
            "daily_pnl": 0.0,
            "uptime": "0:00:10",
            "is_running": True,
            "trading_paused": False,
            "trading_mode": self._trading_mode,
            "runtime_mode": self._projection.runtime_mode,
            "runtime_is_running": self._projection.runtime_is_running,
            "runtime_owns_entries": self._projection.runtime_owns_entries,
            "runtime_owns_exits": self._projection.runtime_owns_exits,
        }

    def get_native_shadow_report(self):
        return self._native_shadow_report

    def get_runtime_visibility_info(self):
        return {
            "configured_deployment_profile": self._configured_deployment_profile,
            "effective_runtime_mode": self._projection.runtime_mode,
            "effective_runtime_enabled": self._projection.runtime_enabled,
            "effective_runtime_owns_entries": self._projection.runtime_owns_entries,
            "effective_runtime_owns_exits": self._projection.runtime_owns_exits,
            "runtime_mode_source": (
                "deployment_profile"
                if self._configured_deployment_profile is not None
                else "legacy_default"
            ),
        }

    def get_runtime_projection(self) -> RuntimeProjection:
        return self._projection

    def get_runtime_telemetry_info(self):
        return {
            "total_events": 2,
            "event_counts": {"market.kline": 1},
            "metric_counts": {"queue_depth.runtime.event_bus": 1},
            "queue_depths": {"runtime.event_bus": 1},
            "last_seen_at": NOW.isoformat(),
            "latency": {
                "event_age_ms": {
                    "count": 1,
                    "total_ms": 25.0,
                    "average_ms": 25.0,
                    "min_ms": 25.0,
                    "max_ms": 25.0,
                    "last_ms": 25.0,
                },
                "exchange_age_ms": {
                    "count": 1,
                    "total_ms": 5.0,
                    "average_ms": 5.0,
                    "min_ms": 5.0,
                    "max_ms": 5.0,
                    "last_ms": 5.0,
                },
                "queue_lag_ms": {},
            },
        }

    def get_active_trades(self):
        return list(self._active_trades)

    async def get_training_job_status(self):
        return self._training_snapshot


@pytest.mark.asyncio
async def test_status_route_includes_runtime_cutover_fields():
    job = TrainingJobRecord(
        job_id="job-status",
        status="running",
        requested_by="api",
        pairs=("BTC/USDT",),
        candles=900,
        timeframe="1h",
        progress=35.0,
    )
    bot = FakeStatusBot(
        make_projection(
            source=ProjectionSource.RUNTIME_PROJECTION,
            runtime_mode="runtime_primary",
            active_positions=(make_projected_position(),),
            open_order_count=2,
            pending_exit_count=1,
            rejected_order_count=1,
        ),
        configured_deployment_profile="paper_runtime_primary",
        training_snapshot=TrainingJobSnapshot(
            current_job=job,
            last_job=job,
            can_start_training=False,
            active_job_count=1,
        ),
    )

    payload = await get_status(bot=bot)

    assert payload["active_trades"] == 1
    assert payload["runtime_mode"] == "runtime_primary"
    assert payload["configured_deployment_profile"] == "paper_runtime_primary"
    assert payload["effective_runtime_mode"] == "runtime_primary"
    assert payload["effective_runtime_owns_entries"] is True
    assert payload["effective_runtime_owns_exits"] is True
    assert payload["runtime_is_running"] is True
    assert payload["runtime_owns_entries"] is True
    assert payload["runtime_owns_exits"] is True
    assert payload["runtime_telemetry"]["total_events"] == 2
    assert payload["runtime_telemetry"]["latency"]["event_age_ms"]["count"] == 1
    assert payload["runtime_is_healthy"] is True
    assert payload["state_source"] == "runtime_projection"
    assert payload["rollout_environment"] == "paper"
    assert payload["rollout_stage"] == "primary"
    assert payload["rollout_health"] == "promotion_ready"
    assert payload["rollout_recommended_action"] == "stay_runtime_primary"
    assert "Runtime primary ownership is healthy" in payload["rollout_summary"]
    assert payload["projected_active_positions"] == 1
    assert payload["training_job"]["current_job"]["job_id"] == "job-status"
    assert payload["training_job"]["current_job"]["status"] == "running"
    assert payload["projected_open_orders"] == 2
    assert payload["projected_pending_exits"] == 1
    assert payload["projected_rejected_orders"] == 1
    assert payload["strategy"] == "ensemble"
    assert payload["trading_mode"] == "paper"


@pytest.mark.asyncio
async def test_status_route_keeps_legacy_trade_count_in_shadow_mode():
    bot = FakeStatusBot(
        make_projection(
            source=ProjectionSource.SHADOW,
            runtime_mode="shadow",
            active_positions=(),
        ),
        configured_deployment_profile="paper_shadow",
    )

    payload = await get_status(bot=bot)

    assert payload["active_trades"] == 1
    assert payload["state_source"] == "shadow"
    assert payload["configured_deployment_profile"] == "paper_shadow"
    assert payload["effective_runtime_mode"] == "shadow"
    assert payload["runtime_is_healthy"] is True
    assert payload["runtime_telemetry"]["queue_depths"]["runtime.event_bus"] == 1
    assert payload["rollout_environment"] == "paper"
    assert payload["rollout_stage"] == "shadow"
    assert payload["rollout_health"] == "shadow_ready"
    assert payload["rollout_recommended_action"] == "continue_shadow"
    assert "validate before promotion" in payload["rollout_summary"]
    assert payload["projected_active_positions"] == 0
    assert payload["native_shadow_report"] is None


@pytest.mark.asyncio
async def test_status_route_surfaces_native_shadow_mismatch_report():
    bot = FakeStatusBot(
        make_projection(
            source=ProjectionSource.RUNTIME_PROJECTION,
            runtime_mode="runtime_primary",
            active_positions=(make_projected_position(),),
        ),
        configured_deployment_profile="paper_runtime_primary",
        native_shadow_report={
            "native_shadow_enabled": True,
            "native_shadow_status": "divergent",
            "native_shadow_is_match": False,
            "native_shadow_mismatch_count": 2,
            "native_shadow_mismatches": [
                "active_position_count",
                "open_order_count",
            ],
            "native_shadow_summary": "Native shadow diverges from Python runtime.",
        },
    )

    payload = await get_status(bot=bot)

    assert payload["native_shadow_report"]["native_shadow_status"] == "divergent"
    assert payload["native_shadow_report"]["native_shadow_is_match"] is False
    assert payload["native_shadow_report"]["native_shadow_mismatch_count"] == 2
    assert payload["native_shadow_report"]["native_shadow_mismatches"] == [
        "active_position_count",
        "open_order_count",
    ]


@pytest.mark.asyncio
async def test_status_route_marks_unhealthy_runtime_for_rollback():
    bot = FakeStatusBot(
        make_projection(
            source=ProjectionSource.RUNTIME_PROJECTION,
            runtime_mode="runtime_primary",
            active_positions=(make_projected_position(),),
        )
    )
    bot._projection = make_projection(
        source=ProjectionSource.RUNTIME_PROJECTION,
        runtime_mode="runtime_primary",
        active_positions=(make_projected_position(),),
    )
    object.__setattr__(bot._projection, "runtime_is_healthy", False)

    payload = await get_status(bot=bot)

    assert payload["runtime_is_healthy"] is False
    assert payload["rollout_environment"] == "paper"
    assert payload["rollout_health"] == "rollback_recommended"
    assert payload["rollout_recommended_action"] == "rollback_to_legacy"
    assert "rollback to legacy" in payload["rollout_summary"]


@pytest.mark.asyncio
async def test_status_route_marks_runtime_startup_pending():
    bot = FakeStatusBot(
        make_projection(
            source=ProjectionSource.SHADOW,
            runtime_mode="shadow",
        ),
        configured_deployment_profile="paper_shadow",
    )
    object.__setattr__(bot._projection, "runtime_is_running", False)
    object.__setattr__(bot._projection, "runtime_is_healthy", False)
    object.__setattr__(bot._projection, "runtime_start_status", "starting")
    object.__setattr__(bot._projection, "runtime_start_pending", True)

    payload = await get_status(bot=bot)

    assert payload["runtime_is_running"] is False
    assert payload["runtime_start_status"] == "starting"
    assert payload["runtime_start_pending"] is True
    assert payload["rollout_environment"] == "paper"
    assert payload["rollout_health"] == "startup_pending"
    assert payload["rollout_recommended_action"] == "wait_for_runtime"
    assert "startup is still in progress" in payload["rollout_summary"]


@pytest.mark.asyncio
async def test_status_route_distinguishes_live_canary_rollout():
    bot = FakeStatusBot(
        make_projection(
            source=ProjectionSource.SHADOW,
            runtime_mode="shadow",
        ),
        configured_deployment_profile="live_shadow",
        trading_mode="live",
    )

    payload = await get_status(bot=bot)

    assert payload["trading_mode"] == "live"
    assert payload["configured_deployment_profile"] == "live_shadow"
    assert payload["rollout_environment"] == "live"
    assert payload["rollout_stage"] == "canary"
    assert payload["rollout_health"] == "shadow_ready"
    assert payload["rollout_recommended_action"] == "promote_live_runtime_entries"
    assert "Live runtime shadow canary is healthy" in payload["rollout_summary"]


@pytest.mark.asyncio
async def test_status_route_uses_live_rollback_guidance():
    bot = FakeStatusBot(
        make_projection(
            source=ProjectionSource.RUNTIME_PROJECTION,
            runtime_mode="runtime_primary",
            active_positions=(make_projected_position(),),
        ),
        configured_deployment_profile="live_runtime_primary",
        trading_mode="live",
    )
    object.__setattr__(bot._projection, "runtime_is_healthy", False)

    payload = await get_status(bot=bot)

    assert payload["rollout_environment"] == "live"
    assert payload["rollout_stage"] == "primary"
    assert payload["rollout_health"] == "rollback_recommended"
    assert payload["rollout_recommended_action"] == "rollback_to_live_legacy"
    assert "rollback to live_legacy" in payload["rollout_summary"]


@pytest.mark.asyncio
async def test_trades_route_uses_runtime_projection_when_runtime_owns_state():
    bot = FakeStatusBot(
        make_projection(
            source=ProjectionSource.RUNTIME_PROJECTION,
            runtime_mode="runtime_primary",
            active_positions=(make_projected_position(),),
        )
    )

    payload = await get_active_trades(bot=bot)

    assert payload["count"] == 1
    assert payload["source"] == "runtime_projection"
    assert payload["runtime_mode"] == "runtime_primary"
    assert payload["trades"][0]["symbol"] == "BTC/USDT"
    assert payload["trades"][0]["amount"] == 0.25
    assert payload["trades"][0]["source"] == "runtime_projection"


@pytest.mark.asyncio
async def test_trades_route_keeps_legacy_state_when_runtime_is_shadow():
    bot = FakeStatusBot(
        make_projection(
            source=ProjectionSource.SHADOW,
            runtime_mode="shadow",
            active_positions=(make_projected_position(),),
        )
    )

    payload = await get_active_trades(bot=bot)

    assert payload["count"] == 1
    assert payload["source"] == "legacy"
    assert payload["runtime_mode"] == "shadow"
    assert payload["shadow_projected_count"] == 1
    assert payload["trades"] == [{"symbol": "BTC/USDT"}]
