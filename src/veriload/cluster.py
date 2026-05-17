"""Custom inter-node message primitives."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ClusterMessage:
    """Application-defined message exchanged across a VeriLoad cluster."""

    name: str
    payload: dict[str, Any]
    source: str
    target: str


@dataclass(frozen=True)
class DistributedMessageDefinition:
    """Metadata attached to a custom distributed message handler."""

    name: str
    concurrent: bool = False


MessageHandler = Callable[[ClusterMessage], Awaitable[None] | None]


class ClusterBus(Protocol):
    """Minimal message bus exposed to scenario code."""

    async def send(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        target: str = "controller",
    ) -> None:
        """Send a JSON-serializable custom message."""


def distributed_message(
    name: str,
    *,
    concurrent: bool = False,
) -> Callable[[MessageHandler], MessageHandler]:
    """Mark a function as a handler for a custom distributed message."""

    if not name:
        raise ValueError("message name must not be empty")

    def decorator(handler: MessageHandler) -> MessageHandler:
        setattr(
            handler,
            "__veriload_message__",
            DistributedMessageDefinition(name=name, concurrent=concurrent),
        )
        return handler

    return decorator


class NullClusterBus:
    """No-op bus used when a runner has no cluster attached."""

    async def send(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        target: str = "controller",
    ) -> None:
        """Drop a custom message."""


class InProcessClusterBus:
    """Loopback message bus for local runs and tests."""

    def __init__(self, *, node_id: str = "local") -> None:
        self.node_id = node_id
        self._handlers: tuple[tuple[str, MessageHandler], ...] = ()

    def register(self, name: str, handler: MessageHandler) -> None:
        """Register a handler for one custom message name."""

        if not name:
            raise ValueError("message name must not be empty")
        self._handlers = (*self._handlers, (name, handler))

    async def send(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        target: str = "controller",
    ) -> None:
        """Deliver a custom message to matching local handlers."""

        message = ClusterMessage(name=name, payload=payload, source=self.node_id, target=target)
        for handler_name, handler in self._handlers:
            if handler_name != name:
                continue
            result = handler(message)
            if inspect.isawaitable(result):
                await result
