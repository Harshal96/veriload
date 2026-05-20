"""Networked controller/worker execution for distributed VeriLoad runs."""

from __future__ import annotations

import asyncio
import base64
import os
import platform
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from veriload.cleanup import CleanupFailure, CleanupSummary, merge_cleanup_summaries
from veriload.cluster import ClusterBus, ClusterMessage
from veriload.config import ConfigError, VeriLoadConfig
from veriload.data import Company, Contact, Job, Person, PersonaAllocator, PersonaPool, PersonaRecord
from veriload.distributed import (
    DistributedRunResult,
    WorkerRunResult,
    _spawn_rate_for_worker,
    _target_users_for_worker,
    plan_worker_partitions,
)
from veriload.distributed_protocol import ControlMessage, ProtocolError, read_message, write_message
from veriload.engine import LocalRunner
from veriload.metrics import (
    InMemoryMetricsSink,
    LatencySummary,
    MetricEvent,
    RequestFailed,
    RequestFinished,
    RunSummary,
    SegmentSummary,
)
from veriload.run_bundle import create_run_bundle, extract_run_bundle
from veriload.runtime import build_cleanup_snapshot, build_database_factory, build_persona_pool, build_profile
from veriload.safety import assert_persona_pool_safe
from veriload.scenarios import load_user_class

CONTROLLER_NODE_ID = "controller"


class NetworkDistributedError(RuntimeError):
    """Raised when a networked distributed run fails."""


@dataclass
class _WorkerConnection:
    node_id: str
    worker_index: int
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    state: str = "registered"


@dataclass(frozen=True)
class _WorkerExecution:
    result: WorkerRunResult
    events: tuple[MetricEvent, ...]


