import pytest

from veriload.distributed_protocol import ControlMessage, ProtocolError, decode_frame, encode_frame


def test_control_message_round_trips_with_valid_signature() -> None:
    message = ControlMessage.create(
        run_id="run-1",
        message_type="hello",
        node_id="worker-1",
        payload={"capabilities": ["http"]},
    )

    decoded = decode_frame(encode_frame(message, token="secret"), token="secret")

    assert decoded.message_type == "hello"
    assert decoded.node_id == "worker-1"
    assert decoded.payload == {"capabilities": ["http"]}


def test_control_message_rejects_tampered_signature() -> None:
    message = ControlMessage.create(
        run_id="run-1",
        message_type="hello",
        node_id="worker-1",
        payload={"capabilities": ["http"]},
    )
    encoded = encode_frame(message, token="secret")
    tampered = encoded.replace(b'"worker-1"', b'"worker-2"', 1)

    with pytest.raises(ProtocolError, match="signature"):
        decode_frame(tampered, token="secret")


def test_control_message_rejects_unsupported_protocol_version() -> None:
    message = ControlMessage.create(
        run_id="run-1",
        message_type="hello",
        node_id="worker-1",
        payload={},
        protocol_version=99,
    )

    with pytest.raises(ProtocolError, match="protocol"):
        decode_frame(encode_frame(message, token="secret"), token="secret")
