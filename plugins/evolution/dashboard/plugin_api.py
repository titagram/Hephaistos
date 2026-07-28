"""Authenticated, local-only HTTP adapter for Evolution dashboard operations."""

from __future__ import annotations

import re
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Literal, TypeVar

from fastapi import APIRouter, FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hermes_cli.evolution.dashboard_confirmations import DashboardConfirmationStore
from hermes_cli.evolution.dashboard_jobs import (
    EvolutionJobError,
    EvolutionJobManager,
)
from hermes_cli.evolution.dashboard_service import (
    EvolutionDashboardConflict,
    EvolutionDashboardError,
    EvolutionDashboardService,
)


@asynccontextmanager
async def _lifespan(_: FastAPI):
    try:
        yield
    finally:
        shutdown_for_tests()


router = APIRouter(lifespan=_lifespan)

_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.ASCII,
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$", re.ASCII)
_MAX_DOCUMENT_BYTES = 32 * 1024
_UNAVAILABLE_CODES = frozenset(
    {
        "evolution_unavailable",
        "job_unavailable",
        "gnothi_unavailable",
        "telos_unavailable",
        "suggestion_unavailable",
        "blueprint_unavailable",
        "lifecycle_unavailable",
    }
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CollectorName(str, Enum):
    source = "source"
    capabilities = "capabilities"
    runtime = "runtime"
    contracts = "contracts"
    dependencies = "dependencies"
    experience = "experience"


class InitializeRequest(_StrictModel):
    """Intentionally empty: initialization has no client-selected target."""


class MutationContext(_StrictModel):
    organism_id: str = Field(pattern=_UUID.pattern)
    expected_snapshot_digest: str = Field(pattern=_DIGEST.pattern)


class RebuildRequest(MutationContext):
    force: bool
    collectors: list[CollectorName] = Field(max_length=6)


class ObserverToggleRequest(MutationContext):
    enabled: bool


class TelosDraftRequest(MutationContext):
    document: dict[str, Any]


class TelosPrepareRequest(MutationContext):
    current_digest: str = Field(pattern=_DIGEST.pattern)
    target_digest: str = Field(pattern=_DIGEST.pattern)
    action: Literal["activate", "rollback"]


class TelosConfirmRequest(TelosPrepareRequest):
    confirmation_id: str = Field(pattern=_UUID.pattern)
    phrase: str = Field(min_length=1, max_length=256)


class BlueprintRequest(MutationContext):
    expected_suggestion_digest: str = Field(pattern=_DIGEST.pattern)


_Model = TypeVar("_Model", bound=_StrictModel)
_state_lock = threading.RLock()
_local_root: Path | None = None
_manager: EvolutionJobManager | None = None
_manager_root: Path | None = None
_confirmations: DashboardConfirmationStore | None = None
_confirmations_root: Path | None = None


def set_local_root_for_tests(root: Path) -> None:
    """Install an isolated process-local root for the bare-router test harness."""
    global _local_root
    with _state_lock:
        _close_manager_locked()
        _local_root = Path(root)
        _reset_confirmations_locked()


def local_root_for_tests() -> Path:
    if _local_root is None:
        raise RuntimeError("no isolated local root")
    return _local_root


def shutdown_for_tests() -> None:
    """Release test-owned worker threads; production uses the router shutdown hook."""
    with _state_lock:
        _close_manager_locked()
        _reset_confirmations_locked()


def _root() -> Path | None:
    return _local_root


def _service(*, with_jobs: bool = False) -> EvolutionDashboardService:
    root = _root()
    with _state_lock:
        manager = _manager_for_root_locked(root) if with_jobs else _manager
    return EvolutionDashboardService(root, job_manager=manager)


def _manager_for_root_locked(root: Path | None) -> EvolutionJobManager | None:
    global _manager, _manager_root
    if root is None:
        return _manager
    if _manager is not None and _manager_root == root:
        return _manager
    _close_manager_locked()
    if not root.is_dir():
        return None
    _manager = EvolutionJobManager(root)
    _manager_root = root
    return _manager


def _job_manager() -> EvolutionJobManager | None:
    with _state_lock:
        return _manager_for_root_locked(_root())


def _confirmation_store() -> DashboardConfirmationStore:
    global _confirmations, _confirmations_root
    root = _root()
    with _state_lock:
        if _confirmations is None or _confirmations_root != root:
            _confirmations = DashboardConfirmationStore(root)
            _confirmations_root = root
        return _confirmations


def _close_manager_locked() -> None:
    global _manager, _manager_root
    if _manager is not None:
        _manager.shutdown()
    _manager = None
    _manager_root = None


def _reset_confirmations_locked() -> None:
    global _confirmations, _confirmations_root
    _confirmations = None
    _confirmations_root = None


class _PublicAPIError(Exception):
    def __init__(self, code: str, http_status: int) -> None:
        self.code = code
        self.http_status = http_status


def _error(code: str, http_status: int) -> _PublicAPIError:
    return _PublicAPIError(code, http_status)


def _error_response(error: _PublicAPIError) -> JSONResponse:
    return JSONResponse(status_code=error.http_status, content={"code": error.code})


def _public_route(function: Callable[..., Any]) -> Callable[..., Any]:
    """Normalize all adapter failures without leaking framework exception text."""
    if function.__code__.co_flags & 0x80:  # coroutine function
        @wraps(function)
        async def async_wrapped(*args: Any, **kwargs: Any) -> Any:
            try:
                return await function(*args, **kwargs)
            except _PublicAPIError as exc:
                return _error_response(exc)
            except Exception as exc:
                try:
                    _raise_public_error(exc)
                except _PublicAPIError as public:
                    return _error_response(public)
                raise
        return async_wrapped

    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except _PublicAPIError as exc:
            return _error_response(exc)
        except Exception as exc:
            try:
                _raise_public_error(exc)
            except _PublicAPIError as public:
                return _error_response(public)
            raise
    return wrapped


def _raise_public_error(exc: Exception) -> None:
    if isinstance(exc, _PublicAPIError):
        raise exc
    if isinstance(exc, EvolutionDashboardConflict):
        raise _error(exc.code, status.HTTP_409_CONFLICT) from None
    if isinstance(exc, EvolutionDashboardError):
        status_code = (
            status.HTTP_500_INTERNAL_SERVER_ERROR
            if exc.code in _UNAVAILABLE_CODES
            else status.HTTP_400_BAD_REQUEST
        )
        raise _error(exc.code, status_code) from None
    if isinstance(exc, EvolutionJobError):
        raise _error(exc.code, status.HTTP_400_BAD_REQUEST) from None
    if isinstance(exc, ValueError):
        raise _error("invalid_request", status.HTTP_400_BAD_REQUEST) from None
    raise _error("evolution_unavailable", status.HTTP_500_INTERNAL_SERVER_ERROR) from None


async def _body(request: Request, model: type[_Model]) -> _Model:
    try:
        raw = await request.body()
        payload = {} if not raw else await request.json()
        return model.model_validate(payload)
    except (ValidationError, ValueError, TypeError):
        raise _error("invalid_request", status.HTTP_422_UNPROCESSABLE_CONTENT) from None


def _document_is_bounded(document: dict[str, Any]) -> bool:
    forbidden_keys = frozenset({"path", "url", "command", "actor", "actor_id", "session", "session_id"})

    def safe(value: Any) -> bool:
        if isinstance(value, dict):
            return all(
                isinstance(key, str)
                and key.lower() not in forbidden_keys
                and safe(item)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return all(safe(item) for item in value)
        return not isinstance(value, str) or "://" not in value

    try:
        import json

        return safe(document) and len(
            json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode()
        ) <= _MAX_DOCUMENT_BYTES
    except (TypeError, ValueError):
        return False


def _query_int(request: Request, name: str, *, default: int, minimum: int, maximum: int) -> int:
    values = request.query_params.getlist(name)
    if not values:
        return default
    if len(values) != 1 or not values[0].isdigit():
        raise _error("invalid_request", status.HTTP_422_UNPROCESSABLE_CONTENT)
    value = int(values[0])
    if not minimum <= value <= maximum:
        raise _error("invalid_request", status.HTTP_422_UNPROCESSABLE_CONTENT)
    return value


def _query_identifier(request: Request, name: str, *, required: bool = False) -> str | None:
    values = request.query_params.getlist(name)
    if not values:
        if required:
            raise _error("invalid_request", status.HTTP_422_UNPROCESSABLE_CONTENT)
        return None
    if len(values) != 1 or not _IDENTIFIER.fullmatch(values[0]):
        raise _error("invalid_request", status.HTTP_422_UNPROCESSABLE_CONTENT)
    return values[0]


def _query_text(request: Request, name: str, *, maximum: int = 128) -> str:
    values = request.query_params.getlist(name)
    if not values:
        return ""
    if len(values) != 1 or len(values[0]) > maximum:
        raise _error("invalid_request", status.HTTP_422_UNPROCESSABLE_CONTENT)
    return values[0]


def _job_payload(job: Any) -> dict[str, Any]:
    return asdict(job)


@router.get("/snapshot")
@_public_route
def get_snapshot() -> dict[str, Any]:
    try:
        return _service().snapshot()
    except Exception as exc:
        _raise_public_error(exc)


@router.get("/mutation-context")
@_public_route
def get_mutation_context() -> dict[str, str]:
    try:
        return _service().mutation_context()
    except Exception as exc:
        _raise_public_error(exc)


@router.get("/graph")
@_public_route
def get_graph(request: Request) -> dict[str, Any]:
    try:
        depth = _query_int(request, "depth", default=2, minimum=0, maximum=4)
        limit = _query_int(request, "limit", default=200, minimum=1, maximum=200)
        root_id = _query_identifier(request, "root_id")
        expected_revision = _query_identifier(request, "expected_revision")
        search = _query_text(request, "search", maximum=128)
        kinds = request.query_params.getlist("kind")
        allowed_kinds = frozenset({"capability", "component", "contract", "dependency", "evidence", "file", "function", "module", "runtime", "service", "source"})
        if len(kinds) > 11 or any(kind not in allowed_kinds for kind in kinds):
            raise _error("invalid_request", status.HTTP_422_UNPROCESSABLE_CONTENT)
        return _service().graph(
            root_id=root_id,
            depth=depth,
            limit=limit,
            kinds=frozenset(kinds),
            search=search,
            expected_revision=expected_revision,
        )
    except Exception as exc:
        _raise_public_error(exc)


@router.get("/revisions")
@_public_route
def get_revisions(request: Request) -> dict[str, Any]:
    try:
        return _service().revisions(_query_int(request, "limit", default=50, minimum=1, maximum=50))
    except Exception as exc:
        _raise_public_error(exc)


@router.get("/diff")
@_public_route
def get_diff(request: Request) -> dict[str, Any]:
    try:
        left = _query_identifier(request, "left", required=True)
        right = _query_identifier(request, "right", required=True)
        assert left is not None and right is not None
        return _service().revision_diff(left, right)
    except Exception as exc:
        _raise_public_error(exc)


@router.get("/telos")
@_public_route
def get_telos(request: Request) -> dict[str, Any]:
    try:
        return _service().telos(history_limit=_query_int(request, "history_limit", default=50, minimum=1, maximum=50))
    except Exception as exc:
        _raise_public_error(exc)


@router.get("/pipeline")
@_public_route
def get_pipeline(request: Request) -> dict[str, Any]:
    try:
        return _service().pipeline(
            attempt_id=_query_identifier(request, "attempt_id"),
            limit=_query_int(request, "limit", default=50, minimum=1, maximum=50),
        )
    except Exception as exc:
        _raise_public_error(exc)


@router.get("/audit")
@_public_route
def get_audit(request: Request) -> dict[str, Any]:
    try:
        return _service().audit(
            after=_query_int(request, "after", default=0, minimum=0, maximum=1_000_000_000),
            limit=_query_int(request, "limit", default=100, minimum=1, maximum=100),
        )
    except Exception as exc:
        _raise_public_error(exc)


@router.get("/jobs/{job_id}")
@_public_route
def get_job(job_id: str) -> dict[str, Any]:
    if not _UUID.fullmatch(job_id):
        raise _error("invalid_request", status.HTTP_422_UNPROCESSABLE_CONTENT)
    try:
        manager = _job_manager()
        if manager is None:
            raise _error("job_not_found", status.HTTP_404_NOT_FOUND)
        job = manager.get_job(job_id)
        if job is None:
            raise _error("job_not_found", status.HTTP_404_NOT_FOUND)
        return _job_payload(job)
    except Exception as exc:
        _raise_public_error(exc)


@router.post("/initialize")
@_public_route
async def post_initialize(request: Request) -> dict[str, Any]:
    await _body(request, InitializeRequest)
    try:
        return _service().initialize()
    except Exception as exc:
        _raise_public_error(exc)


@router.post("/jobs/organism-rebuild", status_code=status.HTTP_202_ACCEPTED)
@_public_route
async def post_rebuild(request: Request) -> dict[str, Any]:
    body = await _body(request, RebuildRequest)
    try:
        job = _service(with_jobs=True).submit_rebuild(
            organism_id=body.organism_id,
            expected_snapshot_digest=body.expected_snapshot_digest,
            force=body.force,
            collectors=[collector.value for collector in body.collectors],
        )
        return _job_payload(job)
    except Exception as exc:
        _raise_public_error(exc)


@router.post("/jobs/observer-scan", status_code=status.HTTP_202_ACCEPTED)
@_public_route
async def post_observer_scan(request: Request) -> dict[str, Any]:
    body = await _body(request, MutationContext)
    try:
        return _job_payload(
            _service(with_jobs=True).submit_observer_scan(**body.model_dump())
        )
    except Exception as exc:
        _raise_public_error(exc)


@router.post("/observer")
@_public_route
async def post_observer(request: Request) -> dict[str, Any]:
    body = await _body(request, ObserverToggleRequest)
    try:
        return _service().set_observer_enabled(**body.model_dump())
    except Exception as exc:
        _raise_public_error(exc)


@router.post("/telos/drafts")
@_public_route
async def post_telos_draft(request: Request) -> dict[str, Any]:
    body = await _body(request, TelosDraftRequest)
    if not _document_is_bounded(body.document):
        raise _error("invalid_request", status.HTTP_422_UNPROCESSABLE_CONTENT)
    try:
        return _service().save_telos_draft(**body.model_dump())
    except Exception as exc:
        _raise_public_error(exc)


@router.post("/telos/transitions/prepare")
@_public_route
async def post_telos_prepare(request: Request) -> dict[str, Any]:
    body = await _body(request, TelosPrepareRequest)
    try:
        return _confirmation_store().prepare(**body.model_dump())
    except Exception as exc:
        _raise_public_error(exc)


@router.post("/telos/transitions/confirm")
@_public_route
async def post_telos_confirm(request: Request) -> dict[str, Any]:
    body = await _body(request, TelosConfirmRequest)
    try:
        return _confirmation_store().confirm(**body.model_dump())
    except Exception as exc:
        _raise_public_error(exc)


@router.post("/suggestions/{suggestion_id}/blueprint")
@_public_route
async def post_blueprint(suggestion_id: str, request: Request) -> dict[str, Any]:
    if not _UUID.fullmatch(suggestion_id):
        raise _error("invalid_request", status.HTTP_422_UNPROCESSABLE_CONTENT)
    body = await _body(request, BlueprintRequest)
    try:
        return _service().create_blueprint(
            **body.model_dump(), suggestion_id=suggestion_id
        )
    except Exception as exc:
        _raise_public_error(exc)
