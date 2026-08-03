"""Reusable Hades backend sync runner."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import gzip
import hashlib
import logging
from pathlib import Path
import re
import time
from typing import Callable

from hermes_cli import hades_backend_db as db
from hermes_cli.hades_artifact_hash import (
    artifact_payload_hash as _artifact_payload_hash,
    canonical_artifact_bytes,
)

logger = logging.getLogger("hermes_cli.hades_backend")


@dataclass(frozen=True)
class SyncResult:
    summary: dict[str, object]
    exit_code: int


ARTIFACT_UPLOAD_CACHE_PREFIX = "artifact_upload_cache"
ARTIFACT_COMPRESSION_MIN_BYTES = 64 * 1024
GRAPH_V2_UPLOAD_CACHE_PREFIX = "graph_v2_upload_cache"
GRAPH_V2_ACTIVE_CACHE_PREFIX = "graph_v2_active"
GRAPH_IMPORT_POLL_TIMEOUT_SECONDS = 180.0
GRAPH_IMPORT_POLL_INTERVAL_SECONDS = 2.0


def _credential_fingerprint(secret: str) -> str | None:
    value = str(secret or "")
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None


def _persisted_credential_fingerprint(agent: db.BackendAgent) -> str | None:
    from hermes_cli.config import load_env

    return _credential_fingerprint(load_env().get(agent.token_env_key, ""))


def _graph_v2_cache_key(
    binding: db.WorkspaceBinding,
    source_identity: dict[str, object],
    artifact_graph_version: str,
) -> str:
    source_digest = hashlib.sha256(
        canonical_artifact_bytes(source_identity)
    ).hexdigest()
    return ":".join((
        GRAPH_V2_UPLOAD_CACHE_PREFIX,
        binding.project_id,
        binding.backend_workspace_binding_id,
        source_digest,
        artifact_graph_version,
    ))


def _graph_v2_active_cache_key(binding: db.WorkspaceBinding) -> str:
    return ":".join((
        GRAPH_V2_ACTIVE_CACHE_PREFIX,
        binding.project_id,
        binding.backend_workspace_binding_id,
    ))


def _graph_v2_spool(
    binding: db.WorkspaceBinding, artifact_graph_version: str
) -> Path:
    from hermes_constants import get_hermes_home

    return (
        get_hermes_home()
        / "cache"
        / "hades"
        / "graph-imports"
        / binding.project_id
        / binding.backend_workspace_binding_id
        / artifact_graph_version
    )


def _verification_summary_counts(value: object) -> dict[str, object] | None:
    """Keep only fail-open aggregate counts; never cache verification payloads."""

    if not isinstance(value, dict):
        return None

    def count(key: str) -> int | None:
        raw = value.get(key)
        return raw if type(raw) is int and 0 <= raw <= 1_000_000 else None

    queued = count("verification_queued")
    high_priority = count("verification_high_priority")
    raw_domains = value.get("verification_by_domain")
    if queued is None or high_priority is None or not isinstance(raw_domains, dict):
        return None
    domains = {
        str(key): raw
        for key, raw in raw_domains.items()
        if isinstance(key, str)
        and key in {"graph", "wiki"}
        and type(raw) is int
        and 0 <= raw <= 1_000_000
    }
    if len(domains) != len(raw_domains):
        return None
    return {
        "verification_queued": queued,
        "verification_high_priority": high_priority,
        "verification_by_domain": dict(sorted(domains.items())),
    }


def _fetch_graph_verification_summary(
    client: object,
    binding: db.WorkspaceBinding,
    projection_version: str,
) -> dict[str, object] | None:
    from hermes_cli.hades_backend_client import redact_secret

    try:
        value = client.graph_verification_summary(
            project_id=binding.project_id,
            workspace_binding_id=binding.backend_workspace_binding_id,
            projection_version=projection_version,
        )
        summary = _verification_summary_counts(value)
        if summary is None:
            raise ValueError("backend verification summary is malformed")
        return summary
    except Exception as exc:
        logger.info(
            "hades_backend.graph_v2.verification_summary_unavailable",
            extra={
                "hades_event": "graph_v2.verification_summary_unavailable",
                "hades_project_id": binding.project_id,
                "hades_workspace_binding_id": binding.backend_workspace_binding_id,
                "hades_error": redact_secret(str(exc)),
            },
        )
        return None


def run_backend_sync(
    *,
    client_factory: Callable[[], object] | None = None,
    now: int | None = None,
    quiet: bool = False,
    project_id: str | None = None,
    workspace_binding_ids: list[str] | tuple[str, ...] | None = None,
) -> SyncResult:
    """Explicitly synchronize project artifacts for linked workspaces."""

    from hermes_cli import hades_backend_runtime as runtime
    from hermes_cli.hades_backend_client import redact_secret
    from hermes_cli.hades_backend_jobs import execute_job

    sync_time = now
    started_monotonic = time.monotonic()
    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)
        bindings = db.list_workspace_bindings(conn, status="linked") if agent else []
        bindings = _filter_sync_bindings(
            bindings,
            project_id=project_id,
            workspace_binding_ids=workspace_binding_ids,
        )
        agents = {
            binding.agent_id: loaded
            for binding in bindings
            if (loaded := db.get_agent(conn, binding.agent_id)) is not None
        }

    if agent is None:
        logger.info(
            "hades_backend.sync.skipped",
            extra={"hades_event": "sync.skipped", "hades_reason": "not_configured"},
        )
        return SyncResult({"error": 1}, 1)

    empty_summary: dict[str, object] = {
        "artifacts_uploaded": 0,
        "artifacts_skipped": 0,
        "artifact_errors": 0,
        "source_slice_candidates": 0,
        "auth_failed_routes": 0,
        "auth_quarantined_routes": 0,
        "stale_auth_routes": 0,
        "duration_ms": max(
            0, int((time.monotonic() - started_monotonic) * 1000)
        ),
    }
    if not bindings:
        logger.info(
            "hades_backend.sync.skipped",
            extra={
                "hades_event": "sync.skipped",
                "hades_reason": "no_linked_workspace",
                "hades_agent_id": agent.agent_id,
                "hades_project_id": agent.project_id,
            },
        )
        with db.connect_closing() as conn:
            db.record_sync_state(conn, "last_sync_summary", empty_summary)
        return SyncResult(empty_summary, 0)

    logger.info(
        "hades_backend.sync.start",
        extra={
            "hades_event": "sync.start",
            "hades_agent_id": agent.agent_id,
            "hades_project_id": agent.project_id,
            "hades_binding_count": len(bindings),
        },
    )
    clients: dict[str, object] = {}
    created_clients: list[object] = []
    route_auth: dict[tuple[str, str], dict[str, bool | int]] = {}
    used_credential_fingerprints: dict[str, str | None] = {}

    def client_for_agent(sync_agent: db.BackendAgent) -> object:
        if sync_agent.agent_id not in used_credential_fingerprints:
            used_credential_fingerprints[sync_agent.agent_id] = (
                _credential_fingerprint(runtime.agent_token(sync_agent))
            )
        if client_factory is not None:
            created = client_factory()
            created_clients.append(created)
            return created
        existing = clients.get(sync_agent.agent_id)
        if existing is not None:
            return existing
        if (
            sync_agent.agent_id == agent.agent_id
            and sync_agent.project_id == agent.project_id
        ):
            created = runtime.client_from_config()
        else:
            created = runtime.client_for_agent(sync_agent)
        clients[sync_agent.agent_id] = created
        created_clients.append(created)
        return created

    artifacts_uploaded = 0
    artifact_errors = 0
    artifacts_skipped = 0
    source_slice_candidates = 0
    sync_errors = 0

    try:
        for binding in bindings:
            binding_agent = agents.get(binding.agent_id)
            if binding_agent is None:
                sync_errors += 1
                _record_sync_error(
                    binding,
                    f"missing local backend agent for {binding.agent_id}",
                )
                continue

            route_key = (binding.project_id, binding_agent.agent_id)
            auth_observation = route_auth.setdefault(
                route_key,
                {
                    "success": False,
                    "unauthorized": False,
                    "unauthorized_errors": 0,
                },
            )
            try:
                client = client_for_agent(binding_agent)
            except Exception as exc:
                if _is_unauthorized_error(exc):
                    auth_observation["unauthorized"] = True
                    auth_observation["unauthorized_errors"] += 1
                sync_errors += 1
                _record_sync_error(binding, str(exc))
                if not quiet:
                    print(
                        "backend sync: failed to configure client for "
                        f"{binding.display_path}: {redact_secret(str(exc))}"
                    )
                continue

            try:
                binding, metadata_refreshed = _refresh_workspace_binding_metadata(
                    client,
                    binding_agent,
                    binding,
                )
                if metadata_refreshed:
                    auth_observation["success"] = True
                (
                    baseline_uploaded,
                    baseline_failed,
                    baseline_skipped,
                    baseline_candidates,
                ) = _sync_baseline_artifacts(
                    client,
                    binding_agent,
                    binding,
                    execute_job=execute_job,
                )
                artifacts_uploaded += baseline_uploaded
                artifact_errors += baseline_failed
                artifacts_skipped += baseline_skipped
                source_slice_candidates += baseline_candidates
                sync_errors += baseline_failed
                if baseline_uploaded or baseline_skipped:
                    auth_observation["success"] = True
            except Exception as exc:
                if _is_unauthorized_error(exc):
                    auth_observation["unauthorized"] = True
                    auth_observation["unauthorized_errors"] += 1
                sync_errors += 1
                _record_sync_error(binding, str(exc))
                if not quiet:
                    print(
                        "backend sync: failed to upload baseline artifacts for "
                        f"{binding.display_path}: {redact_secret(str(exc))}"
                    )

        auth_failed_routes = 0
        auth_quarantined_routes = 0
        stale_auth_routes = 0
        with db.connect_closing() as conn:
            for (
                route_project_id,
                route_agent_id,
            ), observation in route_auth.items():
                unauthorized_cycle = bool(
                    observation["unauthorized"] and not observation["success"]
                )
                route_agent = agents.get(route_agent_id)
                used_fingerprint = used_credential_fingerprints.get(route_agent_id)
                persisted_fingerprint = (
                    _persisted_credential_fingerprint(route_agent)
                    if route_agent is not None
                    else None
                )
                stale_credential = bool(
                    unauthorized_cycle
                    and used_fingerprint
                    and persisted_fingerprint
                    and used_fingerprint != persisted_fingerprint
                )
                if stale_credential:
                    stale_auth_routes += 1
                    sync_errors = max(
                        0,
                        sync_errors - int(observation["unauthorized_errors"]),
                    )
                    continue
                if unauthorized_cycle:
                    auth_failed_routes += 1
                elif not observation["success"]:
                    continue
                outcome = db.record_route_auth_cycle(
                    conn,
                    project_id=route_project_id,
                    agent_id=route_agent_id,
                    unauthorized=unauthorized_cycle,
                    now=sync_time,
                )
                if outcome["quarantined"]:
                    auth_quarantined_routes += 1

        summary = {
            "artifacts_uploaded": artifacts_uploaded,
            "artifacts_skipped": artifacts_skipped,
            "artifact_errors": artifact_errors,
            "source_slice_candidates": source_slice_candidates,
            "auth_failed_routes": auth_failed_routes,
            "auth_quarantined_routes": auth_quarantined_routes,
            "stale_auth_routes": stale_auth_routes,
            "duration_ms": max(
                0, int((time.monotonic() - started_monotonic) * 1000)
            ),
        }
        with db.connect_closing() as conn:
            db.record_sync_state(conn, "last_sync_summary", summary)
            if sync_errors == 0:
                db.clear_sync_state(conn, "last_sync_error")

        logger.info(
            "hades_backend.sync.complete",
            extra={
                "hades_event": "sync.complete",
                "hades_agent_id": agent.agent_id,
                "hades_project_id": agent.project_id,
                "hades_exit_code": 1 if sync_errors else 0,
                "hades_summary": summary,
            },
        )
        return SyncResult(summary, 1 if sync_errors else 0)
    finally:
        closed: set[int] = set()
        for client in created_clients:
            identity = id(client)
            if identity in closed:
                continue
            closed.add(identity)
            close = getattr(client, "close", None)
            if callable(close):
                close()


def _sync_baseline_artifacts(
    client: object,
    agent: db.BackendAgent,
    binding: db.WorkspaceBinding,
    *,
    execute_job: Callable[..., dict[str, object]],
) -> tuple[int, int, int, int]:
    uploaded = failed = skipped = source_slice_candidates = 0
    head_commit = str(binding.head_commit or "").strip()
    for capability in ("sync_git_tree", "populate_backend_ast"):
        if not _agent_has_capability(agent, capability):
            continue
        payload: dict[str, object] = {
            "project_id": binding.project_id,
            "workspace_binding_id": binding.backend_workspace_binding_id,
            "head_commit": head_commit,
            "workspace_head_commit": head_commit,
            "max_source_slice_candidates": 25,
        }
        result = execute_job(
            {"job_id": None, "capability": capability, "payload": payload},
            workspace_root=binding.repo_root,
        )
        final_status = str(result.get("status") or "completed")
        if final_status != "completed":
            failed += 1
            continue
        candidates = result.get("source_slice_candidates") if isinstance(result, dict) else None
        if isinstance(candidates, list):
            source_slice_candidates += len(candidates)
        artifact_uploaded, artifact_failed, artifact_skipped = _upload_job_artifact(
            client,
            agent,
            binding,
            None,
            result,
        )
        uploaded += artifact_uploaded
        failed += artifact_failed
        skipped += artifact_skipped
    return uploaded, failed, skipped, source_slice_candidates


def _refresh_workspace_binding_metadata(
    client: object,
    agent: db.BackendAgent,
    binding: db.WorkspaceBinding,
) -> tuple[db.WorkspaceBinding, bool]:
    from hermes_cli.hades_backend_runtime import git_metadata

    metadata = git_metadata(Path(binding.repo_root))
    head_commit = str(metadata.get("head_commit") or "").strip()
    if not head_commit:
        return binding, False
    git_remote_display = str(metadata.get("git_remote_display") or "")
    git_remote_hash = str(metadata.get("git_remote_hash") or "")
    if (
        head_commit == binding.head_commit
        and git_remote_display == binding.git_remote_display
        and git_remote_hash == binding.git_remote_hash
    ):
        return binding, False

    bind_workspace = getattr(client, "bind_workspace", None)
    if not callable(bind_workspace):
        raise RuntimeError("backend client cannot refresh workspace metadata")
    response = bind_workspace(
        project_id=binding.project_id,
        agent_id=agent.agent_id,
        local_project_id=binding.local_project_id,
        workspace_fingerprint=binding.workspace_fingerprint,
        display_path=binding.display_path,
        git_remote_display=git_remote_display,
        git_remote_hash=git_remote_hash,
        head_commit=head_commit,
    )
    returned_binding_id = str(
        response.get("workspace_binding_id") or response.get("id") or ""
    ) if isinstance(response, dict) else ""
    if returned_binding_id and returned_binding_id != binding.backend_workspace_binding_id:
        raise RuntimeError("backend returned a different workspace binding during metadata refresh")

    with db.connect_closing() as conn:
        refreshed = db.update_workspace_binding_git_metadata(
            conn,
            binding.workspace_fingerprint,
            git_remote_display=git_remote_display,
            git_remote_hash=git_remote_hash,
            head_commit=head_commit,
        )
    if refreshed is None:
        raise RuntimeError("local workspace binding disappeared during metadata refresh")
    return refreshed, True


def _agent_has_capability(agent: db.BackendAgent, capability: str) -> bool:
    capabilities = agent.capabilities if isinstance(agent.capabilities, dict) else {}
    if not capabilities:
        return True
    if capability in capabilities:
        return bool(capabilities.get(capability))
    if capability == "sync_git_tree":
        return bool(capabilities.get("artifacts", False))
    if capability == "populate_backend_ast":
        return bool(capabilities.get("artifacts", False))
    return True


def _filter_sync_bindings(
    bindings: list[db.WorkspaceBinding],
    *,
    project_id: str | None,
    workspace_binding_ids: list[str] | tuple[str, ...] | None,
) -> list[db.WorkspaceBinding]:
    clean_project_id = str(project_id or "").strip()
    clean_binding_ids = {
        str(binding_id).strip()
        for binding_id in (workspace_binding_ids or [])
        if str(binding_id or "").strip()
    }
    filtered: list[db.WorkspaceBinding] = []
    for binding in bindings:
        if clean_project_id and binding.project_id != clean_project_id:
            continue
        if clean_binding_ids and binding.backend_workspace_binding_id not in clean_binding_ids:
            continue
        filtered.append(binding)
    return filtered


def _binding_contains_path(binding: db.WorkspaceBinding, path: str | Path) -> bool:
    try:
        candidate = Path(path).expanduser().resolve()
        root = Path(binding.repo_root).expanduser().resolve()
        candidate.relative_to(root)
        return True
    except Exception:
        return False


def matching_workspace_binding_ids(
    *,
    cwd: str | Path | None = None,
    changed_paths: list[str] | tuple[str, ...] | set[str] | None = None,
) -> list[str]:
    if not db.hades_backend_db_path().exists():
        return []
    probes: list[str | Path] = []
    if cwd:
        probes.append(cwd)
    probes.extend(str(path) for path in (changed_paths or []) if str(path or "").strip())
    if not probes:
        return []
    with db.connect_closing() as conn:
        bindings = [
            binding
            for binding in db.list_workspace_bindings(conn, status="linked")
            if db.get_agent(conn, binding.agent_id) is not None
        ]
    matches: list[db.WorkspaceBinding] = []
    seen: set[str] = set()
    for binding in bindings:
        if any(_binding_contains_path(binding, probe) for probe in probes):
            binding_id = binding.backend_workspace_binding_id
            if binding_id in seen:
                continue
            seen.add(binding_id)
            matches.append(binding)
    # ``list_workspace_bindings`` returns newest first.  Python's stable sort
    # therefore preserves that ordering for duplicate bindings with the same
    # repository root while preferring the most specific containing root.
    matches.sort(key=lambda binding: len(str(Path(binding.repo_root).expanduser().resolve())), reverse=True)
    return [matches[0].backend_workspace_binding_id] if matches else []


def _matching_workspace_binding_ids(**kwargs):
    """Backward-compatible private alias for workspace binding matching."""
    return matching_workspace_binding_ids(**kwargs)


def _is_unauthorized_error(exc: Exception) -> bool:
    from hermes_cli.hades_backend_client import HadesBackendError

    return isinstance(exc, HadesBackendError) and exc.status_code == 401


def _record_sync_error(binding: db.WorkspaceBinding, message: str) -> None:
    from hermes_cli.hades_backend_client import redact_secret

    redacted = redact_secret(message)
    logger.warning(
        "hades_backend.sync.error",
        extra={
            "hades_event": "sync.error",
            "hades_project_id": binding.project_id,
            "hades_workspace_binding_id": binding.backend_workspace_binding_id,
            "hades_error": redacted,
        },
    )
    with db.connect_closing() as conn:
        db.record_sync_state(
            conn,
            "last_sync_error",
            {
                "workspace_binding_id": binding.backend_workspace_binding_id,
                "project_id": binding.project_id,
                "message": redacted,
            },
        )


def _artifact_upload_cache_key(binding: db.WorkspaceBinding, schema: str) -> str:
    return f"{ARTIFACT_UPLOAD_CACHE_PREFIX}:{binding.backend_workspace_binding_id}:{schema}"


def _artifact_upload_fields(artifact_payload: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    encoded = canonical_artifact_bytes(artifact_payload)
    if len(encoded) < ARTIFACT_COMPRESSION_MIN_BYTES:
        return {"artifact": artifact_payload}, {"compressed": False, "original_bytes": len(encoded), "compressed_bytes": 0}

    compressed = gzip.compress(encoded)
    if len(compressed) >= len(encoded):
        return {"artifact": artifact_payload}, {"compressed": False, "original_bytes": len(encoded), "compressed_bytes": len(compressed)}

    return (
        {
            "artifact_encoding": "gzip+base64",
            "artifact_compressed": base64.b64encode(compressed).decode("ascii"),
            "artifact_uncompressed_sha256": hashlib.sha256(encoded).hexdigest(),
            "artifact_uncompressed_bytes": len(encoded),
            "artifact_compressed_bytes": len(compressed),
        },
        {"compressed": True, "original_bytes": len(encoded), "compressed_bytes": len(compressed)},
    )


def _artifact_file_manifest(artifact_payload: dict[str, object]) -> dict[str, object]:
    path_items: dict[str, list[str]] = {}

    def add_item(item: object, *, allow_route_path: bool = False) -> None:
        if not isinstance(item, dict):
            return
        path = str(item.get("path") or item.get("source_path") or "").strip()
        path = path.replace("\\", "/")
        if (
            not path
            or (path.startswith("/") and not allow_route_path)
            or re.match(r"^[A-Za-z]:/", path)
            or any(part in {".", ".."} for part in path.split("/"))
        ):
            return
        if "sha256" in item:
            item_hash = str(item.get("sha256") or "")
        else:
            item_hash = _artifact_payload_hash(item)
        if not item_hash:
            return
        path_items.setdefault(path, []).append(item_hash)

    for item in artifact_payload.get("files") or []:
        add_item(item)
    for section in ("routes", "symbols", "edges"):
        for item in artifact_payload.get(section) or []:
            add_item(item, allow_route_path=section == "routes")
    for section in ("nodes", "relationships"):
        for item in artifact_payload.get(section) or []:
            if not isinstance(item, dict):
                continue
            properties = item.get("properties")
            if not isinstance(properties, dict):
                continue
            add_item({"path": properties.get("path"), "row": item})
    database = artifact_payload.get("database")
    if isinstance(database, dict):
        for table in database.get("tables") or []:
            add_item(table)
            if isinstance(table, dict):
                for item in table.get("columns") or []:
                    add_item(item)
                for item in table.get("foreign_keys") or []:
                    add_item(item)

    paths = {
        path: hashlib.sha256("".join(sorted(hashes)).encode("utf-8")).hexdigest()
        for path, hashes in sorted(path_items.items())
    }
    return {"paths": paths, "count": len(paths), "sha256": _artifact_payload_hash(paths)}


def _artifact_file_delta(cached_manifest: object, current_manifest: dict[str, object]) -> dict[str, object]:
    previous_paths = {}
    if isinstance(cached_manifest, dict) and isinstance(cached_manifest.get("paths"), dict):
        previous_paths = {str(path): str(value) for path, value in cached_manifest["paths"].items()}
    raw_current_paths = current_manifest.get("paths")
    current_paths = {str(path): str(value) for path, value in raw_current_paths.items()} if isinstance(raw_current_paths, dict) else {}
    added = sorted(path for path in current_paths if path not in previous_paths)
    removed = sorted(path for path in previous_paths if path not in current_paths)
    changed = sorted(path for path, value in current_paths.items() if previous_paths.get(path) not in {None, value})
    unchanged = sorted(path for path, value in current_paths.items() if previous_paths.get(path) == value)
    return {
        "added": len(added),
        "changed": len(changed),
        "removed": len(removed),
        "unchanged": len(unchanged),
        "added_paths": added[:100],
        "changed_paths": changed[:100],
        "removed_paths": removed[:100],
    }


def _upload_graph_v2_bundle(
    client: object,
    binding: db.WorkspaceBinding,
    artifact: dict[str, object],
) -> tuple[int, int, int]:
    """Resume immutable chunks and cache only a projection reported ready."""

    from hermes_cli.config import load_config_readonly
    from hermes_cli.hades_backend_client import ChunkHeaders, redact_secret
    from hermes_cli.hades_graph_config import load_hades_graph_index_config
    from hermes_cli.hades_graph_v2.bundle import GraphBundleWriter

    version = str(artifact.get("artifact_graph_version") or "")
    source = artifact.get("source_identity")
    manifest = artifact.get("bundle")
    if (
        re.fullmatch(r"[a-f0-9]{64}", version) is None
        or not isinstance(source, dict)
        or not isinstance(manifest, dict)
        or manifest.get("schema") != "hades.graph_bundle.v2"
        or manifest.get("artifact_schema") != "hades.code_graph.v2"
        or manifest.get("artifact_graph_version") != version
        or manifest.get("source") != source
    ):
        return (0, 1, 0)
    project = manifest.get("project")
    if not isinstance(project, dict) or (
        project.get("project_id") != binding.project_id
        or project.get("workspace_binding_id")
        != binding.backend_workspace_binding_id
    ):
        return (0, 1, 0)

    cache_key = _graph_v2_cache_key(binding, source, version)
    cache_identity = {
        "schema": "hades.code_graph.v2",
        "project_id": binding.project_id,
        "workspace_binding_id": binding.backend_workspace_binding_id,
        "source_identity": source,
        "artifact_graph_version": version,
        "publication_status": "ready",
    }
    with db.connect_closing() as conn:
        cached = db.get_sync_state(conn, cache_key)
        active = db.get_sync_state(conn, _graph_v2_active_cache_key(binding))
    writer = GraphBundleWriter()
    spool = _graph_v2_spool(binding, version)
    graph_config = load_hades_graph_index_config(load_config_readonly())
    writer.cleanup_stale(
        spool.parents[2],
        ttl_seconds=graph_config.spool_ttl_seconds,
        now=time.time(),
    )
    if (
        isinstance(cached, dict)
        and isinstance(active, dict)
        and all(cached.get(key) == value for key, value in cache_identity.items())
        and active == cached
        and re.fullmatch(
            r"[a-f0-9]{64}", str(cached.get("projection_version") or "")
        )
        is not None
    ):
        refreshed = {
            **cached,
            "verification_summary": _fetch_graph_verification_summary(
                client,
                binding,
                str(cached["projection_version"]),
            ),
        }
        with db.connect_closing() as conn:
            db.record_sync_states(
                conn,
                {
                    cache_key: refreshed,
                    _graph_v2_active_cache_key(binding): refreshed,
                },
            )
        writer.delete(spool, outcome="published")
        return (0, 0, 1)

    try:
        with writer.upload_session(spool) as session:
            local = session.state
            if local.manifest != manifest or local.artifact_graph_version != version:
                raise RuntimeError("local graph spool does not match the immutable manifest")
            descriptors = manifest.get("chunks")
            if not isinstance(descriptors, list) or len(descriptors) != len(
                local.chunk_paths
            ):
                raise RuntimeError("graph manifest chunk descriptors are incomplete")

            state = client.create_graph_import(manifest)

            def remember_import_state() -> None:
                session.record_import_state(
                    import_id=state.import_id,
                    state_version=max(1, int(state.attempt_generation or 1)),
                    validation_status=state.validation_status,
                    publication_status=state.publication_status,
                )

            remember_import_state()
            for index in state.missing_chunk_indexes:
                if not 0 <= index < len(local.chunk_paths):
                    raise RuntimeError("backend requested a graph chunk outside the manifest")
                descriptor = descriptors[index]
                if not isinstance(descriptor, dict) or descriptor.get("index") != index:
                    raise RuntimeError("graph chunk descriptor index is not canonical")
                headers = ChunkHeaders(
                    sha256=str(descriptor.get("sha256") or ""),
                    uncompressed_bytes=int(descriptor.get("uncompressed_bytes") or 0),
                    compressed_sha256=str(
                        descriptor.get("compressed_sha256") or ""
                    ),
                    compressed_bytes=int(descriptor.get("compressed_bytes") or 0),
                )
                with local.chunk_paths[index].open("rb") as body:
                    client.upload_graph_chunk(state.import_id, index, body, headers)
                session.record_uploaded(index)

            if not state.is_ready:
                state = client.complete_graph_import(state.import_id, version)
                remember_import_state()
            poll_started = time.monotonic()
            max_polls = max(
                1,
                int(
                    GRAPH_IMPORT_POLL_TIMEOUT_SECONDS
                    / GRAPH_IMPORT_POLL_INTERVAL_SECONDS
                ),
            )
            for _attempt in range(max_polls):
                if state.is_ready:
                    break
                if state.validation_status in {"failed", "stale"} or (
                    state.publication_status in {"failed", "stale"}
                ):
                    raise RuntimeError("backend graph import reached a terminal failure")
                if time.monotonic() - poll_started >= GRAPH_IMPORT_POLL_TIMEOUT_SECONDS:
                    break
                time.sleep(GRAPH_IMPORT_POLL_INTERVAL_SECONDS)
                state = client.graph_import(state.import_id)
                remember_import_state()
            if not state.is_ready:
                logger.info(
                    "hades_backend.graph_v2.import_pending",
                    extra={
                        "hades_event": "graph_v2.import_pending",
                        "hades_project_id": binding.project_id,
                        "hades_workspace_binding_id": binding.backend_workspace_binding_id,
                        "hades_import_id": state.import_id,
                        "hades_validation_status": state.validation_status,
                        "hades_publication_status": state.publication_status,
                    },
                )
                return (0, 0, 1)

            verification_summary = _fetch_graph_verification_summary(
                client,
                binding,
                str(state.projection_version),
            )

            ready_identity = {
                **cache_identity,
                "projection_version": state.projection_version,
                "verification_summary": verification_summary,
            }
            with db.connect_closing() as conn:
                db.record_sync_states(
                    conn,
                    {
                        cache_key: ready_identity,
                        _graph_v2_active_cache_key(binding): ready_identity,
                    },
                )
            session.delete(outcome="published")
        return (1, 0, 0)
    except Exception as exc:
        logger.warning(
            "hades_backend.graph_v2.upload_failed",
            extra={
                "hades_event": "graph_v2.upload_failed",
                "hades_project_id": binding.project_id,
                "hades_workspace_binding_id": binding.backend_workspace_binding_id,
                "hades_error": redact_secret(str(exc)),
            },
        )
        return (0, 1, 0)


def _upload_job_artifact(
    client: object,
    agent: db.BackendAgent,
    binding: db.WorkspaceBinding,
    job_id: str | None,
    result: dict,
) -> tuple[int, int, int]:
    artifact = result.get("artifact") if isinstance(result, dict) else None
    if not isinstance(artifact, dict):
        return (0, 0, 0)
    schema = str(artifact.get("schema") or "").strip()
    if schema == "hades.code_graph.v2":
        return _upload_graph_v2_bundle(client, binding, artifact)
    if schema in {"hades.php_graph.v1", "hades.code_graph.v1"}:
        return (0, 1, 0)
    if schema not in {
        "hades.git_tree.v1",
        "hades.symbols.v1",
    }:
        return (0, 0, 0)
    artifact_payload = dict(artifact)
    head_commit = str(binding.head_commit or "").strip()
    if head_commit:
        artifact_payload.setdefault("head_commit", head_commit)
        artifact_payload.setdefault("indexed_head_commit", head_commit)
        artifact_payload.setdefault("workspace_head_commit", head_commit)
    payload_hash = _artifact_payload_hash(artifact_payload)
    file_manifest = _artifact_file_manifest(artifact_payload)
    cache_key = _artifact_upload_cache_key(binding, schema)
    with db.connect_closing() as conn:
        cached = db.get_sync_state(conn, cache_key) or {}
    file_delta = _artifact_file_delta(cached.get("file_manifest"), file_manifest)
    if (
        str(cached.get("sha256") or "") == payload_hash
        and str(cached.get("head_commit") or "") == head_commit
        and str(cached.get("schema") or "") == schema
    ):
        logger.info(
            "hades_backend.artifact.skipped",
            extra={
                "hades_event": "artifact.skipped",
                "hades_project_id": binding.project_id,
                "hades_workspace_binding_id": binding.backend_workspace_binding_id,
                "hades_job_id": job_id,
                "hades_schema": schema,
                "hades_reason": "unchanged",
                "hades_file_count": int(file_manifest.get("count") or 0),
            },
        )
        return (0, 0, 1)
    try:
        try:
            lookup = client.artifact_lookup(
                project_id=binding.project_id,
                agent_id=agent.agent_id,
                workspace_binding_id=binding.backend_workspace_binding_id,
                schema=schema,
                sha256=payload_hash,
            )
        except AttributeError:
            lookup = None
        except Exception:
            lookup = None
            logger.info(
                "hades_backend.artifact.lookup_unavailable",
                extra={
                    "hades_event": "artifact.lookup_unavailable",
                    "hades_project_id": binding.project_id,
                    "hades_workspace_binding_id": binding.backend_workspace_binding_id,
                    "hades_job_id": job_id,
                    "hades_schema": schema,
                },
            )
        if isinstance(lookup, dict) and lookup.get("exists") is True:
            artifact = lookup.get("artifact") if isinstance(lookup.get("artifact"), dict) else {}
            logger.info(
                "hades_backend.artifact.skipped",
                extra={
                    "hades_event": "artifact.skipped",
                    "hades_project_id": binding.project_id,
                    "hades_workspace_binding_id": binding.backend_workspace_binding_id,
                    "hades_job_id": job_id,
                    "hades_schema": schema,
                    "hades_reason": "unchanged_on_backend",
                    "hades_artifact_id": artifact.get("id"),
                    "hades_file_count": int(file_manifest.get("count") or 0),
                    "hades_file_delta": file_delta,
                },
            )
            with db.connect_closing() as conn:
                db.record_sync_state(
                    conn,
                    cache_key,
                    {
                        "schema": schema,
                        "sha256": payload_hash,
                        "head_commit": head_commit,
                        "job_id": job_id,
                        "backend_artifact_id": artifact.get("id"),
                        "backend_skip_reason": "unchanged_on_backend",
                        "file_manifest": file_manifest,
                        "file_delta": file_delta,
                    },
                )
            return (0, 0, 1)

        artifact_fields, compression = _artifact_upload_fields(artifact_payload)
        upload_payload = {
            "project_id": binding.project_id,
            "agent_id": agent.agent_id,
            "workspace_binding_id": binding.backend_workspace_binding_id,
            "job_id": job_id,
            "schema": schema,
            **artifact_fields,
            "sha256": payload_hash,
            "truncated": bool(artifact_payload.get("truncated", False)),
            "redactions": int(artifact_payload.get("redactions", 0) or 0),
        }
        try:
            client.upload_artifact(**upload_payload)
        except AttributeError:
            raise
        except Exception:
            if not compression.get("compressed"):
                raise
            upload_payload = {
                "project_id": binding.project_id,
                "agent_id": agent.agent_id,
                "workspace_binding_id": binding.backend_workspace_binding_id,
                "job_id": job_id,
                "schema": schema,
                "artifact": artifact_payload,
                "sha256": payload_hash,
                "truncated": bool(artifact_payload.get("truncated", False)),
                "redactions": int(artifact_payload.get("redactions", 0) or 0),
            }
            client.upload_artifact(**upload_payload)
            compression = {**compression, "fallback_raw": True}
        logger.info(
            "hades_backend.artifact.uploaded",
            extra={
                "hades_event": "artifact.uploaded",
                "hades_project_id": binding.project_id,
                "hades_workspace_binding_id": binding.backend_workspace_binding_id,
                "hades_job_id": job_id,
                "hades_schema": schema,
                "hades_truncated": bool(artifact_payload.get("truncated", False)),
                "hades_redactions": int(artifact_payload.get("redactions", 0) or 0),
                "hades_file_count": int(file_manifest.get("count") or 0),
                "hades_file_delta": file_delta,
                "hades_compressed": bool(compression.get("compressed")),
                "hades_compression_fallback_raw": bool(compression.get("fallback_raw")),
                "hades_original_bytes": int(compression.get("original_bytes") or 0),
                "hades_compressed_bytes": int(compression.get("compressed_bytes") or 0),
            },
        )
        with db.connect_closing() as conn:
            db.record_sync_state(
                conn,
                cache_key,
                {
                    "schema": schema,
                    "sha256": payload_hash,
                    "head_commit": head_commit,
                    "job_id": job_id,
                    "file_manifest": file_manifest,
                    "file_delta": file_delta,
                },
            )
        return (1, 0, 0)
    except AttributeError:
        return (0, 0, 0)
    except Exception as exc:
        _record_sync_error(binding, f"artifact upload failed: {exc}")
        return (0, 1, 0)


def _upload_job_source_slice(client: object, agent: db.BackendAgent, binding: db.WorkspaceBinding, job_id: str, result: dict) -> tuple[int, int]:
    source_slice = result.get("source_slice") if isinstance(result, dict) else None
    if not isinstance(source_slice, dict):
        return (0, 0)
    source_slice_payload = dict(source_slice)
    head_commit = str(binding.head_commit or "").strip()
    if head_commit:
        source_slice_payload.setdefault("head_commit", head_commit)
    try:
        client.create_source_slice(
            project_id=binding.project_id,
            agent_id=agent.agent_id,
            workspace_binding_id=binding.backend_workspace_binding_id,
            job_id=job_id,
            **source_slice_payload,
        )
        logger.info(
            "hades_backend.source_slice.uploaded",
            extra={
                "hades_event": "source_slice.uploaded",
                "hades_project_id": binding.project_id,
                "hades_workspace_binding_id": binding.backend_workspace_binding_id,
                "hades_job_id": job_id,
                "hades_path": source_slice_payload.get("path"),
                "hades_truncated": bool(source_slice_payload.get("truncated", False)),
                "hades_redactions": int(source_slice_payload.get("redactions", 0) or 0),
            },
        )
        return (1, 0)
    except AttributeError:
        return (0, 0)
    except Exception as exc:
        _record_sync_error(binding, f"source slice upload failed: {exc}")
        return (0, 1)
