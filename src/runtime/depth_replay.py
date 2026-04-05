"""Replay helpers for bounded order-book depth capture JSONL files."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json

from runtime.depth_capture import CapturedDepthEvent
from services.order_book_service import (
    OrderBookLevel,
    OrderBookService,
    OrderBookSnapshot,
)


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


def _parse_level(level: object, *, field_name: str) -> OrderBookLevel:
    mapping = _require_mapping(level, field_name=field_name)
    return OrderBookLevel(
        price=_require_float(mapping["price"], field_name=f"{field_name}.price"),
        quantity=_require_float(
            mapping["quantity"], field_name=f"{field_name}.quantity"
        ),
    )


def _parse_levels(levels: object, *, field_name: str) -> tuple[OrderBookLevel, ...]:
    if not isinstance(levels, Iterable):
        raise TypeError(f"{field_name} must be iterable, got {type(levels)!r}")
    return tuple(
        _parse_level(level, field_name=f"{field_name}[{index}]")
        for index, level in enumerate(levels)
    )


def _parse_captured_record(line: str) -> CapturedDepthEvent:
    data = json.loads(line)
    if not isinstance(data, Mapping):
        raise TypeError("captured depth line must decode to a mapping")

    payload_type = _require_str(data["payload_type"], field_name="payload_type")
    payload = data.get("payload")
    return CapturedDepthEvent(
        capture_index=int(data["capture_index"]),
        event_id=_require_str(data["event_id"], field_name="event_id"),
        event_type=_require_str(data["event_type"], field_name="event_type"),
        source=_require_str(data["source"], field_name="source"),
        symbol=(
            None
            if data.get("symbol") is None
            else _require_str(data["symbol"], field_name="symbol")
        ),
        sequence=(None if data.get("sequence") is None else int(data["sequence"])),
        ts_exchange=(
            None
            if data.get("ts_exchange") is None
            else _require_str(data["ts_exchange"], field_name="ts_exchange")
        ),
        ts_received=_require_str(data["ts_received"], field_name="ts_received"),
        captured_at=_require_str(data["captured_at"], field_name="captured_at"),
        payload_type=payload_type,
        payload=payload,
    )


def _read_jsonl_lines(source: str | Path | Iterable[str]) -> Iterator[str]:
    if isinstance(source, (str, Path)):
        with Path(source).open("r", encoding="utf-8") as handle:
            yield from handle
        return
    yield from source


@dataclass(slots=True, frozen=True)
class DepthReplayStep:
    """A single replay step for a captured depth record."""

    record: CapturedDepthEvent
    snapshot: OrderBookSnapshot | None
    sequence_issue: str | None


@dataclass(slots=True, frozen=True)
class RuntimeDepthReplayHarness:
    """Deterministic replay wrapper for exported depth capture JSONL."""

    records: tuple[CapturedDepthEvent, ...]

    @classmethod
    def from_jsonl(
        cls, source: str | Path | Iterable[str]
    ) -> "RuntimeDepthReplayHarness":
        records = tuple(
            _parse_captured_record(line)
            for line in _read_jsonl_lines(source)
            if line.strip()
        )
        return cls(records=records)

    def iter_records(self) -> Iterator[CapturedDepthEvent]:
        return iter(self.records)

    def iter_steps(
        self, order_book_service: OrderBookService | None = None
    ) -> Iterator[DepthReplayStep]:
        service = order_book_service or OrderBookService(name="replay-order-book")
        last_sequences: dict[str, int] = {}
        for record in self.records:
            yield self._apply_record(service, record, last_sequences)

    def replay_into(
        self, order_book_service: OrderBookService
    ) -> tuple[DepthReplayStep, ...]:
        return tuple(self.iter_steps(order_book_service))

    def iter_snapshots(
        self, order_book_service: OrderBookService | None = None
    ) -> Iterator[OrderBookSnapshot]:
        for step in self.iter_steps(order_book_service):
            if step.snapshot is not None:
                yield step.snapshot

    @staticmethod
    def _apply_record(
        order_book_service: OrderBookService,
        record: CapturedDepthEvent,
        last_sequences: dict[str, int],
    ) -> DepthReplayStep:
        payload = record.payload
        sequence_issue = None
        if record.sequence is not None and record.symbol is not None:
            previous_sequence = last_sequences.get(record.symbol)
            if previous_sequence is not None:
                sequence_issue = RuntimeDepthReplayHarness._sequence_issue(
                    previous_sequence,
                    record.sequence,
                )
            last_sequences[record.symbol] = record.sequence

        if not isinstance(payload, Mapping):
            return DepthReplayStep(record=record, snapshot=None, sequence_issue=None)

        if record.payload_type == "OrderBookSnapshot":
            snapshot = RuntimeDepthReplayHarness._decode_snapshot(payload)
            order_book_service.replace_snapshot(
                snapshot.symbol,
                bids=snapshot.bids,
                asks=snapshot.asks,
                sequence=snapshot.sequence,
                exchange_timestamp=snapshot.exchange_timestamp,
                received_at=snapshot.received_at,
            )
            return DepthReplayStep(
                record=record,
                snapshot=order_book_service.get_snapshot(snapshot.symbol, depth=None),
                sequence_issue=sequence_issue,
            )

        return DepthReplayStep(
            record=record, snapshot=None, sequence_issue=sequence_issue
        )

    @staticmethod
    def _decode_snapshot(payload: Mapping[str, object]) -> OrderBookSnapshot:
        symbol = _require_str(payload["symbol"], field_name="symbol")
        bids = _parse_levels(payload.get("bids", ()), field_name="bids")
        asks = _parse_levels(payload.get("asks", ()), field_name="asks")
        return OrderBookSnapshot(
            symbol=symbol,
            bids=bids,
            asks=asks,
            sequence=(
                None if payload.get("sequence") is None else int(payload["sequence"])
            ),
            exchange_timestamp=_parse_datetime(payload.get("exchange_timestamp")),
            received_at=_parse_datetime(payload.get("received_at")),
            updated_at=_require_datetime(
                payload["updated_at"], field_name="updated_at"
            ),
            is_stale=bool(payload.get("is_stale", False)),
            is_gap_detected=bool(payload.get("is_gap_detected", False)),
            is_sequence_valid=bool(payload.get("is_sequence_valid", True)),
            last_sequence_delta=(
                None
                if payload.get("last_sequence_delta") is None
                else int(payload["last_sequence_delta"])
            ),
            sequence_issue=(
                None
                if payload.get("sequence_issue") is None
                else _require_str(
                    payload["sequence_issue"], field_name="sequence_issue"
                )
            ),
        )

    @staticmethod
    def _sequence_issue(
        previous_sequence: int | None, current_sequence: int
    ) -> str | None:
        if previous_sequence is None:
            return None
        delta = current_sequence - previous_sequence
        if delta == 1:
            return None
        if delta > 1:
            return "gap"
        if delta == 0:
            return "duplicate"
        return "non_monotonic"


def load_depth_replay(
    source: str | Path | Iterable[str],
) -> RuntimeDepthReplayHarness:
    return RuntimeDepthReplayHarness.from_jsonl(source)


def replay_depth_events(
    source: str | Path | Iterable[str],
    order_book_service: OrderBookService,
) -> tuple[DepthReplayStep, ...]:
    return load_depth_replay(source).replay_into(order_book_service)
