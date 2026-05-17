"""Signed control-plane messages for networked distributed runs."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import asdict, dataclass, replace
from typing import Any

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 8 * 1024 * 1024


class ProtocolError(RuntimeError):
    """Raised when a control-plane message is invalid."""


@dataclass(frozen=True)
class ControlMessage:
    """One signed JSON control-plane message."""

    protocol_version: int
    run_id: str
    message_type: str
    message_id: str
    node_id: str
    sent_at: float
    payload: dict[str, Any]
    reply_to: str | None = None
    signature: str | None = None

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        message_type: str,
        node_id: str,
        payload: dict[str, Any],
        reply_to: str | None = None,
        protocol_version: int = PROTOCOL_VERSION,
    ) -> ControlMessage:
        """Create an unsigned message with a generated ID and timestamp."""

        return cls(
            protocol_version=protocol_version,
            run_id=run_id,
            message_type=message_type,
            message_id=str(uuid.uuid4()),
            node_id=node_id,
            sent_at=time.time(),
            payload=payload,
            reply_to=reply_to,
        )

    def without_signature(self) -> ControlMessage:
        """Return a copy with the signature removed."""

        return replace(self, signature=None)

    def signed(self, token: str) -> ControlMessage:
        """Return a copy signed with the shared cluster token."""

        return replace(self, signature=_signature(_canonical_message(self.without_signature()), token))


async def write_message(
    writer: asyncio.StreamWriter,
    message: ControlMessage,
    *,
    token: str,
) -> None:
    """Write one length-prefixed signed message to a stream."""

    frame = encode_frame(message, token=token)
    writer.write(len(frame).to_bytes(4, "big") + frame)
    await writer.drain()


async def read_message(
    reader: asyncio.StreamReader,
    *,
    token: str,
    max_frame_bytes: int = MAX_FRAME_BYTES,
) -> ControlMessage:
    """Read and validate one length-prefixed signed message from a stream."""

    try:
        header = await reader.readexactly(4)
        frame_size = int.from_bytes(header, "big")
        if frame_size <= 0 or frame_size > max_frame_bytes:
            raise ProtocolError("frame size exceeds control-plane limit")
        return decode_frame(await reader.readexactly(frame_size), token=token)
    except asyncio.IncompleteReadError as exc:
        raise ProtocolError("control-plane connection closed") from exc


def encode_frame(message: ControlMessage, *, token: str) -> bytes:
    """Encode a signed message frame."""

    _validate_token(token)
    signed = message.signed(token)
    return _canonical_message(signed)


def decode_frame(frame: bytes, *, token: str) -> ControlMessage:
    """Decode and verify one signed message frame."""

    _validate_token(token)
    try:
        raw = json.loads(frame.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid JSON control-plane frame") from exc
    if not isinstance(raw, dict):
        raise ProtocolError("control-plane frame must be a JSON object")
    try:
        message = ControlMessage(
            protocol_version=int(raw["protocol_version"]),
            run_id=str(raw["run_id"]),
            message_type=str(raw["message_type"]),
            message_id=str(raw["message_id"]),
            node_id=str(raw["node_id"]),
            sent_at=float(raw["sent_at"]),
            payload=raw["payload"],
            reply_to=raw.get("reply_to"),
            signature=raw.get("signature"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProtocolError("missing or invalid control-plane field") from exc
    if message.protocol_version != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported protocol version: {message.protocol_version}")
    if not isinstance(message.payload, dict):
        raise ProtocolError("message payload must be a JSON object")
    expected = _signature(_canonical_message(message.without_signature()), token)
    if message.signature is None or not hmac.compare_digest(message.signature, expected):
        raise ProtocolError("invalid control-plane message signature")
    return message


def _validate_token(token: str) -> None:
    if not token:
        raise ProtocolError("cluster token is required")


def _canonical_message(message: ControlMessage) -> bytes:
    return json.dumps(
        asdict(message),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _signature(canonical_message: bytes, token: str) -> str:
    return hmac.new(token.encode("utf-8"), canonical_message, hashlib.sha256).hexdigest()
