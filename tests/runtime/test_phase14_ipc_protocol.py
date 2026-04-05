from __future__ import annotations

from dataclasses import dataclass

import pytest

from runtime.ipc_protocol import (
    IPCFailureCode,
    IPCEnvelope,
    IPCPayloadKind,
    IPCProtocolError,
    IPCVersionMismatchError,
    build_failure_envelope,
    decode_ipc_envelope,
    encode_ipc_envelope,
)


@dataclass(slots=True)
class _Payload:
    symbol: str
    quantity: float


def test_ipc_envelope_round_trips_and_serializes_payloads() -> None:
    envelope = IPCEnvelope.create(
        IPCPayloadKind.MARKET_DATA,
        _Payload(symbol="BTCUSDT", quantity=1.25),
        correlation_id="corr-1",
    )

    encoded = encode_ipc_envelope(envelope)
    decoded = decode_ipc_envelope(encoded)

    assert decoded.version == 1
    assert decoded.kind == IPCPayloadKind.MARKET_DATA
    assert decoded.correlation_id == "corr-1"
    assert decoded.payload == {"symbol": "BTCUSDT", "quantity": 1.25}


def test_ipc_envelope_rejects_version_mismatch() -> None:
    envelope = IPCEnvelope.create(IPCPayloadKind.EXECUTION, {"ok": True})
    payload = envelope.as_dict()
    payload["version"] = 2

    with pytest.raises(IPCVersionMismatchError):
        IPCEnvelope.from_mapping(payload)


def test_ipc_envelope_rejects_invalid_payload_shape() -> None:
    with pytest.raises(IPCProtocolError):
        IPCEnvelope.from_mapping(
            {
                "version": 1,
                "message_id": "msg-1",
                "kind": "unknown",
                "created_at": "2026-04-05T00:00:00Z",
                "payload": {},
            }
        )


def test_failure_envelopes_are_explicit_and_versioned() -> None:
    envelope = build_failure_envelope(
        IPCFailureCode.INVALID_PAYLOAD,
        "payload missing required field",
        correlation_id="corr-2",
    )

    assert envelope.kind == IPCPayloadKind.ERROR
    assert envelope.failure_code == IPCFailureCode.INVALID_PAYLOAD
    assert envelope.failure_message == "payload missing required field"
    assert envelope.correlation_id == "corr-2"
    assert decode_ipc_envelope(encode_ipc_envelope(envelope)).failure_code == (
        IPCFailureCode.INVALID_PAYLOAD
    )
