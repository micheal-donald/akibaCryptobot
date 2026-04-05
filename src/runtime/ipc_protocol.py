"""Versioned IPC message envelopes for the native hot-path boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from enum import Enum
import json
from pathlib import Path
from uuid import uuid4

IPC_PROTOCOL_VERSION = 1


class IPCProtocolError(ValueError):
    """Raised when an IPC envelope cannot be decoded safely."""


class IPCVersionMismatchError(IPCProtocolError):
    """Raised when the payload version does not match the supported version."""


class IPCPayloadKind(str, Enum):
    """Message kind for the native boundary."""

    MARKET_DATA = "market_data"
    EXECUTION = "execution"
    HEARTBEAT = "heartbeat"
    ERROR = "error"


class IPCFailureCode(str, Enum):
    """Normalized failure codes returned through the IPC boundary."""

    VERSION_MISMATCH = "version_mismatch"
    INVALID_ENVELOPE = "invalid_envelope"
    INVALID_PAYLOAD = "invalid_payload"
    UNSUPPORTED_KIND = "unsupported_kind"


@dataclass(slots=True, frozen=True)
class IPCEnvelope:
    """Versioned JSON-friendly message envelope for native hot-path calls."""

    version: int
    message_id: str
    kind: IPCPayloadKind
    created_at: str
    payload: object
    correlation_id: str | None = None
    failure_code: IPCFailureCode | None = None
    failure_message: str | None = None

    @classmethod
    def create(
        cls,
        kind: IPCPayloadKind,
        payload: object,
        *,
        correlation_id: str | None = None,
        created_at: datetime | None = None,
    ) -> "IPCEnvelope":
        return cls(
            version=IPC_PROTOCOL_VERSION,
            message_id=uuid4().hex,
            kind=kind,
            created_at=(created_at or datetime.utcnow()).isoformat(),
            payload=payload,
            correlation_id=correlation_id,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "message_id": self.message_id,
            "kind": self.kind.value,
            "created_at": self.created_at,
            "payload": _serialize_value(self.payload),
            "correlation_id": self.correlation_id,
            "failure_code": (
                self.failure_code.value if self.failure_code is not None else None
            ),
            "failure_message": self.failure_message,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str | bytes) -> "IPCEnvelope":
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, Mapping):
            raise IPCProtocolError("IPC envelope must decode to a mapping")
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "IPCEnvelope":
        version = _require_int(data.get("version"), field_name="version")
        if version != IPC_PROTOCOL_VERSION:
            raise IPCVersionMismatchError(
                f"unsupported IPC protocol version: {version}"
            )
        kind_value = _require_str(data.get("kind"), field_name="kind")
        try:
            kind = IPCPayloadKind(kind_value)
        except ValueError as exc:
            raise IPCProtocolError(f"unsupported IPC kind: {kind_value!r}") from exc

        failure_code_value = data.get("failure_code")
        failure_code = None
        if failure_code_value is not None:
            try:
                failure_code = IPCFailureCode(
                    _require_str(failure_code_value, field_name="failure_code")
                )
            except ValueError as exc:
                raise IPCProtocolError(
                    f"unsupported IPC failure code: {failure_code_value!r}"
                ) from exc

        payload = data.get("payload")
        return cls(
            version=version,
            message_id=_require_str(data.get("message_id"), field_name="message_id"),
            kind=kind,
            created_at=_require_str(data.get("created_at"), field_name="created_at"),
            payload=payload,
            correlation_id=(
                None
                if data.get("correlation_id") is None
                else _require_str(
                    data.get("correlation_id"), field_name="correlation_id"
                )
            ),
            failure_code=failure_code,
            failure_message=(
                None
                if data.get("failure_message") is None
                else _require_str(
                    data.get("failure_message"), field_name="failure_message"
                )
            ),
        )

    def failure(
        self,
        code: IPCFailureCode,
        message: str,
    ) -> "IPCEnvelope":
        return IPCEnvelope(
            version=self.version,
            message_id=self.message_id,
            kind=IPCPayloadKind.ERROR,
            created_at=self.created_at,
            payload=self.payload,
            correlation_id=self.correlation_id,
            failure_code=code,
            failure_message=message,
        )


def decode_ipc_envelope(raw: str | bytes) -> IPCEnvelope:
    """Decode a JSON IPC envelope and enforce protocol versioning."""

    return IPCEnvelope.from_json(raw)


def encode_ipc_envelope(envelope: IPCEnvelope) -> bytes:
    """Serialize an IPC envelope to compact UTF-8 JSON bytes."""

    return envelope.to_json().encode("utf-8")


def build_failure_envelope(
    code: IPCFailureCode,
    message: str,
    *,
    correlation_id: str | None = None,
) -> IPCEnvelope:
    """Build a standard failure response envelope."""

    return IPCEnvelope(
        version=IPC_PROTOCOL_VERSION,
        message_id=uuid4().hex,
        kind=IPCPayloadKind.ERROR,
        created_at=datetime.utcnow().isoformat(),
        payload={"error": message, "code": code.value},
        correlation_id=correlation_id,
        failure_code=code,
        failure_message=message,
    )


def _serialize_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {key: _serialize_value(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_serialize_value(item) for item in value]
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, set):
        return [_serialize_value(item) for item in sorted(value, key=repr)]
    return repr(value)


def _require_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise IPCProtocolError(f"{field_name} must be an integer, got {type(value)!r}")
    return value


def _require_str(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise IPCProtocolError(f"{field_name} must be a string, got {type(value)!r}")
    return value
