from __future__ import annotations

import pytest

from runtime.config import RuntimeConfig
from runtime.mode import RuntimeMode


def test_runtime_config_from_mapping_supports_phase_two_fields() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "runtime": {
                "symbols": ["BTC/USDT", "ETH/USDT"],
                "kline_intervals": ["1m", "15m"],
                "candle_history_limit": 512,
                "queue_maxsize": 64,
                "stale_after_seconds": 45,
                "market_data_source": "binance",
                "sandbox_execution": True,
                "sandbox_market_data": False,
                "shadow_mode": False,
            }
        }
    )

    assert config.symbols == ("BTC/USDT", "ETH/USDT")
    assert config.kline_intervals == ("1m", "15m")
    assert config.candle_history_limit == 512
    assert config.queue_maxsize == 64
    assert config.event_bus_maxsize == 64
    assert config.stale_after_seconds == 45.0
    assert config.market_data_source == "binance"
    assert config.sandbox_execution is True
    assert config.sandbox_market_data is False
    assert config.shadow_mode is False
    assert config.runtime_mode is RuntimeMode.RUNTIME_ENTRIES
    assert config.runtime_owns_entries is True
    assert config.runtime_owns_exits is False


def test_runtime_config_from_mapping_supports_legacy_nested_config() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "exchange": {"name": "binance", "sandbox": True},
            "trading": {
                "pairs": ["BTC/USDT", "ETH/USDT"],
                "timeframes": {"primary": "1h", "secondary": "15m"},
            },
            "runtime": {"event_bus_maxsize": 16},
        }
    )

    assert config.symbols == ("BTC/USDT", "ETH/USDT")
    assert config.kline_intervals == ("1h", "15m")
    assert config.queue_maxsize == 16
    assert config.event_bus_maxsize == 16
    assert config.market_data_source == "binance"
    assert config.sandbox_execution is True
    assert config.sandbox_market_data is False


def test_runtime_config_from_mapping_supports_explicit_runtime_mode() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "runtime": {
                "symbols": ["BTC/USDT"],
                "kline_intervals": ["1m"],
                "runtime_mode": "runtime_primary",
                "shadow_mode": True,
            }
        }
    )

    assert config.runtime_mode is RuntimeMode.RUNTIME_PRIMARY
    assert config.shadow_mode is False
    assert config.runtime_owns_entries is True
    assert config.runtime_owns_exits is True


def test_runtime_config_mirrors_queue_size_aliases() -> None:
    queue_config = RuntimeConfig(
        symbols=("BTC/USDT",), kline_intervals=("1m",), queue_maxsize=8
    )
    bus_config = RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        event_bus_maxsize=12,
    )

    assert queue_config.queue_maxsize == 8
    assert queue_config.event_bus_maxsize == 8
    assert bus_config.queue_maxsize == 12
    assert bus_config.event_bus_maxsize == 12


def test_runtime_config_rejects_mismatched_queue_sizes() -> None:
    with pytest.raises(ValueError, match="must match"):
        RuntimeConfig(
            symbols=("BTC/USDT",),
            kline_intervals=("1m",),
            queue_maxsize=8,
            event_bus_maxsize=16,
        )


def test_runtime_config_defaults_to_one_minute_interval_when_unspecified() -> None:
    config = RuntimeConfig.from_mapping({})

    assert config.kline_intervals == ("1m",)
