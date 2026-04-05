from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from start_bot import log_runtime_visibility
from runtime.config import RuntimeConfig


def test_log_runtime_visibility_reports_live_primary_stage() -> None:
    logger = MagicMock()
    runtime_config = RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        deployment_profile="live_runtime_primary",
        allow_live_runtime=True,
    )
    bot = SimpleNamespace(
        runtime_config=runtime_config,
        get_runtime_visibility_info=lambda: {
            "configured_deployment_profile": "live_runtime_primary",
            "effective_runtime_mode": "runtime_primary",
            "effective_runtime_enabled": True,
            "effective_runtime_owns_entries": True,
            "effective_runtime_owns_exits": True,
            "runtime_mode_source": "deployment_profile",
        },
    )

    log_runtime_visibility(logger, bot)

    logger.info.assert_any_call(
        "Live runtime rollout profile=%s stage=%s allow_live_runtime=%s",
        "live_runtime_primary",
        "primary",
        True,
    )