class NetworkDistributedController:
    """Controller process for a true networked distributed run."""

    def __init__(
        self,
        *,
        config: VeriLoadConfig,
        config_path: Path,
        bind_host: str = "127.0.0.1",
        bind_port: int = 5557,
        expect_workers: int,
        cluster_token: str,
        ready_timeout_seconds: float = 30,
    ) -> None:
        if expect_workers <= 0:
            raise ValueError("expect_workers must be greater than 0")
        if not cluster_token:
            raise ValueError("cluster_token is required")
        self.config = config
        self.config_path = config_path
        self.bind_host = bind_host
        self.bind_port = bind_port
        self.expect_workers = expect_workers
        self.cluster_token = cluster_token
        self.ready_timeout_seconds = ready_timeout_seconds
        self.run_id = str(uuid.uuid4())
        self._serving = asyncio.Event()
        self._ready = asyncio.Event()
        self._start = asyncio.Event()
        self._done = asyncio.Event()
        self._workers: dict[str, _WorkerConnection] = {}
        self._worker_results: dict[str, WorkerRunResult] = {}
        self._custom_messages: tuple[ClusterMessage, ...] = ()
        self._failures: list[BaseException] = []
        self._metrics = InMemoryMetricsSink()
        self._server: asyncio.Server | None = None
        self._pool: PersonaPool | None = None

    @property
    def port(self) -> int:
        """Return the bound controller port."""

        if self._server is None or not self._server.sockets:
            return self.bind_port
        return int(self._server.sockets[0].getsockname()[1])

    async def wait_until_serving(self) -> None:
        """Wait until the controller is accepting worker connections."""

        await self._serving.wait()

    @property
    def custom_messages(self) -> tuple[ClusterMessage, ...]:
        """Custom worker messages received during the run."""

        return self._custom_messages

    async def run(self) -> DistributedRunResult:
        """Run the controller until all expected workers finish."""

        if self.config.scenario is None:
            raise ConfigError("scenario.path and scenario.user_class are required for distributed controller")
        config_dir = self.config_path.parent
        scenario_path = self.config.scenario.path
        if not scenario_path.is_absolute():
            scenario_path = config_dir / scenario_path
        bundle = create_run_bundle(
            config_path=self.config_path,
            scenario_path=scenario_path,
            config_dir=config_dir,
        )
        self._pool = build_persona_pool(self.config)
        assert_persona_pool_safe(
            self._pool,
            require_non_routable_contacts=self.config.safety.require_non_routable_contacts,
        )
        self._server = await asyncio.start_server(
            lambda reader, writer: self._handle_worker(reader, writer, bundle),
            self.bind_host,
            self.bind_port,
        )
        self._serving.set()
        try:
            async with self._server:
                try:
                    await asyncio.wait_for(self._ready.wait(), timeout=self.ready_timeout_seconds)
                except TimeoutError as exc:
                    raise TimeoutError(f"expected {self.expect_workers} workers to become ready") from exc
                self._start.set()
                await self._done.wait()
        finally:
            self._close_workers()
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()
        if self._failures:
            raise NetworkDistributedError(str(self._failures[0])) from self._failures[0]
        workers = tuple(
            self._worker_results[node_id]
            for node_id in sorted(self._worker_results, key=lambda item: self._worker_results[item].worker_index)
        )
        return DistributedRunResult(
            summary=self._metrics.summary(),
            workers=workers,
            cleanup=merge_cleanup_summaries(tuple(worker.cleanup for worker in workers)),
        )

    async def _handle_worker(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        bundle: Any,
    ) -> None:
        connection: _WorkerConnection | None = None
        try:
            hello = await read_message(reader, token=self.cluster_token)
            if hello.message_type != "hello":
                raise ProtocolError("worker must send hello first")
            node_id = hello.node_id
            if node_id in self._workers:
                raise ProtocolError(f"duplicate worker node id: {node_id}")
            if len(self._workers) >= self.expect_workers:
                raise ProtocolError("too many workers connected")
            connection = _WorkerConnection(
                node_id=node_id,
                worker_index=len(self._workers),
                reader=reader,
                writer=writer,
            )
            self._workers[node_id] = connection
            await self._send(
                writer,
                "hello_ack",
                {"worker_index": connection.worker_index, "run_id": self.run_id},
                reply_to=hello.message_id,
            )
            await self._send(
                writer,
                "bundle_data",
                {
                    "sha256": bundle.sha256,
                    "manifest": bundle.manifest,
                    "archive_b64": base64.b64encode(bundle.archive_bytes).decode("ascii"),
                },
            )
            sync_complete = await read_message(reader, token=self.cluster_token)
            if sync_complete.message_type != "sync_complete":
                raise ProtocolError("worker did not confirm bundle sync")
            connection.state = "ready"
            if len([worker for worker in self._workers.values() if worker.state == "ready"]) >= self.expect_workers:
                self._ready.set()
            await self._start.wait()
            await self._send_assignment(connection, bundle.manifest)
            await self._send(writer, "start_run", {})
            await self._receive_worker_run(connection)
        except Exception as exc:  # noqa: BLE001 - handler converts arbitrary worker failures.
            if self._start.is_set():
                self._failures.append(exc)
                self._done.set()
        finally:
            writer.close()
            await writer.wait_closed()

    async def _send_assignment(self, connection: _WorkerConnection, bundle_manifest: dict[str, object]) -> None:
        assert self._pool is not None
        partitions = plan_worker_partitions(self._pool, worker_count=self.expect_workers)
        partition = partitions[connection.worker_index]
        target_users = _target_users_for_worker(
            self.config.run.users,
            self.expect_workers,
            connection.worker_index,
        )
        spawn_rate = _spawn_rate_for_worker(
            self.config.run.spawn_rate,
            total_target_users=self.config.run.users,
            target_users=target_users,
        )
        worker_pool = self._pool.partition(
            worker_index=connection.worker_index,
            worker_count=self.expect_workers,
        )
        await self._send(
            connection.writer,
            "assign_partition",
            {
                "worker_index": connection.worker_index,
                "worker_count": self.expect_workers,
                "target_users": target_users,
                "spawn_rate": spawn_rate,
                "run_seed": self.config.data.seed + connection.worker_index,
                "persona_ids": list(partition.persona_ids),
                "personas": [asdict(persona) for persona in worker_pool.personas],
                "config": _worker_config_payload(self.config, bundle_manifest),
                "scenario_path": str(bundle_manifest["scenario_path"]),
            },
        )

    async def _receive_worker_run(self, connection: _WorkerConnection) -> None:
        seen_sequences: set[int] = set()
        while True:
            message = await read_message(connection.reader, token=self.cluster_token)
            if message.message_type == "heartbeat":
                await self._send(connection.writer, "heartbeat_ack", {}, reply_to=message.message_id)
                continue
            if message.message_type == "metric_batch":
                sequence = int(message.payload["sequence"])
                if sequence not in seen_sequences:
                    seen_sequences.add(sequence)
                    for event_payload in message.payload["events"]:
                        self._metrics.record(_metric_event_from_payload(event_payload))
                await self._send(
                    connection.writer,
                    "metric_batch_ack",
                    {"sequence": sequence},
                    reply_to=message.message_id,
                )
                continue
            if message.message_type == "worker_summary":
                result = _worker_result_from_payload(message.payload)
                connection.state = "stopped"
                self._worker_results[connection.node_id] = result
                if len(self._worker_results) >= self.expect_workers:
                    self._done.set()
                return
            if message.message_type == "custom":
                self._custom_messages = (
                    *self._custom_messages,
                    ClusterMessage(
                        name=str(message.payload["name"]),
                        payload=message.payload["payload"],
                        source=message.node_id,
                        target=str(message.payload.get("target", CONTROLLER_NODE_ID)),
                    ),
                )
                continue
            if message.message_type == "run_failed":
                raise NetworkDistributedError(str(message.payload.get("error", "worker failed")))
            raise ProtocolError(f"unexpected worker message: {message.message_type}")

    async def _send(
        self,
        writer: asyncio.StreamWriter,
        message_type: str,
        payload: dict[str, Any],
        *,
        reply_to: str | None = None,
    ) -> None:
        await write_message(
            writer,
            ControlMessage.create(
                run_id=self.run_id,
                message_type=message_type,
                node_id=CONTROLLER_NODE_ID,
                payload=payload,
                reply_to=reply_to,
            ),
            token=self.cluster_token,
        )

    def _close_workers(self) -> None:
        for worker in self._workers.values():
            worker.writer.close()


