"""Long-lived, process-local authority for one engineering review lifecycle.

The public Hermes process owns this service. Short-lived ``hermes-review-engine``
commands are untrusted RPC proxies: they can request deterministic operations,
but there is deliberately no RPC that can add reviewer evidence or select
executable code. The delegate callback commits evidence directly in the owner
process through :mod:`agent.review_evidence`.

The Unix socket's private directory/mode and kernel peer credentials exclude
other OS users. A process running as the Hermes user is still treated as
untrusted: the RPC surface can only request validated deterministic operations
for the pre-registered run/workspace. It cannot write evidence, choose a
bundle, recover a signing capability, or make an engine result authoritative
without the owner's later authenticated checks.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import socket
import stat
import struct
import threading
from pathlib import Path
from typing import Any, Mapping

from hermes_constants import get_hermes_home

from .bridge import EngineeringReviewBridge
from .execution_policy import ExecutionDecision, decide_execution, target_kind_for
from .protocol import MAX_TRANSPORT_BYTES, EngineRequest, EngineResponse
from .runs import Effort, ReviewRun, ReviewRunError


_AUTHORITY_VERSION = 1
_SOCKET_DIRECTORY = f"hermes-review-authority-{getattr(os, 'geteuid', lambda: 0)()}"
_LOGGER = logging.getLogger(__name__)


class ReviewAuthorityUnavailable(RuntimeError):
    """The registered long-lived authority is absent or rejected the request."""


def _authority_socket_path(session_id: str) -> Path:
    identity = f"{get_hermes_home().resolve(strict=False)}\0{session_id}".encode()
    name = hashlib.sha256(identity).hexdigest()[:32] + ".sock"
    # Darwin limits AF_UNIX paths to 103 bytes while TMPDIR is commonly much
    # longer. The uid-owned 0700 directory is the confidentiality boundary.
    return Path("/tmp") / _SOCKET_DIRECTORY / name


def _private_socket_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ReviewAuthorityUnavailable("authority socket directory is unsafe")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise ReviewAuthorityUnavailable("authority socket directory has another owner")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise ReviewAuthorityUnavailable("authority socket directory is not private")


def _peer_uid(connection: socket.socket) -> int | None:
    """Return a kernel-authenticated Unix peer uid, or fail closed."""
    if hasattr(socket, "SO_PEERCRED"):
        raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        _, uid, _ = struct.unpack("=iii", raw)
        return uid
    if hasattr(socket, "LOCAL_PEERCRED"):
        # Darwin's xucred begins with cr_version followed by cr_uid.
        raw = connection.getsockopt(0, socket.LOCAL_PEERCRED, 8)
        _, uid = struct.unpack("=II", raw)
        return uid
    return None


def _read_message(connection: socket.socket) -> dict[str, Any]:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = connection.recv(min(65536, MAX_TRANSPORT_BYTES + 2 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_TRANSPORT_BYTES + 1:
            raise ReviewAuthorityUnavailable("authority request exceeds 4 MiB")
    raw = b"".join(chunks)
    if not raw.endswith(b"\n") or b"\n" in raw[:-1]:
        raise ReviewAuthorityUnavailable("authority request must be one JSON line")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewAuthorityUnavailable("authority request is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ReviewAuthorityUnavailable("authority request must be an object")
    return value


def _send_message(connection: socket.socket, value: Mapping[str, Any]) -> None:
    data = (
        json.dumps(
            dict(value), allow_nan=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        + b"\n"
    )
    if len(data) > MAX_TRANSPORT_BYTES + 1:
        raise ReviewAuthorityUnavailable("authority response exceeds 4 MiB")
    connection.sendall(data)


class ReviewAuthority:
    """Own run capability, executable snapshot, evidence and bridge lifecycle."""

    def __init__(
        self,
        *,
        workspace: Path | str,
        target: str,
        effort: Effort,
        session_id: str,
        bundle: Path | None = None,
        execution_decision: ExecutionDecision | None = None,
    ) -> None:
        self.socket_path = _authority_socket_path(session_id)
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._socket_identity: tuple[int, int] | None = None
        self._bridge = EngineeringReviewBridge(require_authority=True)
        self._execution_decision = execution_decision or decide_execution(
            target_kind=target_kind_for(target),
            sandbox=None,
            allow_local=False,
        )
        self._engine_cleanup_failed = False
        self._capture_completed = False
        self._cleanup_completed = False
        self._closed = False
        # Run creation is deliberately last: once its capability exists there
        # are no remaining constructor steps that can fail before ownership is
        # fully established and ``start_serving`` can roll back transactionally.
        self.run = ReviewRun.create(
            workspace,
            target=target,
            effort=effort,
            session_id=session_id,
            bundle=bundle,
        )

    def __enter__(self) -> ReviewAuthority:
        self.start_serving()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def start_serving(self) -> None:
        """Publish the private proxy socket after the session-bound run exists."""
        if self._listener is not None:
            return
        if self._closed:
            raise ReviewAuthorityUnavailable("review authority is already closed")
        listener: socket.socket | None = None
        try:
            if os.name == "nt" or not hasattr(socket, "AF_UNIX"):
                raise ReviewAuthorityUnavailable(
                    "review authority peer authentication is unavailable on this platform"
                )
            _private_socket_directory(self.socket_path.parent)
            if self.socket_path.exists() or self.socket_path.is_symlink():
                raise ReviewAuthorityUnavailable(
                    "a review authority is already registered"
                )
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
            info = self.socket_path.lstat()
            self._socket_identity = (info.st_dev, info.st_ino)
            listener.listen(8)
            listener.settimeout(0.1)
            self._listener = listener
            self._thread = threading.Thread(
                target=self._serve,
                name=f"review-authority-{self.run.run_id}",
                daemon=True,
            )
            self._thread.start()
        except BaseException as exc:
            self._rollback_start(listener, exc)
            raise

    def _remove_owned_socket(self) -> bool:
        """Remove only the endpoint inode this owner published."""
        try:
            info = self.socket_path.lstat()
            if self._socket_identity != (info.st_dev, info.st_ino):
                return False
            self.socket_path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False
        finally:
            self._socket_identity = None

    def _record_terminal_state(self, *, cleanup_failed: bool, cause: object) -> None:
        """Persist status best-effort, but always revoke in-memory authority."""
        try:
            if cleanup_failed:
                self.run = self.run.mark_cleanup_failed()
            else:
                self.run = self.run.mark_complete()
        except BaseException:
            _LOGGER.warning(
                "could not persist review authority terminal state (%s)",
                cause,
                exc_info=True,
            )
        finally:
            self.run.revoke_authority_capability()

    def _rollback_start(
        self, listener: socket.socket | None, cause: BaseException
    ) -> None:
        """Undo every partially published resource after startup failure."""
        self._closed = True
        self._stop.set()
        rollback_cause: object = cause
        try:
            if listener is not None:
                try:
                    listener.close()
                except OSError as exc:
                    rollback_cause = f"{cause}; listener close failed: {exc}"
            self._listener = None
            self._thread = None
            if not self._remove_owned_socket():
                rollback_cause = f"{rollback_cause}; socket cleanup failed"
        finally:
            self._record_terminal_state(
                cleanup_failed=True,
                cause=rollback_cause,
            )

    def _serve(self) -> None:
        listener = self._listener
        assert listener is not None
        while not self._stop.is_set():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with connection:
                try:
                    connection.settimeout(1)
                    peer_uid = _peer_uid(connection)
                    if peer_uid is None or peer_uid != os.geteuid():
                        raise ReviewAuthorityUnavailable(
                            "authority client ownership could not be verified"
                        )
                    response = self._dispatch(_read_message(connection))
                    _send_message(connection, {"ok": True, "value": response})
                except Exception as exc:
                    try:
                        _send_message(connection, {"ok": False, "error": str(exc)})
                    except Exception:
                        pass

    def _dispatch(self, message: Mapping[str, Any]) -> dict[str, Any]:
        if message.get("version") != _AUTHORITY_VERSION:
            raise ReviewAuthorityUnavailable("unknown authority protocol version")
        action = message.get("action")
        if action == "start" and set(message) == {"version", "action"}:
            return {
                "runId": self.run.run_id,
                "planPath": str(self.run.root / "plan.json"),
            }
        if action == "invoke" and set(message) == {
            "version",
            "action",
            "request",
            "timeout",
        }:
            timeout = message["timeout"]
            request = EngineRequest.from_wire(message["request"])
            if request.artifact_root.resolve(strict=False) != self.run.root:
                raise ReviewAuthorityUnavailable(
                    "artifactRoot does not match the registered run"
                )
            if request.workspace.resolve(strict=False) != self.run.workspace:
                raise ReviewAuthorityUnavailable(
                    "workspace does not match the registered run"
                )
            if request.command in {"build-test", "test-efficacy"}:
                # The proxy/model cannot choose or weaken executable-code
                # policy.  Replace any caller value with the authority-owned
                # decision immediately before invoking the captured bundle.
                trusted_input = dict(request.input)
                trusted_input["execution"] = self._execution_decision.to_wire()
                request = EngineRequest(
                    request_id=request.request_id,
                    command=request.command,
                    workspace=request.workspace,
                    artifact_root=request.artifact_root,
                    input=trusted_input,
                )
            response = self._bridge.invoke(
                request, timeout=timeout, cancel_event=self._stop
            )
            if request.command == "capture-target" and isinstance(
                response.output.get("planPath"), str
            ):
                self._capture_completed = True
            if request.command == "cleanup":
                self._engine_cleanup_failed = response.status != "passed"
                self._cleanup_completed = response.status == "passed"
            elif any(
                diagnostic.code == "cleanup_failed"
                for diagnostic in response.diagnostics
            ):
                self._engine_cleanup_failed = True
            return response.to_wire()
        if action == "cleanup" and set(message) == {
            "version",
            "action",
            "runId",
            "timeout",
        }:
            if message["runId"] != self.run.run_id:
                raise ReviewAuthorityUnavailable(
                    "cleanup run does not match the registered run"
                )
            timeout = message["timeout"]
            request = EngineRequest(
                request_id=f"cleanup-{secrets.token_hex(12)}",
                command="cleanup",
                workspace=self.run.workspace,
                artifact_root=self.run.root,
                input={"runId": self.run.run_id},
            )
            response = self._bridge.invoke(
                request, timeout=timeout, cancel_event=self._stop
            )
            self._engine_cleanup_failed = response.status != "passed"
            self._cleanup_completed = response.status == "passed"
            return response.to_wire()
        raise ReviewAuthorityUnavailable("authority action or fields are invalid")

    def close(self) -> None:
        """Stop proxy access, remove the socket, and destroy run capabilities."""
        if self._closed:
            self.run.revoke_authority_capability()
            return
        cleanup_failures: list[str] = []
        try:
            self._closed = True
            listener = self._listener
            self._stop.set()
            if listener is not None:
                try:
                    listener.close()
                except OSError as exc:
                    cleanup_failures.append(f"listener close failed: {exc}")
            self._listener = None
            if self._thread is not None:
                try:
                    self._thread.join(timeout=5)
                    if self._thread.is_alive():
                        cleanup_failures.append("active work did not stop")
                except RuntimeError as exc:
                    cleanup_failures.append(f"authority thread cleanup failed: {exc}")
                self._thread = None
            if self._capture_completed and not self._cleanup_completed:
                request = EngineRequest(
                    request_id=f"cleanup-{secrets.token_hex(12)}",
                    command="cleanup",
                    workspace=self.run.workspace,
                    artifact_root=self.run.root,
                    input={"runId": self.run.run_id},
                )
                try:
                    response = self._bridge.invoke(request, timeout=60)
                    self._cleanup_completed = response.status == "passed"
                    self._engine_cleanup_failed = not self._cleanup_completed
                    if not self._cleanup_completed:
                        _LOGGER.warning(
                            "engineering review cleanup failed; recovery: "
                            "hermes-review-engine cleanup --run %s",
                            self.run.run_id,
                        )
                except BaseException as exc:
                    self._engine_cleanup_failed = True
                    cleanup_failures.append(
                        f"registered worktree cleanup failed: {exc}"
                    )
                    _LOGGER.warning(
                        "engineering review cleanup failed; recovery: "
                        "hermes-review-engine cleanup --run %s",
                        self.run.run_id,
                    )
            if not self._remove_owned_socket():
                cleanup_failures.append("authority socket cleanup failed")
        except BaseException as exc:
            cleanup_failures.append(f"unexpected authority cleanup failure: {exc}")
            raise
        finally:
            self._record_terminal_state(
                cleanup_failed=bool(cleanup_failures)
                or self._engine_cleanup_failed,
                cause="; ".join(cleanup_failures) or "normal close",
            )


class ReviewAuthorityClient:
    """Untrusted local proxy client; it cannot write evidence or select code."""

    def __init__(self, session_id: str) -> None:
        self.socket_path = _authority_socket_path(session_id)

    def _request(self, message: Mapping[str, Any]) -> dict[str, Any]:
        try:
            _private_socket_directory(self.socket_path.parent)
            info = self.socket_path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise ReviewAuthorityUnavailable("authority endpoint is unsafe")
            if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
                raise ReviewAuthorityUnavailable("authority endpoint has another owner")
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise ReviewAuthorityUnavailable("authority endpoint is not private")
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            with connection:
                connection.settimeout(660)
                connection.connect(str(self.socket_path))
                _send_message(connection, message)
                connection.shutdown(socket.SHUT_WR)
                response = _read_message(connection)
        except (OSError, ReviewAuthorityUnavailable) as exc:
            if isinstance(exc, ReviewAuthorityUnavailable):
                raise
            raise ReviewAuthorityUnavailable(
                "live review authority is unavailable"
            ) from exc
        if response.get("ok") is not True or not isinstance(
            response.get("value"), dict
        ):
            message_text = response.get("error")
            detail = (
                message_text if isinstance(message_text, str) else "request rejected"
            )
            raise ReviewAuthorityUnavailable(detail)
        return response["value"]

    def start(self) -> dict[str, Any]:
        return self._request({"version": _AUTHORITY_VERSION, "action": "start"})

    def invoke(self, request: EngineRequest, *, timeout: float) -> EngineResponse:
        value = self._request({
            "version": _AUTHORITY_VERSION,
            "action": "invoke",
            "request": request.to_wire(),
            "timeout": timeout,
        })
        return EngineResponse.from_wire(value, expected_request_id=request.request_id)

    def cleanup(self, run_id: str, *, timeout: float) -> EngineResponse:
        value = self._request({
            "version": _AUTHORITY_VERSION,
            "action": "cleanup",
            "runId": run_id,
            "timeout": timeout,
        })
        request_id = value.get("requestId")
        if not isinstance(request_id, str):
            raise ReviewAuthorityUnavailable("cleanup response has no request identity")
        return EngineResponse.from_wire(value, expected_request_id=request_id)
