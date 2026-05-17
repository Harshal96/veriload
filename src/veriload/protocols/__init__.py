"""Protocol adapters for VeriLoad scenarios."""

from veriload.protocols.database import DatabaseClient, DatabaseResult
from veriload.protocols.grpc import GrpcClient
from veriload.protocols.http import HttpClient
from veriload.protocols.websocket import WebSocketClient

__all__ = ["DatabaseClient", "DatabaseResult", "GrpcClient", "HttpClient", "WebSocketClient"]
