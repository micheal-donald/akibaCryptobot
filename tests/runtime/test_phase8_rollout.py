from datetime import datetime, timezone

import pytest

from api.routes.status import get_status
from runtime.view_models import (
    ProjectedPositionView,
    ProjectionSource,
    RuntimeProjection,
)


NOW = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)


class FakeStatusBot:
    def __init__(self, projection: RuntimeProjection):
        self._projection = projection
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = ""
        self.config = {"exchange": {"sandbox": True}}
        self.strategy_manager = type(
            "StrategyManager", (), {"active_strategy": "ensemble"}
        )()

    def get_status_info(self):
        return {
            "active_trades": 3,
            "daily_pnl": 12.34,
            "uptime": "0:10:00",
            "is_running": True,
            "runtime_mode": self._projection.runtime_mode,
            "runtime_is_running": self._projection.runtime_is_running,
            "runtime_owns_entries": self._projection.runtime_owns_entries,
            "runtime_owns_exits": self._projection.runtime_owns_exits,
        }

    def get_runtime_projection(self) -> RuntimeProjection:
        return self._projection


def make_position(symbol: str = "BTC/USDT") -> ProjectedPositionView:
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


def make_projection(
    *,
    source: ProjectionSource,
    runtime_mode: str,
    runtime_is_healthy: bool = True,
) -> RuntimeProjection:
    owns_entries = runtime_mode in {"runtime_entries", "runtime_primary"}
    owns_exits = runtime_mode == "runtime_primary"
    return RuntimeProjection(
        source=source,
        runtime_mode=runtime_mode,
        runtime_enabled=runtime_mode != "legacy",
        runtime_is_running=runtime_mode != "legacy",
        runtime_is_healthy=runtime_is_healthy,
        runtime_owns_entries=owns_entries,
        runtime_owns_exits=owns_exits,
        active_position_count=1,
        protected_position_count=0,
        pending_exit_count=1,
        open_order_count=2,
        rejected_order_count=0,
        positions=(make_position(),),
        orders=(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("projection", "expected_health", "expected_action", "expected_source"),
    [
        (
            make_projection(
                source=ProjectionSource.LEGACY,
                runtime_mode="legacy",
            ),
            "legacy",
            "stay_legacy",
            "legacy",
        ),
        (
            make_projection(
                source=ProjectionSource.SHADOW,
                runtime_mode="shadow",
            ),
            "shadow_ready",
            "continue_shadow",
            "shadow",
        ),
        (
            make_projection(
                source=ProjectionSource.RUNTIME_PROJECTION,
                runtime_mode="runtime_entries",
            ),
            "promotion_ready",
            "promote_runtime_primary",
            "runtime_projection",
        ),
        (
            make_projection(
                source=ProjectionSource.RUNTIME_PROJECTION,
                runtime_mode="runtime_primary",
            ),
            "promotion_ready",
            "stay_runtime_primary",
            "runtime_projection",
        ),
        (
            make_projection(
                source=ProjectionSource.RUNTIME_PROJECTION,
                runtime_mode="runtime_primary",
                runtime_is_healthy=False,
            ),
            "rollback_recommended",
            "rollback_to_legacy",
            "runtime_projection",
        ),
    ],
)
async def test_phase8_activation_status_checklist(
    projection, expected_health, expected_action, expected_source
):
    bot = FakeStatusBot(projection)

    payload = await get_status(bot=bot)

    assert payload["runtime_mode"] == projection.runtime_mode
    assert payload["state_source"] == expected_source
    assert payload["runtime_is_healthy"] is projection.runtime_is_healthy
    assert payload["rollout_health"] == expected_health
    assert payload["rollout_recommended_action"] == expected_action
    assert payload["rollout_summary"]
    assert payload["trading_mode"] == "paper"

    if projection.runtime_mode == "runtime_entries":
        assert payload["runtime_owns_entries"] is True
        assert payload["runtime_owns_exits"] is False
        assert payload["rollout_summary"].startswith(
            "Runtime entry ownership is healthy"
        )

    if projection.runtime_mode == "runtime_primary":
        assert payload["runtime_owns_entries"] is True
        assert payload["runtime_owns_exits"] is True

    if not projection.runtime_is_healthy:
        assert "rollback to legacy" in payload["rollout_summary"]