class NetworkDistributedWorker:
    """Worker process that connects to a networked VeriLoad controller."""

    def __init__(
        self,
        *,
        controller_host: str,
        controller_port: int,
        work_dir: Path,
        cluster_token: str,
        node_id: str | None = None,
    ) -> None:
        if not cluster_token:
            raise ValueError("cluster_token is required")
        self.controller_host = controller_host
        self.controller_port = controller_port
        self.work_dir = work_dir
        self.cluster_token = cluster_token
        self.node_id = node_id or f"{platform.node() or 'worker'}-{os.getpid()}"
        self.run_id = "pending"

    async def run(self) -> WorkerRunResult:
        """Connect to the controller, download the run bundle, and execute one partition."""

        reader, writer = await asyncio.open_connection(self.controller_host, self.controller_port)
        try:
            await write_message(
                writer,
                ControlMessage.create(
                    run_id=self.run_id,
                    message_type="hello",
                    node_id=self.node_id,
                    payload={
                        "python": platform.python_version(),
                        "platform": platform.platform(),
                        "pid": os.getpid(),
                    },
                ),
                token=self.cluster_token,
            )
            hello_ack = await read_message(reader, token=self.cluster_token)
            if hello_ack.message_type != "hello_ack":
                raise ProtocolError("controller did not acknowledge worker hello")
            self.run_id = str(hello_ack.payload["run_id"])
            bundle_message = await read_message(reader, token=self.cluster_token)
            if bundle_message.message_type != "bundle_data":
                raise ProtocolError("controller did not send a run bundle")
            extracted = extract_run_bundle(
                base64.b64decode(str(bundle_message.payload["archive_b64"])),
                self.work_dir,
                expected_sha256=str(bundle_message.payload["sha256"]),
            )
            await self._send(writer, "sync_complete", {"bundle_sha256": bundle_message.payload["sha256"]})
            assignment = await read_message(reader, token=self.cluster_token)
            if assignment.message_type != "assign_partition":
                raise ProtocolError("controller did not assign a worker partition")
            start = await read_message(reader, token=self.cluster_token)
            if start.message_type != "start_run":
                raise ProtocolError("controller did not start the run")
            execution = await _execute_worker_assignment(
                extracted.bundle_dir,
                assignment.payload,
                cluster=_WorkerClusterBus(
                    writer=writer,
                    token=self.cluster_token,
                    run_id=self.run_id,
                    node_id=self.node_id,
                ),
            )
            await self._send(
                writer,
                "metric_batch",
                {
                    "sequence": 1,
                    "events": [_metric_event_to_payload(event) for event in execution.events],
                },
            )
            ack = await read_message(reader, token=self.cluster_token)
            if ack.message_type != "metric_batch_ack":
                raise ProtocolError("controller did not acknowledge metric batch")
            await self._send(writer, "worker_summary", _worker_result_to_payload(execution.result))
            return execution.result
        finally:
            writer.close()
            await writer.wait_closed()

    async def _send(
        self,
        writer: asyncio.StreamWriter,
        message_type: str,
        payload: dict[str, Any],
    ) -> None:
        await write_message(
            writer,
            ControlMessage.create(
                run_id=self.run_id,
                message_type=message_type,
                node_id=self.node_id,
                payload=payload,
            ),
            token=self.cluster_token,
        )


