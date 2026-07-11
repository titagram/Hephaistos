"""Profile-scoped receiver and authority gate for Persephone agent messages.

The backend queue is only a transport.  This module independently binds every
message to a locally linked project/agent/workspace tuple, persists it, then
revalidates the immutable stored envelope before any request may proceed.
Actual information retrieval belongs to :mod:`hades_information_worker` (O5).
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import sqlite3
import threading
import time
from typing import Any, Callable, ContextManager, Iterable, Mapping
import uuid

from hermes_cli import hades_backend_db as db
from hermes_cli.hades_persephone_messages import (
    BACKEND_CAPABILITY,
    AGENT_MESSAGE_SCHEMA,
    AgentMessageEnvelope,
    EffectClass,
    MessageType,
    parse_envelope,
)
from hermes_cli.hades_persephone_store import (
    claim_information_request,
    get_cursor,
    get_message,
    record_cursor,
    record_inbox,
    record_subscription_mismatch,
    recover_abandoned_information_requests,
    recover_abandoned_outbox,
    transition_message,
)
from hermes_cli.hades_persephone_transport import (
    iter_persephone_events,
    send_due_messages,
)


logger = logging.getLogger("hermes_cli.hades_backend")

AUTO_INFORMATION_CAPABILITIES = frozenset(
    {
        "source_slice",
        "source_search",
        "symbol_lookup",
        "git_metadata",
        "artifact_metadata",
        "project_memory_search",
    }
)


def classify_request(envelope: AgentMessageEnvelope) -> str:
    """Return the local policy disposition without granting new authority.

    Only an explicitly information-only request in the fixed allow-list is
    auto-accepted.  Unknown effects, capabilities, and request types are held
    for a local human instead of being inferred safe from their names.
    """
    if (
        envelope.message_type == MessageType.INFORMATION_REQUEST
        and envelope.effect == EffectClass.INFORMATION_READ
        and envelope.capability in AUTO_INFORMATION_CAPABILITIES
    ):
        return "accepted"
    return "waiting_human_approval"


@dataclass
class _ReceiverWorker:
    project_id: str
    agent_id: str
    agent: db.BackendAgent | None
    client: object | None = None
    queue_supported: bool | None = None
    descriptor: tuple[Any, ...] = ()
    failure_count: int = 0
    next_retry_at: int | None = None
    capability_next_probe_at: int | None = None


ConnectionFactory = Callable[[], ContextManager[sqlite3.Connection]]
ClientFactory = Callable[[db.BackendAgent], object]
EventReader = Callable[..., Iterable[dict[str, Any]]]
InformationExecutor = Callable[..., object]


class PersephoneReceiver:
    """Own one bounded, fair subscription worker per project/agent identity."""

    def __init__(
        self,
        *,
        connection_factory: ConnectionFactory = db.connect_closing,
        client_factory: ClientFactory | None = None,
        event_reader: EventReader = iter_persephone_events,
        poll_interval: float = 5.0,
        batch_size: int = 50,
        max_projects_per_cycle: int = 8,
        information_executor: InformationExecutor | None = None,
        response_id_factory: Callable[[], str] | None = None,
        now: Callable[[], int] | None = None,
    ) -> None:
        if not 1 <= int(batch_size) <= 100:
            raise ValueError("batch_size must be between 1 and 100")
        if int(max_projects_per_cycle) < 1:
            raise ValueError("max_projects_per_cycle must be positive")
        if float(poll_interval) < 0:
            raise ValueError("poll_interval must be non-negative")
        self.connection_factory = connection_factory
        self.client_factory = client_factory
        self.event_reader = event_reader
        self.poll_interval = float(poll_interval)
        self.batch_size = int(batch_size)
        self.max_projects_per_cycle = int(max_projects_per_cycle)
        self.information_executor = information_executor
        self.response_id_factory = response_id_factory
        self._now = now or (lambda: int(time.time()))
        self._bindings: dict[str, db.WorkspaceBinding] = {}
        self._workers: dict[tuple[str, str], _ReceiverWorker] = {}
        self._queue_capability = True
        self._next_worker = 0
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._flush_thread: threading.Thread | None = None
        self._flush_succeeded = True
        self._fatal_error = False
        self._draining = False
        self._stopped = False

    @property
    def workers(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            return tuple(sorted(self._workers))

    def refresh_bindings(
        self,
        bindings: Iterable[db.WorkspaceBinding] | None = None,
        *,
        agents: Mapping[str, db.BackendAgent] | None = None,
        queue_capability: bool | None = None,
    ) -> None:
        """Atomically replace routes with all linked bindings in the profile."""
        loaded_agents = dict(agents or {})
        if bindings is None:
            with self.connection_factory() as conn:
                linked = db.list_workspace_bindings(conn, status="linked")
                for binding in linked:
                    if binding.agent_id not in loaded_agents:
                        agent = db.get_agent(conn, binding.agent_id)
                        if agent is not None:
                            loaded_agents[binding.agent_id] = agent
        else:
            linked = list(bindings)

        routes: dict[str, db.WorkspaceBinding] = {}
        ambiguous_routes: set[str] = set()
        worker_keys: set[tuple[str, str]] = set()
        bindings_by_worker: dict[tuple[str, str], list[db.WorkspaceBinding]] = {}
        for binding in linked:
            if binding.status != "linked":
                continue
            # Duplicate backend IDs are never allowed to silently select a
            # different workspace.  Keep the first deterministic route only.
            binding_id = binding.backend_workspace_binding_id
            existing = routes.get(binding_id)
            if existing is None:
                routes[binding_id] = binding
            elif existing != binding:
                ambiguous_routes.add(binding_id)
            key = (binding.project_id, binding.agent_id)
            worker_keys.add(key)
            bindings_by_worker.setdefault(key, []).append(binding)
        for binding_id in ambiguous_routes:
            routes.pop(binding_id, None)

        clients_to_close: list[object] = []
        with self._lock:
            previous = self._workers
            self._bindings = routes
            if queue_capability is not None:
                self._queue_capability = queue_capability is True
            self._workers = {}
            for key in sorted(worker_keys):
                old = previous.get(key)
                agent = loaded_agents.get(key[1]) or (old.agent if old else None)
                worker_bindings = tuple(
                    sorted(
                        bindings_by_worker.get(key, []),
                        key=lambda item: (
                            item.backend_workspace_binding_id,
                            item.workspace_fingerprint,
                            item.repo_root,
                        ),
                    )
                )
                descriptor = (agent, worker_bindings)
                reusable = old is not None and old.descriptor == descriptor
                self._workers[key] = _ReceiverWorker(
                    project_id=key[0],
                    agent_id=key[1],
                    agent=agent,
                    client=old.client if reusable else None,
                    queue_supported=(old.queue_supported if reusable else None),
                    descriptor=descriptor,
                    failure_count=(old.failure_count if reusable else 0),
                    next_retry_at=(old.next_retry_at if reusable else None),
                    capability_next_probe_at=(
                        old.capability_next_probe_at if reusable else None
                    ),
                )
                if old is not None and not reusable and old.client is not None:
                    clients_to_close.append(old.client)
            for key, old in previous.items():
                if key not in self._workers and old.client is not None:
                    clients_to_close.append(old.client)
            if self._workers:
                self._next_worker %= len(self._workers)
            else:
                self._next_worker = 0
        self._close_detached_clients(clients_to_close)
        timestamp = self._now()
        with self.connection_factory() as conn:
            if self.information_executor is not None:
                recover_abandoned_information_requests(
                    conn,
                    now=timestamp,
                    abandoned_before=timestamp - 30,
                    limit=self.batch_size,
                )
            recover_abandoned_outbox(
                conn,
                now=timestamp,
                abandoned_before=timestamp - 30,
            )

    def start(self) -> None:
        """Start the single owned coordinator thread; repeated calls are safe."""
        with self._lock:
            if self.thread is not None and self.thread.is_alive():
                return
            if not self._workers:
                self.refresh_bindings()
            self._stop_event.clear()
            self._fatal_error = False
            self._draining = False
            self._stopped = False
            thread = threading.Thread(
                target=self._run_loop,
                name="hades-persephone-receiver",
                daemon=True,
            )
            self.thread = thread
            thread.start()

    def set_queue_capability(
        self, *, project_id: str, agent_id: str, supported: bool
    ) -> None:
        """Record authenticated backend capability discovery for one queue."""
        with self._lock:
            worker = self._workers.get((project_id, agent_id))
            if worker is not None:
                worker.queue_supported = supported is True

    def stop(self, *, timeout: float | None = 5.0) -> bool:
        """Stop polling and close every client owned by this receiver."""
        with self._lock:
            self._draining = True
            thread = self.thread
            self._stop_event.set()
            flush_workers = tuple(self._workers.values())
            flush_thread = self._flush_thread
            if flush_thread is None or not flush_thread.is_alive():
                total_timeout = 5.0 if timeout is None else max(0.0, float(timeout))
                flush_deadline = time.monotonic() + min(1.0, total_timeout / 2)
                self._flush_succeeded = False
                flush_thread = threading.Thread(
                    target=self._flush_owned_outbox,
                    args=(flush_workers, flush_deadline),
                    name="hades-persephone-outbox-flush",
                    daemon=True,
                )
                self._flush_thread = flush_thread
                flush_thread.start()
        total_timeout = 5.0 if timeout is None else max(0.0, float(timeout))
        flush_thread.join(timeout=min(1.0, total_timeout / 2))
        with self._lock:
            clients = self._detach_worker_clients_locked()
        # Closing active clients interrupts both a stream and an overdue flush.
        self._close_detached_clients(clients)
        remaining_timeout = max(0.0, total_timeout - min(1.0, total_timeout / 2))
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=remaining_timeout)
        flush_thread.join(timeout=0.1)
        with self._lock:
            joined = thread is None or not thread.is_alive()
            flushed = not flush_thread.is_alive() and self._flush_succeeded
            if joined and flushed:
                self.thread = None
                self._flush_thread = None
                self._draining = False
                self._stopped = True
                for worker in self._workers.values():
                    worker.client = None
                return True
            return False

    def _flush_owned_outbox(
        self,
        flush_workers: tuple[_ReceiverWorker, ...],
        deadline: float,
    ) -> None:
        """Attempt a bounded sender-scoped flush after intake is quiesced."""
        remaining = self.batch_size
        flushed_scopes: set[tuple[str, str]] = set()
        succeeded = True
        for worker in flush_workers:
            scope = (worker.project_id, worker.agent_id)
            if remaining <= 0 or scope in flushed_scopes:
                continue
            flushed_scopes.add(scope)
            client = worker.client
            temporary_client = False
            try:
                with self.connection_factory() as conn:
                    due = conn.execute(
                        "SELECT 1 FROM persephone_outbox "
                        "WHERE project_id = ? AND sender_agent_id = ? "
                        "AND state IN ('outbox_pending', 'retry') "
                        "AND next_attempt_at <= ? LIMIT 1",
                        (worker.project_id, worker.agent_id, int(self._now())),
                    ).fetchone()
                if due is None:
                    continue
                if time.monotonic() >= deadline:
                    succeeded = False
                    break
                if client is None:
                    if worker.agent is None or self.client_factory is None:
                        succeeded = False
                        continue
                    client = self.client_factory(worker.agent)
                    temporary_client = True
                    capabilities = client.capabilities()
                    if not (
                        isinstance(capabilities, dict)
                        and capabilities.get(BACKEND_CAPABILITY) is True
                    ):
                        succeeded = False
                        continue
                    if time.monotonic() >= deadline:
                        succeeded = False
                        continue
                with self.connection_factory() as conn:
                    counts = send_due_messages(
                        conn,
                        client,
                        now=self._now(),
                        limit=remaining,
                        project_id=worker.project_id,
                        sender_agent_id=worker.agent_id,
                    )
                remaining -= sum(int(value) for value in counts.values())
            except Exception:
                succeeded = False
                logger.exception(
                    "hades_persephone.receiver_shutdown_flush_failed",
                    extra={
                        "hades_event": "persephone.receiver_shutdown_flush_failed",
                        "hades_project_id": worker.project_id,
                        "hades_agent_id": worker.agent_id,
                    },
                )
            finally:
                if temporary_client and client is not None:
                    self._close_detached_clients([client])
        with self._lock:
            self._flush_succeeded = succeeded

    @staticmethod
    def _unique_clients(clients: Iterable[object]) -> list[object]:
        unique: list[object] = []
        for client in clients:
            if not any(existing is client for existing in unique):
                unique.append(client)
        return unique

    def _close_detached_clients(self, clients: Iterable[object]) -> None:
        """Close clients whose ownership was already removed under the lock."""
        for client in self._unique_clients(clients):
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    logger.exception(
                        "hades_persephone.receiver_client_close_failed",
                        extra={
                            "hades_event": "persephone.receiver_client_close_failed"
                        },
                    )

    def _detach_worker_clients_locked(self) -> list[object]:
        clients = self._unique_clients(
            worker.client
            for worker in self._workers.values()
            if worker.client is not None
        )
        for worker in self._workers.values():
            worker.client = None
        return clients

    def _release_worker_clients(self) -> None:
        with self._lock:
            clients = self._detach_worker_clients_locked()
        self._close_detached_clients(clients)

    def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    self.run_once()
                except Exception:
                    logger.exception(
                        "hades_persephone.receiver_cycle_failed",
                        extra={"hades_event": "persephone.receiver_cycle_failed"},
                    )
                self._stop_event.wait(self.poll_interval)
        except BaseException:
            with self._lock:
                self._fatal_error = True
            logger.exception(
                "hades_persephone.receiver_crashed",
                extra={"hades_event": "persephone.receiver_crashed"},
            )
        finally:
            self._release_worker_clients()
            with self._lock:
                if self.thread is threading.current_thread():
                    self.thread = None

    def health_snapshot(self) -> dict[str, Any]:
        """Return bounded lifecycle health without message payload contents."""
        with self._lock:
            workers = tuple(self._workers.values())
            thread = self.thread
            fatal = self._fatal_error
            queue_capability = self._queue_capability
        projects = len({worker.project_id for worker in workers})
        failure_count = sum(worker.failure_count for worker in workers)
        retry_values = [
            worker.next_retry_at
            for worker in workers
            if worker.next_retry_at is not None
        ]
        if fatal or (thread is not None and not thread.is_alive()):
            state = "failed"
        elif self._draining:
            state = "draining"
        elif self._stopped:
            state = "stopped"
        elif not queue_capability or not workers or all(
            worker.queue_supported is False for worker in workers
        ):
            state = "disabled_capability"
        elif failure_count:
            state = "backoff"
        elif any(worker.queue_supported is True for worker in workers):
            state = "connected"
        else:
            state = "polling"
        return {
            "state": state,
            "active": bool(
                thread is not None
                and thread.is_alive()
                and state in {"polling", "connected", "backoff", "draining"}
            ),
            "projects": projects,
            "failure_count": failure_count,
            "next_retry_at": min(retry_values) if retry_values else None,
        }

    def _worker_batch(self) -> list[_ReceiverWorker]:
        with self._lock:
            ordered = [self._workers[key] for key in sorted(self._workers)]
            if not ordered:
                return []
            count = min(len(ordered), self.max_projects_per_cycle)
            start = self._next_worker % len(ordered)
            selected = [ordered[(start + offset) % len(ordered)] for offset in range(count)]
            self._next_worker = (start + count) % len(ordered)
            return selected

    def _prepare_worker(self, worker: _ReceiverWorker) -> object | None:
        if not self._queue_capability or worker.agent is None or self.client_factory is None:
            return None
        key = (worker.project_id, worker.agent_id)
        with self._lock:
            if self._stop_event.is_set() or self._workers.get(key) is not worker:
                return None
            if worker.client is None:
                worker.client = self.client_factory(worker.agent)
            client = worker.client
            known_supported = worker.queue_supported is True
            if (
                worker.queue_supported is False
                and worker.capability_next_probe_at is not None
                and worker.capability_next_probe_at > self._now()
            ):
                return None
        if not known_supported:
            response = client.capabilities()
            supported = bool(
                isinstance(response, dict) and response.get(BACKEND_CAPABILITY) is True
            )
            with self._lock:
                if self._workers.get(key) is not worker or worker.client is not client:
                    return None
                worker.queue_supported = supported
                worker.capability_next_probe_at = (
                    None if supported else int(self._now()) + 300
                )
            if not supported:
                return None
        return client

    def probe_capabilities(self) -> int:
        """Probe every configured identity once without opening an inbox stream."""
        supported = 0
        with self._lock:
            workers = tuple(self._workers[key] for key in sorted(self._workers))
        for worker in workers:
            try:
                if self._prepare_worker(worker) is not None:
                    supported += 1
            except Exception:
                self._record_worker_failure(worker)
                logger.exception(
                    "hades_persephone.receiver_capability_probe_failed",
                    extra={
                        "hades_event": "persephone.receiver_capability_probe_failed",
                        "hades_project_id": worker.project_id,
                        "hades_agent_id": worker.agent_id,
                    },
                )
        return supported

    def _record_worker_failure(self, worker: _ReceiverWorker) -> None:
        with self._lock:
            worker.failure_count += 1
            delay = min(300, 2 ** min(worker.failure_count, 8))
            worker.next_retry_at = int(self._now()) + delay

    def run_once(self) -> int:
        """Poll a fair bounded subset of project queues and durably ingest it."""
        timestamp = self._now()
        with self.connection_factory() as conn:
            if self.information_executor is not None:
                recover_abandoned_information_requests(
                    conn,
                    now=timestamp,
                    abandoned_before=timestamp - 30,
                    limit=self.batch_size,
                )
            recover_abandoned_outbox(
                conn,
                now=timestamp,
                abandoned_before=timestamp - 30,
            )
        ingested = 0
        delivered_scopes: set[tuple[str, str]] = set()
        for worker in self._worker_batch():
            timestamp = self._now()
            if (
                worker.next_retry_at is not None
                and worker.next_retry_at > timestamp
            ):
                continue
            try:
                client = self._prepare_worker(worker)
                if client is None:
                    continue
                with self.connection_factory() as conn:
                    delivery_scope = (worker.project_id, worker.agent_id)
                    if delivery_scope not in delivered_scopes:
                        send_due_messages(
                            conn,
                            client,
                            now=timestamp,
                            limit=self.batch_size,
                            project_id=worker.project_id,
                            sender_agent_id=worker.agent_id,
                        )
                        delivered_scopes.add(delivery_scope)
                    cursor = get_cursor(
                        conn,
                        project_id=worker.project_id,
                        target_agent_id=worker.agent_id,
                    )
                events = self.event_reader(
                    client,
                    project_id=worker.project_id,
                    target_agent_id=worker.agent_id,
                    cursor=cursor,
                    limit=self.batch_size,
                )
                for event in events:
                    if self._stop_event.is_set():
                        break
                    self.ingest_event(
                        event,
                        expected_project_id=worker.project_id,
                        expected_target_agent_id=worker.agent_id,
                    )
                    ingested += 1
                with self._lock:
                    worker.failure_count = 0
                    worker.next_retry_at = None
            except Exception:
                # One broken project must not starve the following project in
                # the same round-robin cycle.
                logger.exception(
                    "hades_persephone.receiver_worker_failed",
                    extra={
                        "hades_event": "persephone.receiver_worker_failed",
                        "hades_project_id": worker.project_id,
                        "hades_agent_id": worker.agent_id,
                    },
                )
                self._record_worker_failure(worker)
        return ingested

    @staticmethod
    def _event_envelope(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
        if event.get("schema") == AGENT_MESSAGE_SCHEMA:
            # SSE injects its opaque transport cursor as ``id`` into the JSON
            # event.  It is not an envelope extension and must not make the
            # otherwise exact O1 contract fail unknown-field validation.
            return {key: value for key, value in event.items() if key != "id"}
        payload = event.get("payload")
        if isinstance(payload, Mapping) and payload.get("schema") == AGENT_MESSAGE_SCHEMA:
            return payload
        envelope = event.get("envelope")
        if isinstance(envelope, Mapping) and envelope.get("schema") == AGENT_MESSAGE_SCHEMA:
            return envelope
        return None

    @staticmethod
    def is_agent_event(event: Mapping[str, Any]) -> bool:
        return PersephoneReceiver._event_envelope(event) is not None

    def _route_disposition(self, envelope: AgentMessageEnvelope) -> str | None:
        key = (envelope.project_id, envelope.target_agent_id)
        with self._lock:
            worker = self._workers.get(key)
            binding = (
                self._bindings.get(envelope.target_workspace_binding_id)
                if envelope.target_workspace_binding_id is not None
                else None
            )
        if worker is None:
            return "receiver_route_unavailable"
        if worker.queue_supported is False:
            return "agent_queue_unsupported"
        if envelope.target_workspace_binding_id is not None and (
            binding is None
            or binding.status != "linked"
            or binding.project_id != envelope.project_id
            or binding.agent_id != envelope.target_agent_id
        ):
            return "target_binding_unavailable"
        return None

    def _duplicate_disposition(self, state: str) -> str:
        return {
            "processing": "accepted",
            "waiting_human_approval": "waiting_human_approval",
            "expired": "expired",
            "rejected": "rejected",
            "processed": "accepted",
            "responded": "accepted",
            "acknowledged": "accepted",
        }.get(state, state)

    def _response_message_id(self, request: AgentMessageEnvelope) -> str:
        if self.response_id_factory is not None:
            return self.response_id_factory()
        return uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"hades-persephone-response:{request.project_id}:{request.message_id}",
        ).hex

    def ingest_event(
        self,
        event: Mapping[str, Any],
        *,
        expected_project_id: str | None = None,
        expected_target_agent_id: str | None = None,
        expected_workspace_binding_id: str | None = None,
    ) -> str:
        """Persist, independently revalidate, classify, and advance one event."""
        if not self._queue_capability:
            return "agent_queue_unsupported"
        if not isinstance(event, Mapping):
            return "invalid_agent_message"
        raw = self._event_envelope(event)
        if raw is None:
            return "not_agent_message"
        try:
            # Historical/just-expired messages must still be durable for audit.
            envelope = parse_envelope(raw, now=0)
        except (TypeError, ValueError):
            return "invalid_agent_message"

        cursor = str(event.get("id") or event.get("cursor") or "").strip()
        timestamp = int(self._now())
        expected_project = str(expected_project_id or "").strip() or None
        expected_agent = str(expected_target_agent_id or "").strip() or None
        expected_binding = (
            str(expected_workspace_binding_id or "").strip() or None
        )
        if (expected_project is None) != (expected_agent is None):
            raise ValueError(
                "expected_project_id and expected_target_agent_id must be provided together"
            )
        cursor_project = expected_project or envelope.project_id
        cursor_agent = expected_agent or envelope.target_agent_id
        with self.connection_factory() as conn:
            stored = record_inbox(conn, envelope, now=timestamp)
            # Reparse the detached durable representation using current time.
            try:
                durable = parse_envelope(stored.envelope.to_dict(), now=timestamp)
            except ValueError as exc:
                if stored.state not in {"received", "retry"}:
                    return self._duplicate_disposition(stored.state)
                if "expired" in str(exc):
                    transition_message(conn, envelope.message_id, "expired", now=timestamp)
                    disposition = "expired"
                else:
                    transition_message(conn, envelope.message_id, "rejected", now=timestamp)
                    disposition = "invalid_agent_message"
            else:
                subscription_mismatch = bool(
                    expected_project is not None
                    and (
                        durable.project_id != expected_project
                        or durable.target_agent_id != expected_agent
                        or (
                            expected_binding is not None
                            and durable.target_workspace_binding_id != expected_binding
                        )
                    )
                )
                if subscription_mismatch:
                    record_subscription_mismatch(
                        conn,
                        durable,
                        subscription_project_id=cursor_project,
                        subscription_agent_id=cursor_agent,
                        subscription_workspace_binding_id=expected_binding,
                        cursor=cursor,
                        now=timestamp,
                    )
                    return "subscription_route_mismatch"
                if stored.state != "received":
                    # Its first delivery already advanced its own opaque
                    # cursor, except for the crash gap after a terminal commit
                    # and before cursor persistence, which is repaired here.
                    if cursor and stored.state in {
                        "responded", "acknowledged", "rejected", "expired"
                    }:
                        record_cursor(
                            conn,
                            project_id=cursor_project,
                            target_agent_id=cursor_agent,
                            cursor=cursor,
                            now=timestamp,
                        )
                    return self._duplicate_disposition(stored.state)
                route_error = self._route_disposition(durable)
                if route_error is not None:
                    # Capability absence is a deployment gate, not a policy
                    # rejection of otherwise valid untrusted data.  Keep it
                    # durable and unprocessed so a later upgraded service may
                    # re-evaluate it without manufacturing a new message ID.
                    if route_error != "agent_queue_unsupported":
                        transition_message(conn, envelope.message_id, "rejected", now=timestamp)
                    disposition = route_error
                else:
                    disposition = classify_request(durable)
                    if disposition != "accepted":
                        transition_message(
                            conn,
                            envelope.message_id,
                            "waiting_human_approval",
                            now=timestamp,
                        )
                    elif self.information_executor is not None:
                        if not claim_information_request(
                            conn,
                            envelope.message_id,
                            expected_states=(stored.state,),
                            now=timestamp,
                        ):
                            claimed = get_message(conn, envelope.message_id)
                            return self._duplicate_disposition(
                                claimed.state if claimed is not None else "retry_pending"
                            )
                        with self._lock:
                            binding = self._bindings.get(
                                durable.target_workspace_binding_id or ""
                            )
                        # _route_disposition already established the exact
                        # project/agent/workspace binding.  Never infer or
                        # fall back to another workspace here.
                        if binding is None:
                            transition_message(
                                conn, envelope.message_id, "rejected", now=timestamp
                            )
                            disposition = "target_binding_unavailable"
                        else:
                            self.information_executor(
                                conn,
                                durable.message_id,
                                binding=binding,
                                now=timestamp,
                                response_message_id=self._response_message_id(durable),
                            )
                        completed = get_message(conn, envelope.message_id)
                        if completed is None or completed.state not in {
                            "responded", "rejected", "expired"
                        }:
                            disposition = "retry_pending"
            should_record_cursor = not (
                disposition in {"agent_queue_unsupported", "retry_pending"}
                or (disposition == "accepted" and self.information_executor is None)
            )
            if cursor and should_record_cursor:
                record_cursor(
                    conn,
                    project_id=cursor_project,
                    target_agent_id=cursor_agent,
                    cursor=cursor,
                    now=timestamp,
                )
            # The cursor advances only after the durable terminal/policy state.
            return disposition


__all__ = [
    "AUTO_INFORMATION_CAPABILITIES",
    "PersephoneReceiver",
    "classify_request",
]
