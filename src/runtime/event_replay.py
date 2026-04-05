"""Replay helpers for bounded runtime event-capture JSONL files."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json

from domain.events import (
    BalanceUpdatePayload,
    BookTickerPayload,
    EventMeta,
    FillPayload,
    HousekeepingPayload,
    KlinePayload,
    OrderIntentPayload,
    OrderRejectedPayload,
    OrderSubmittedPayload,
    OrderUpdatePayload,
    PositionClosedPayload,
    PositionOpenedPayload,
    ProtectionSubmittedPayload,
    RiskBlockedPayload,
    RuntimeEvent,
)

from .event_capture import CapturedRuntimeEvent


@dataclass(slots=True, frozen=True)
class SignalSuppressedPayload:
    """Replay payload for suppressed runtime signal events."""

    symbol: str
    timeframe: str
    reason: str
    candle_close_time: datetime
    strategy_name: str | None
    decision_lag_ms: float | None


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"expected ISO 8601 datetime string, got {type(value)!r}")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    return datetime.fromisoformat(normalized)


def _require_datetime(value: object, *, field_name: str) -> datetime:
    parsed = _parse_datetime(value)
    if parsed is None:
        raise ValueError(f"{field_name} is required")
    return parsed


def _require_mapping(value: object, *, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping, got {type(value)!r}")
    return value


def _require_str(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value)!r}")
    return value


def _require_float(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number, got {type(value)!r}")
    return float(value)


def _decode_book_ticker(payload: Mapping[str, object]) -> BookTickerPayload:
    return BookTickerPayload(
        bid_price=_require_float(payload["bid_price"], field_name="bid_price"),
        bid_qty=_require_float(payload["bid_qty"], field_name="bid_qty"),
        ask_price=_require_float(payload["ask_price"], field_name="ask_price"),
        ask_qty=_require_float(payload["ask_qty"], field_name="ask_qty"),
    )


def _decode_kline(payload: Mapping[str, object]) -> KlinePayload:
    return KlinePayload(
        interval=_require_str(payload["interval"], field_name="interval"),
        open_time=_require_datetime(payload["open_time"], field_name="open_time"),
        close_time=_require_datetime(payload["close_time"], field_name="close_time"),
        open=_require_float(payload["open"], field_name="open"),
        high=_require_float(payload["high"], field_name="high"),
        low=_require_float(payload["low"], field_name="low"),
        close=_require_float(payload["close"], field_name="close"),
        volume=_require_float(payload["volume"], field_name="volume"),
        is_closed=bool(payload["is_closed"]),
    )


def _decode_housekeeping(payload: Mapping[str, object]) -> HousekeepingPayload:
    return HousekeepingPayload(
        reason=_require_str(payload["reason"], field_name="reason"),
    )


def _decode_order_intent(payload: Mapping[str, object]) -> OrderIntentPayload:
    return OrderIntentPayload(
        intent_id=_require_str(payload["intent_id"], field_name="intent_id"),
        symbol=_require_str(payload["symbol"], field_name="symbol"),
        side=_require_str(payload["side"], field_name="side"),
        order_type=_require_str(payload["order_type"], field_name="order_type"),
        quantity=_require_float(payload["quantity"], field_name="quantity"),
        limit_price=(
            _require_float(payload["limit_price"], field_name="limit_price")
            if payload.get("limit_price") is not None
            else None
        ),
        stop_loss=(
            _require_float(payload["stop_loss"], field_name="stop_loss")
            if payload.get("stop_loss") is not None
            else None
        ),
        take_profit=(
            _require_float(payload["take_profit"], field_name="take_profit")
            if payload.get("take_profit") is not None
            else None
        ),
        reduce_only=bool(payload.get("reduce_only", False)),
        strategy=(
            payload.get("strategy")
            if payload.get("strategy") is None
            else _require_str(payload["strategy"], field_name="strategy")
        ),
        reason=(
            payload.get("reason")
            if payload.get("reason") is None
            else _require_str(payload["reason"], field_name="reason")
        ),
    )


def _decode_order_submitted(payload: Mapping[str, object]) -> OrderSubmittedPayload:
    return OrderSubmittedPayload(
        client_order_id=_require_str(
            payload["client_order_id"], field_name="client_order_id"
        ),
        exchange_order_id=(
            payload.get("exchange_order_id")
            if payload.get("exchange_order_id") is None
            else _require_str(
                payload["exchange_order_id"], field_name="exchange_order_id"
            )
        ),
        symbol=_require_str(payload["symbol"], field_name="symbol"),
        side=_require_str(payload["side"], field_name="side"),
        order_type=_require_str(payload["order_type"], field_name="order_type"),
        requested_qty=_require_float(
            payload["requested_qty"], field_name="requested_qty"
        ),
        requested_price=(
            None
            if payload.get("requested_price") is None
            else _require_float(
                payload["requested_price"], field_name="requested_price"
            )
        ),
        reduce_only=bool(payload.get("reduce_only", False)),
    )


def _decode_order_update(payload: Mapping[str, object]) -> OrderUpdatePayload:
    return OrderUpdatePayload(
        client_order_id=(
            None
            if payload.get("client_order_id") is None
            else _require_str(payload["client_order_id"], field_name="client_order_id")
        ),
        exchange_order_id=(
            None
            if payload.get("exchange_order_id") is None
            else _require_str(
                payload["exchange_order_id"], field_name="exchange_order_id"
            )
        ),
        symbol=_require_str(payload["symbol"], field_name="symbol"),
        status=_require_str(payload["status"], field_name="status"),
        filled_qty=_require_float(payload["filled_qty"], field_name="filled_qty"),
        remaining_qty=_require_float(
            payload["remaining_qty"], field_name="remaining_qty"
        ),
        avg_fill_price=(
            None
            if payload.get("avg_fill_price") is None
            else _require_float(payload["avg_fill_price"], field_name="avg_fill_price")
        ),
        reduce_only=bool(payload.get("reduce_only", False)),
        raw_status=(
            None
            if payload.get("raw_status") is None
            else _require_str(payload["raw_status"], field_name="raw_status")
        ),
    )


def _decode_fill(payload: Mapping[str, object]) -> FillPayload:
    return FillPayload(
        client_order_id=(
            None
            if payload.get("client_order_id") is None
            else _require_str(payload["client_order_id"], field_name="client_order_id")
        ),
        exchange_order_id=(
            None
            if payload.get("exchange_order_id") is None
            else _require_str(
                payload["exchange_order_id"], field_name="exchange_order_id"
            )
        ),
        symbol=_require_str(payload["symbol"], field_name="symbol"),
        side=_require_str(payload["side"], field_name="side"),
        fill_qty=_require_float(payload["fill_qty"], field_name="fill_qty"),
        fill_price=_require_float(payload["fill_price"], field_name="fill_price"),
        cumulative_qty=_require_float(
            payload["cumulative_qty"], field_name="cumulative_qty"
        ),
        cumulative_quote_qty=(
            None
            if payload.get("cumulative_quote_qty") is None
            else _require_float(
                payload["cumulative_quote_qty"], field_name="cumulative_quote_qty"
            )
        ),
        fee_cost=(
            None
            if payload.get("fee_cost") is None
            else _require_float(payload["fee_cost"], field_name="fee_cost")
        ),
        fee_currency=(
            None
            if payload.get("fee_currency") is None
            else _require_str(payload["fee_currency"], field_name="fee_currency")
        ),
        is_maker=(
            None if payload.get("is_maker") is None else bool(payload["is_maker"])
        ),
        reduce_only=bool(payload.get("reduce_only", False)),
    )


def _decode_balance_update(payload: Mapping[str, object]) -> BalanceUpdatePayload:
    balances = payload.get("balances")
    if not isinstance(balances, Mapping):
        raise TypeError("balances must be a mapping")
    normalized: dict[str, dict[str, float]] = {}
    for asset, values in balances.items():
        asset_name = _require_str(asset, field_name="balances key")
        asset_values = _require_mapping(values, field_name=f"balances[{asset_name}]")
        normalized[asset_name] = {
            str(key): _require_float(value, field_name=f"balances[{asset_name}].{key}")
            for key, value in asset_values.items()
        }
    return BalanceUpdatePayload(balances=normalized)


def _decode_position_opened(payload: Mapping[str, object]) -> PositionOpenedPayload:
    entry_order_ids = payload.get("entry_order_ids", ())
    if not isinstance(entry_order_ids, Iterable):
        raise TypeError("entry_order_ids must be iterable")
    return PositionOpenedPayload(
        position_id=_require_str(payload["position_id"], field_name="position_id"),
        symbol=_require_str(payload["symbol"], field_name="symbol"),
        side=_require_str(payload["side"], field_name="side"),
        quantity=_require_float(payload["quantity"], field_name="quantity"),
        avg_entry_price=_require_float(
            payload["avg_entry_price"], field_name="avg_entry_price"
        ),
        entry_order_ids=tuple(
            _require_str(order_id, field_name="entry_order_ids item")
            for order_id in entry_order_ids
        ),
    )


def _decode_position_closed(payload: Mapping[str, object]) -> PositionClosedPayload:
    return PositionClosedPayload(
        position_id=_require_str(payload["position_id"], field_name="position_id"),
        symbol=_require_str(payload["symbol"], field_name="symbol"),
        side=_require_str(payload["side"], field_name="side"),
        quantity=_require_float(payload["quantity"], field_name="quantity"),
        avg_entry_price=(
            None
            if payload.get("avg_entry_price") is None
            else _require_float(
                payload["avg_entry_price"], field_name="avg_entry_price"
            )
        ),
        avg_exit_price=(
            None
            if payload.get("avg_exit_price") is None
            else _require_float(payload["avg_exit_price"], field_name="avg_exit_price")
        ),
        close_reason=_require_str(payload["close_reason"], field_name="close_reason"),
        pnl=(
            None
            if payload.get("pnl") is None
            else _require_float(payload["pnl"], field_name="pnl")
        ),
        fee_cost=(
            None
            if payload.get("fee_cost") is None
            else _require_float(payload["fee_cost"], field_name="fee_cost")
        ),
    )


def _decode_risk_blocked(payload: Mapping[str, object]) -> RiskBlockedPayload:
    return RiskBlockedPayload(
        intent_id=_require_str(payload["intent_id"], field_name="intent_id"),
        symbol=_require_str(payload["symbol"], field_name="symbol"),
        reason=_require_str(payload["reason"], field_name="reason"),
    )


def _decode_order_rejected(payload: Mapping[str, object]) -> OrderRejectedPayload:
    return OrderRejectedPayload(
        intent_id=_require_str(payload["intent_id"], field_name="intent_id"),
        symbol=_require_str(payload["symbol"], field_name="symbol"),
        reason=_require_str(payload["reason"], field_name="reason"),
    )


def _decode_protection_submitted(
    payload: Mapping[str, object],
) -> ProtectionSubmittedPayload:
    submitted_order_ids = payload.get("submitted_order_ids", ())
    if not isinstance(submitted_order_ids, Iterable):
        raise TypeError("submitted_order_ids must be iterable")
    return ProtectionSubmittedPayload(
        client_order_id=_require_str(
            payload["client_order_id"], field_name="client_order_id"
        ),
        symbol=_require_str(payload["symbol"], field_name="symbol"),
        status=_require_str(payload["status"], field_name="status"),
        mode=_require_str(payload["mode"], field_name="mode"),
        requested_qty=_require_float(
            payload["requested_qty"], field_name="requested_qty"
        ),
        submitted_order_ids=tuple(
            _require_str(order_id, field_name="submitted_order_ids item")
            for order_id in submitted_order_ids
        ),
        reason=(
            None
            if payload.get("reason") is None
            else _require_str(payload["reason"], field_name="reason")
        ),
        stop_loss=(
            None
            if payload.get("stop_loss") is None
            else _require_float(payload["stop_loss"], field_name="stop_loss")
        ),
        take_profit=(
            None
            if payload.get("take_profit") is None
            else _require_float(payload["take_profit"], field_name="take_profit")
        ),
        trailing_stop=(
            None
            if payload.get("trailing_stop") is None
            else _require_float(payload["trailing_stop"], field_name="trailing_stop")
        ),
        attempted_at=_parse_datetime(payload.get("attempted_at")),
        updated_at=_parse_datetime(payload.get("updated_at")),
    )


def _decode_signal_suppressed(
    payload: Mapping[str, object],
) -> SignalSuppressedPayload:
    return SignalSuppressedPayload(
        symbol=_require_str(payload["symbol"], field_name="symbol"),
        timeframe=_require_str(payload["timeframe"], field_name="timeframe"),
        reason=_require_str(payload["reason"], field_name="reason"),
        candle_close_time=_require_datetime(
            payload["candle_close_time"], field_name="candle_close_time"
        ),
        strategy_name=(
            None
            if payload.get("strategy_name") is None
            else _require_str(payload["strategy_name"], field_name="strategy_name")
        ),
        decision_lag_ms=(
            None
            if payload.get("decision_lag_ms") is None
            else _require_float(
                payload["decision_lag_ms"], field_name="decision_lag_ms"
            )
        ),
    )


_PAYLOAD_DECODERS: dict[str, Callable[[Mapping[str, object]], object]] = {
    "BookTickerPayload": _decode_book_ticker,
    "KlinePayload": _decode_kline,
    "HousekeepingPayload": _decode_housekeeping,
    "OrderIntentPayload": _decode_order_intent,
    "OrderSubmittedPayload": _decode_order_submitted,
    "OrderUpdatePayload": _decode_order_update,
    "FillPayload": _decode_fill,
    "BalanceUpdatePayload": _decode_balance_update,
    "PositionOpenedPayload": _decode_position_opened,
    "PositionClosedPayload": _decode_position_closed,
    "RiskBlockedPayload": _decode_risk_blocked,
    "OrderRejectedPayload": _decode_order_rejected,
    "ProtectionSubmittedPayload": _decode_protection_submitted,
    "SignalSuppressedPayload": _decode_signal_suppressed,
}


def _decode_payload(
    payload_type: str,
    payload: object,
) -> object:
    if payload_type not in _PAYLOAD_DECODERS:
        return payload
    mapping = _require_mapping(payload, field_name="payload")
    return _PAYLOAD_DECODERS[payload_type](mapping)


def _parse_captured_record(line: str) -> CapturedRuntimeEvent:
    data = json.loads(line)
    if not isinstance(data, Mapping):
        raise TypeError("captured event line must decode to a mapping")

    payload_type = _require_str(data["payload_type"], field_name="payload_type")
    return CapturedRuntimeEvent(
        capture_index=int(data["capture_index"]),
        event_id=_require_str(data["event_id"], field_name="event_id"),
        event_type=_require_str(data["event_type"], field_name="event_type"),
        source=_require_str(data["source"], field_name="source"),
        symbol=(
            None
            if data.get("symbol") is None
            else _require_str(data["symbol"], field_name="symbol")
        ),
        ts_exchange=(
            None
            if data.get("ts_exchange") is None
            else _require_str(data["ts_exchange"], field_name="ts_exchange")
        ),
        ts_received=_require_str(data["ts_received"], field_name="ts_received"),
        captured_at=_require_str(data["captured_at"], field_name="captured_at"),
        payload_type=payload_type,
        payload=_decode_payload(payload_type, data.get("payload")),
    )


def _read_jsonl_lines(source: str | Path | Iterable[str]) -> Iterator[str]:
    if isinstance(source, (str, Path)):
        with Path(source).open("r", encoding="utf-8") as handle:
            yield from handle
        return
    yield from source


@dataclass(slots=True, frozen=True)
class RuntimeEventReplayHarness:
    """Deterministic replay wrapper for exported runtime capture JSONL."""

    records: tuple[CapturedRuntimeEvent, ...]

    @classmethod
    def from_jsonl(
        cls, source: str | Path | Iterable[str]
    ) -> "RuntimeEventReplayHarness":
        records = tuple(
            _parse_captured_record(line)
            for line in _read_jsonl_lines(source)
            if line.strip()
        )
        return cls(records=records)

    def iter_records(self) -> Iterator[CapturedRuntimeEvent]:
        return iter(self.records)

    def iter_events(self) -> Iterator[RuntimeEvent]:
        for record in self.records:
            yield self._build_runtime_event(record)

    async def replay_into(self, event_bus: object) -> None:
        put = getattr(event_bus, "put", None)
        if put is None or not callable(put):
            raise TypeError("event_bus must provide an async put method")
        for event in self.iter_events():
            result = put(event)
            if hasattr(result, "__await__"):
                await result
            else:
                raise TypeError("event_bus.put must be awaitable")

    @staticmethod
    def _build_runtime_event(record: CapturedRuntimeEvent) -> RuntimeEvent:
        return RuntimeEvent(
            meta=EventMeta(
                event_id=record.event_id,
                event_type=record.event_type,  # type: ignore[arg-type]
                source=record.source,
                symbol=record.symbol,
                ts_exchange=(
                    _parse_datetime(record.ts_exchange)
                    if record.ts_exchange is not None
                    else None
                ),
                ts_received=_require_datetime(
                    record.ts_received, field_name="ts_received"
                ),
            ),
            payload=record.payload,
        )


def load_runtime_event_replay(
    source: str | Path | Iterable[str],
) -> RuntimeEventReplayHarness:
    """Load a replay harness from a JSONL capture source."""

    return RuntimeEventReplayHarness.from_jsonl(source)


def replay_runtime_events(
    source: str | Path | Iterable[str],
) -> tuple[RuntimeEvent, ...]:
    """Load and normalize replay events from a JSONL capture source."""

    return tuple(load_runtime_event_replay(source).iter_events())