class _WorkerClusterBus:
    """Cluster bus that sends custom messages from worker user code to the controller."""

    def __init__(
        self,
        *,
        writer: asyncio.StreamWriter,
        token: str,
        run_id: str,
        node_id: str,
    ) -> None:
        self._writer = writer
        self._token = token
        self._run_id = run_id
        self._node_id = node_id
        self._lock = asyncio.Lock()

    async def send(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        target: str = CONTROLLER_NODE_ID,
    ) -> None:
        async with self._lock:
            await write_message(
                self._writer,
                ControlMessage.create(
                    run_id=self._run_id,
                    message_type="custom",
                    node_id=self._node_id,
                    payload={"name": name, "payload": payload, "target": target},
                ),
                token=self._token,
            )


async def _execute_worker_assignment(
    bundle_dir: Path,
    payload: dict[str, Any],
    *,
    cluster: ClusterBus | None = None,
) -> _WorkerExecution:
    config = VeriLoadConfig.model_validate(payload["config"])
    if config.scenario is None:
        raise ConfigError("networked worker assignment missing scenario")
    user_class = load_user_class(bundle_dir / str(payload["scenario_path"]), config.scenario.user_class)
    worker_index = int(payload["worker_index"])
    target_users = int(payload["target_users"])
    personas = tuple(_persona_from_payload(item) for item in payload["personas"])
    metrics = InMemoryMetricsSink()
    runner = LocalRunner(
        user_classes=[user_class],
        profile=build_profile(config.profile, target_users=target_users),
        persona_allocator=PersonaAllocator(
            PersonaPool(personas),
            mode="unique",
            seed=int(payload["run_seed"]),
        ),
        metrics_sink=metrics,
        run_seed=int(payload["run_seed"]),
        base_url=config.run.base_url,
        database_factory=build_database_factory(config.run.database),
        cleanup_config=build_cleanup_snapshot(config.cleanup),
        spawn_rate=payload.get("spawn_rate"),
        cluster=cluster,
    )
    summary = await runner.run()
    result = WorkerRunResult(
        worker_index=worker_index,
        target_users=target_users,
        persona_ids=tuple(payload["persona_ids"]),
        summary=summary,
        cleanup=runner.cleanup_summary,
    )
    return _WorkerExecution(result=result, events=metrics.events)


def _worker_config_payload(config: VeriLoadConfig, bundle_manifest: dict[str, object]) -> dict[str, Any]:
    payload = config.model_dump(mode="json")
    if config.scenario is not None:
        payload["scenario"] = {
            "path": str(bundle_manifest["scenario_path"]),
            "user_class": config.scenario.user_class,
        }
    return payload


def _persona_from_payload(payload: dict[str, Any]) -> PersonaRecord:
    return PersonaRecord(
        persona_id=str(payload["persona_id"]),
        locale=str(payload["locale"]),
        person=Person(**payload["person"]),
        contact=Contact(**payload["contact"]),
        job=Job(**payload["job"]),
        company=Company(**payload["company"]),
    )


def _metric_event_to_payload(event: MetricEvent) -> dict[str, Any]:
    payload = asdict(event)
    payload["event_type"] = "failed" if isinstance(event, RequestFailed) else "finished"
    return payload


def _metric_event_from_payload(payload: dict[str, Any]) -> MetricEvent:
    event_type = payload.get("event_type")
    data = {key: value for key, value in payload.items() if key != "event_type"}
    if event_type == "failed":
        return RequestFailed(**data)
    if event_type == "finished":
        return RequestFinished(**data)
    raise ProtocolError(f"unsupported metric event type: {event_type}")


def _worker_result_to_payload(result: WorkerRunResult) -> dict[str, Any]:
    return asdict(result)


def _worker_result_from_payload(payload: dict[str, Any]) -> WorkerRunResult:
    return WorkerRunResult(
        worker_index=int(payload["worker_index"]),
        target_users=int(payload["target_users"]),
        persona_ids=tuple(payload["persona_ids"]),
        summary=_run_summary_from_payload(payload["summary"]),
        cleanup=_cleanup_summary_from_payload(payload.get("cleanup", {})),
    )


def _run_summary_from_payload(payload: dict[str, Any]) -> RunSummary:
    return RunSummary(
        total_requests=int(payload["total_requests"]),
        total_failures=int(payload["total_failures"]),
        error_rate=float(payload["error_rate"]),
        latency_ms=_latency_summary_from_payload(payload["latency_ms"]),
        segments={
            key: _segment_summary_from_payload(value)
            for key, value in payload["segments"].items()
        },
        endpoints={
            key: _segment_summary_from_payload(value)
            for key, value in payload["endpoints"].items()
        },
    )


def _cleanup_summary_from_payload(payload: dict[str, Any]) -> CleanupSummary:
    if not payload:
        return CleanupSummary()
    return CleanupSummary(
        enabled=bool(payload.get("enabled", False)),
        attempted=int(payload.get("attempted", 0)),
        succeeded=int(payload.get("succeeded", 0)),
        failed=int(payload.get("failed", 0)),
        failures=tuple(
            CleanupFailure(
                kind=str(item["kind"]),
                target=str(item["target"]),
                error=str(item["error"]),
            )
            for item in payload.get("failures", ())
        ),
    )


def _segment_summary_from_payload(payload: dict[str, Any]) -> SegmentSummary:
    return SegmentSummary(
        total_requests=int(payload["total_requests"]),
        total_failures=int(payload["total_failures"]),
        error_rate=float(payload["error_rate"]),
        latency_ms=_latency_summary_from_payload(payload["latency_ms"]),
    )


def _latency_summary_from_payload(payload: dict[str, Any]) -> LatencySummary:
    return LatencySummary(
        p50=float(payload["p50"]),
        p90=float(payload["p90"]),
        p95=float(payload["p95"]),
        p99=float(payload["p99"]),
        mean=float(payload["mean"]),
        max=float(payload["max"]),
    )
