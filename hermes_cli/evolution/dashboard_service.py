"""Bounded, non-mutating public reads for the Evolution dashboard."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from agent.redact import redact_sensitive_text
from hermes_constants import get_organism_home
from hermes_cli.gnothi.contract import validate_artifact
from hermes_cli.gnothi.query import OrganismQuery
from hermes_cli.gnothi.redaction import redact_value
from hermes_cli.gnothi.store import OrganismRevisionStore

from .bootstrap import evolution_state_kind
from .blueprint_repository import BlueprintRepository
from .ledger import EvolutionLedgerError, StoredEvent
from .organism_identity import OrganismIdentity, probe_organism_identity
from .reconcile import _evaluate_open_ledger, read_evolution_snapshot
from .suggestions import SuggestionRecord, SuggestionRepository
from .telos_contract import telos_revision_from_dict
from .telos_store import TelosStore


SnapshotState = Literal["missing", "ready", "partial", "stale", "blocked", "corrupt"]

STATE_PRIORITY: dict[SnapshotState, int] = {
    "corrupt": 5,
    "blocked": 4,
    "partial": 3,
    "stale": 2,
    "missing": 1,
    "ready": 0,
}

_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_DOMAIN = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z", re.ASCII)
_REQUIRED_GNOTHI_DOMAINS = ("source", "capabilities", "runtime", "contracts")
_MAX_UNKNOWN_DOMAINS = 50
_MAX_PUBLIC_FILE_BYTES = 1024 * 1024
_MAX_GRAPH_DEPTH = 4
_MAX_GRAPH_LIMIT = 200
_MAX_REVISIONS = 50
_MAX_PIPELINE_ROWS = 50
_MAX_AUDIT_EVENTS = 100
_MAX_TELOS_DIRECTORY_ENTRIES = _MAX_REVISIONS + 1
_MAX_DASHBOARD_LIFECYCLE_EVENTS = 256
_MAX_DASHBOARD_EVOLUTION_DIRECTORY_MEMBERS = 64

# Build, canary, promotion, and stable are visible contractual stages only.
# They deliberately remain unavailable until a real local runtime owns them.
PIPELINE_STAGES = (
    ("suggestion", True),
    ("research", True),
    ("blueprint", True),
    ("build", False),
    ("canary", False),
    ("promotion", False),
    ("stable", False),
)


class PublicOrganism(TypedDict):
    id_prefix: str
    lineage_prefix: str


class EvolutionSnapshot(TypedDict):
    schema_version: int
    state: SnapshotState
    observed_at: str
    snapshot_digest: str
    organism: PublicOrganism | None
    gnothi: dict[str, Any]
    telos: dict[str, Any]
    observer: dict[str, Any]
    generations: dict[str, Any]
    pipeline: dict[str, Any]
    diagnostics: list[str]


class _PublicReadError(RuntimeError):
    """A file cannot safely contribute to a public dashboard response."""


class _PublicReadLimitError(RuntimeError):
    """A dashboard read would exceed its fixed public resource budget."""


class EvolutionDashboardError(RuntimeError):
    """A stable public reason code for an unavailable dashboard read."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class EvolutionDashboardConflict(EvolutionDashboardError):
    """A dashboard request was bound to an obsolete immutable revision."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _regular_file_exists(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _is_link_or_reparse_point(info: os.stat_result) -> bool:
    """Reject symbolic links and every Windows reparse point."""
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & reparse_flag
    )


def _supports_posix_descriptor_reads() -> bool:
    """Whether retained directory descriptors can provide the POSIX guarantee."""
    supports_dir_fd = getattr(os, "supports_dir_fd", frozenset())
    supports_follow_symlinks = getattr(os, "supports_follow_symlinks", frozenset())
    return (
        os.name == "posix"
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and os.open in supports_dir_fd
        and os.stat in supports_dir_fd
        and os.stat in supports_follow_symlinks
    )


def _posix_directory_read_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _posix_file_read_flags() -> int:
    return os.O_RDONLY | os.O_NOFOLLOW


def _portable_file_read_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_BINARY", 0)


def _validate_path_info(info: os.stat_result, *, directory: bool) -> None:
    required_kind = stat.S_ISDIR if directory else stat.S_ISREG
    if _is_link_or_reparse_point(info) or not required_kind(info.st_mode):
        raise _PublicReadError("unsafe")


def _relative_path_parts(root: Path, path: Path) -> tuple[str, ...]:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise _PublicReadError("unsafe") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise _PublicReadError("unsafe")
    return relative.parts


def _open_regular_file_beneath_root(root: Path, path: Path) -> int | None:
    """Open a regular file through retained, non-symlink directory descriptors."""
    relative_parts = _relative_path_parts(root, path)

    descriptor: int | None = None
    try:
        expected_root = root.lstat()
        _validate_path_info(expected_root, directory=True)
        descriptor = os.open(root, _posix_directory_read_flags())
        opened_root = os.fstat(descriptor)
        if not stat.S_ISDIR(opened_root.st_mode) or not _same_inode(
            expected_root, opened_root
        ):
            raise _PublicReadError("unsafe")

        for part in relative_parts[:-1]:
            expected = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
            _validate_path_info(expected, directory=True)
            child = os.open(part, _posix_directory_read_flags(), dir_fd=descriptor)
            try:
                opened = os.fstat(child)
                if not stat.S_ISDIR(opened.st_mode) or not _same_inode(
                    expected, opened
                ):
                    raise _PublicReadError("unsafe")
            except BaseException:
                os.close(child)
                raise
            parent_descriptor = descriptor
            descriptor = child
            os.close(parent_descriptor)

        leaf = relative_parts[-1]
        expected = os.stat(leaf, dir_fd=descriptor, follow_symlinks=False)
        _validate_path_info(expected, directory=False)
        file_descriptor = os.open(leaf, _posix_file_read_flags(), dir_fd=descriptor)
        try:
            opened = os.fstat(file_descriptor)
            if not stat.S_ISREG(opened.st_mode) or not _same_inode(expected, opened):
                raise _PublicReadError("unsafe")
            return file_descriptor
        except BaseException:
            os.close(file_descriptor)
            raise
    except FileNotFoundError:
        return None
    except _PublicReadError:
        raise
    except (OSError, TypeError, NotImplementedError) as exc:
        raise _PublicReadError("unreadable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _open_regular_file_portably_beneath_root(root: Path, path: Path) -> int | None:
    """Open after lstat validation and post-open revalidation without openat.

    This is intentionally a fail-closed fallback for platforms such as Windows.
    Unlike the POSIX branch, it cannot retain parent directory handles, so it
    does not provide an atomic parent-chain guarantee.
    """
    relative_parts = _relative_path_parts(root, path)
    checked_paths: list[tuple[Path, os.stat_result, bool]] = []
    current = root
    try:
        root_info = root.lstat()
    except FileNotFoundError:
        return None
    except (OSError, TypeError, NotImplementedError) as exc:
        raise _PublicReadError("unreadable") from exc
    _validate_path_info(root_info, directory=True)
    checked_paths.append((root, root_info, True))

    try:
        for part in relative_parts[:-1]:
            current = current / part
            info = current.lstat()
            _validate_path_info(info, directory=True)
            checked_paths.append((current, info, True))
        leaf_path = current / relative_parts[-1]
        expected_leaf = leaf_path.lstat()
        _validate_path_info(expected_leaf, directory=False)
    except FileNotFoundError:
        return None
    except _PublicReadError:
        raise
    except (OSError, TypeError, NotImplementedError) as exc:
        raise _PublicReadError("unreadable") from exc

    try:
        descriptor = os.open(leaf_path, _portable_file_read_flags())
    except (OSError, TypeError, NotImplementedError) as exc:
        raise _PublicReadError("unreadable") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not _same_inode(expected_leaf, opened):
            raise _PublicReadError("unsafe")
        for checked_path, expected, directory in checked_paths:
            current_info = checked_path.lstat()
            _validate_path_info(current_info, directory=directory)
            if not _same_inode(expected, current_info):
                raise _PublicReadError("unsafe")
        current_leaf = leaf_path.lstat()
        _validate_path_info(current_leaf, directory=False)
        if not _same_inode(expected_leaf, current_leaf) or not _same_inode(
            opened, current_leaf
        ):
            raise _PublicReadError("unsafe")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_unanchored_regular_file(path: Path) -> int | None:
    try:
        expected = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _PublicReadError("unreadable") from exc
    _validate_path_info(expected, directory=False)

    flags = _posix_file_read_flags() if _supports_posix_descriptor_reads() else _portable_file_read_flags()
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _PublicReadError("unreadable") from exc
    try:
        current = os.fstat(descriptor)
        linked = path.lstat()
        if (
            not stat.S_ISREG(current.st_mode)
            or not _same_inode(expected, current)
            or not _same_inode(expected, linked)
            or not _same_inode(current, linked)
        ):
            raise _PublicReadError("unsafe")
        _validate_path_info(linked, directory=False)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_regular_file(path: Path, root: Path | None) -> int | None:
    if root is not None:
        if _supports_posix_descriptor_reads():
            return _open_regular_file_beneath_root(root, path)
        return _open_regular_file_portably_beneath_root(root, path)
    return _open_unanchored_regular_file(path)


def _read_regular_json(
    path: Path, *, root: Path | None = None
) -> dict[str, Any] | None:
    """Read one bounded JSON object without following a substituted path."""
    descriptor = _open_regular_file(path, root)
    if descriptor is None:
        return None
    try:
        chunks: list[bytes] = []
        size = 0
        while chunk := os.read(descriptor, 64 * 1024):
            size += len(chunk)
            if size > _MAX_PUBLIC_FILE_BYTES:
                raise _PublicReadError("oversized")
            chunks.append(chunk)
    finally:
        os.close(descriptor)

    try:
        decoded = json.loads(b"".join(chunks))
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _PublicReadError("invalid_json") from exc
    if not isinstance(decoded, dict):
        raise _PublicReadError("invalid_json")
    return cast(dict[str, Any], decoded)


def _prefix(value: object, length: int) -> str | None:
    return value[:length] if isinstance(value, str) and len(value) >= length else None


class EvolutionDashboardService:
    """Expose a coherent, read-only dashboard snapshot for one organism root."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else get_organism_home()

    def snapshot(self) -> EvolutionSnapshot:
        """Return one bounded view without creating or repairing local state."""
        diagnostics: set[str] = set()
        try:
            identity = probe_organism_identity(self.root)
        except Exception:
            diagnostics.add("identity_corrupt")
            return self._finalize(
                state="corrupt",
                organism=None,
                gnothi=self._missing_gnothi(),
                telos=self._missing_telos(),
                observer={"state": "not_ready", "circuit_open": False},
                generations=self._missing_generations(),
                pipeline={"state": "not_ready"},
                diagnostics=diagnostics,
            )

        if identity is None:
            return self._finalize(
                state="missing",
                organism=None,
                gnothi=self._missing_gnothi(),
                telos=self._missing_telos(),
                observer={"state": "not_ready", "circuit_open": False},
                generations=self._missing_generations(),
                pipeline={"state": "not_ready"},
                diagnostics=diagnostics,
            )

        organism: PublicOrganism = {
            "id_prefix": identity.organism_id[:8],
            "lineage_prefix": identity.lineage_root_digest[:12],
        }
        gnothi = self._probe_gnothi(diagnostics)
        telos = self._probe_telos(identity, diagnostics)
        generations = self._probe_generations(diagnostics)
        observer = self._probe_observer(generations, telos, diagnostics)
        pipeline = {"state": self._pipeline_state(generations)}

        state = self._state_from_components(gnothi, telos, observer, generations)
        return self._finalize(
            state=state,
            organism=organism,
            gnothi=gnothi,
            telos=telos,
            observer=observer,
            generations=generations,
            pipeline=pipeline,
            diagnostics=diagnostics,
        )

    def graph(
        self,
        *,
        root_id: str | None = None,
        depth: int = 2,
        limit: int = _MAX_GRAPH_LIMIT,
        kinds: frozenset[str] | None = None,
        search: str = "",
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """Return a bounded public graph from one verified current revision."""
        self._validate_graph_bounds(depth=depth, limit=limit)
        if kinds is not None and (
            not isinstance(kinds, frozenset)
            or not all(isinstance(kind, str) for kind in kinds)
        ):
            raise ValueError("invalid graph kinds")
        if not isinstance(search, str):
            raise ValueError("invalid graph search")
        if root_id is not None and not isinstance(root_id, str):
            raise ValueError("invalid graph root")
        if expected_revision is not None and not isinstance(expected_revision, str):
            raise ValueError("invalid expected revision")

        identity, store, artifact = self._current_gnothi_for_read()
        if artifact is None or identity is None or store is None:
            if expected_revision is not None:
                raise EvolutionDashboardConflict("gnothi_revision_changed")
            return self._empty_graph()

        contract = cast(dict[str, Any], artifact["organism_contract"])
        revision_id = cast(str, contract["revision_id"])
        if expected_revision is not None and expected_revision != revision_id:
            raise EvolutionDashboardConflict("gnothi_revision_changed")
        try:
            result = OrganismQuery(store, artifact=artifact).subgraph(
                root_id=root_id,
                depth=depth,
                limit=limit,
                kinds=kinds or frozenset(),
                search=search,
            )
        except ValueError:
            raise
        except Exception:
            raise EvolutionDashboardError("gnothi_unavailable") from None

        return {
            "schema_version": 1,
            "revision_id": self._public_text(revision_id, identity, limit=128),
            "revision_digest": hashlib.sha256(_canonical_bytes(artifact)).hexdigest(),
            **self._public_graph_result(result, identity),
        }

    def revisions(self, limit: int = _MAX_REVISIONS) -> dict[str, Any]:
        """Return a bounded public index of immutable Gnothi revisions."""
        if type(limit) is not int or not 1 <= limit <= _MAX_REVISIONS:
            raise ValueError("invalid revision limit")
        identity, store = self._gnothi_store_for_read()
        if identity is None or store is None:
            return {
                "schema_version": 1,
                "items": [],
                "total_revisions": 0,
                "truncated": False,
            }
        try:
            artifacts = store.list_revisions()
        except Exception:
            raise EvolutionDashboardError("gnothi_unavailable") from None

        rows: list[dict[str, Any]] = []
        for artifact in artifacts:
            try:
                self._validate_gnothi_artifact(artifact)
                contract = cast(dict[str, Any], artifact["organism_contract"])
                revision_id = cast(str, contract["revision_id"])
                rows.append(
                    {
                        "revision_id": self._public_text(
                            revision_id, identity, limit=128
                        ),
                        "revision_digest": hashlib.sha256(
                            _canonical_bytes(artifact)
                        ).hexdigest(),
                        "collected_at": self._public_text(
                            contract.get("collected_at"), identity, limit=64
                        ),
                        "status": self._public_text(
                            contract.get("status"), identity, limit=32
                        ),
                        "node_count": self._row_count(artifact.get("nodes")),
                        "edge_count": self._row_count(artifact.get("edges")),
                    }
                )
            except EvolutionDashboardError:
                raise
            except Exception:
                raise EvolutionDashboardError("gnothi_unavailable") from None
        return {
            "schema_version": 1,
            "items": rows[:limit],
            "total_revisions": len(rows),
            "truncated": len(rows) > limit,
        }

    def revision_diff(self, left: str, right: str) -> dict[str, Any]:
        """Return one bounded, sanitized semantic diff between immutable revisions."""
        if not isinstance(left, str) or not isinstance(right, str):
            raise ValueError("invalid revision id")
        identity, store = self._gnothi_store_for_read()
        if identity is None or store is None:
            raise EvolutionDashboardError("gnothi_revision_unavailable")
        try:
            result = OrganismQuery(store).diff(left, right)
        except ValueError:
            raise EvolutionDashboardError("gnothi_revision_unavailable") from None
        except Exception:
            raise EvolutionDashboardError("gnothi_unavailable") from None
        return {
            "schema_version": 1,
            "left_revision_id": self._public_text(left, identity, limit=128),
            "right_revision_id": self._public_text(right, identity, limit=128),
            **self._public_diff_result(result, identity),
        }

    def telos(self, *, history_limit: int = _MAX_REVISIONS) -> dict[str, Any]:
        """Return the active local Telos and bounded, verified immutable history."""
        if (
            isinstance(history_limit, bool)
            or not isinstance(history_limit, int)
            or not 1 <= history_limit <= _MAX_REVISIONS
        ):
            raise ValueError("invalid telos history limit")

        try:
            identity = probe_organism_identity(self.root)
        except Exception:
            return self._empty_telos_read("corrupt")
        if identity is None:
            return self._empty_telos_read("missing")
        if not self._root_is_directory():
            return self._empty_telos_read("blocked")

        telos_root = self.root / "telos"
        revisions_root = telos_root / "revisions"
        if not self._safe_directory(telos_root) or not self._safe_directory(
            revisions_root
        ):
            return self._empty_telos_read("corrupt")

        try:
            store = TelosStore.from_verified_read_root(self.root)

            def read_telos_json(path: Path) -> dict[str, Any] | None:
                return _read_regular_json(path, root=self.root)

            active_digest = store.get_active_digest(read_json=read_telos_json)
            if active_digest is None:
                return self._empty_telos_read("missing")
            if _DIGEST.fullmatch(active_digest) is None:
                raise _PublicReadError("invalid_digest")

            active = store.get_revision(active_digest, read_json=read_telos_json)
            if (
                active.canonical_digest != active_digest
                or active.organism_id != identity.organism_id
            ):
                raise _PublicReadError("invalid_active_revision")

            revisions: list[tuple[str, Any]] = []
            directory_entries = 0
            for path in revisions_root.iterdir():
                directory_entries += 1
                if directory_entries > _MAX_TELOS_DIRECTORY_ENTRIES:
                    # Do not parse or retain unbounded revision documents.  The
                    # dashboard has no pagination for Telos history, so a
                    # populated directory beyond this fixed budget is blocked.
                    raise _PublicReadLimitError("too_many_telos_revisions")
                if path.suffix != ".json":
                    continue
                digest = path.stem
                if _DIGEST.fullmatch(digest) is None or not _regular_file_exists(path):
                    raise _PublicReadError("invalid_revision_path")
                revision = store.get_revision(digest, read_json=read_telos_json)
                if (
                    revision.canonical_digest != digest
                    or revision.organism_id != identity.organism_id
                ):
                    raise _PublicReadError("invalid_revision")
                if digest == active_digest and revision != active:
                    raise _PublicReadError("active_revision_changed")
                revisions.append((digest, revision))
            if active_digest not in {digest for digest, _ in revisions}:
                raise _PublicReadError("active_revision_not_listed")
        except _PublicReadLimitError:
            return self._empty_telos_read("blocked")
        except Exception:
            return self._empty_telos_read("corrupt")

        revisions.sort(key=lambda item: item[0], reverse=True)
        history = [
            self._public_telos_revision(revision, identity)
            for digest, revision in revisions
            if digest != active_digest
        ]
        return {
            "schema_version": 1,
            "state": "ready",
            "active_digest": active_digest,
            "active_revision": self._public_telos_revision(active, identity),
            "history": history[:history_limit],
            "total_revisions": len(revisions),
            "truncated": len(history) > history_limit,
        }

    def pipeline(
        self, *, attempt_id: str | None = None, limit: int = _MAX_PIPELINE_ROWS
    ) -> dict[str, Any]:
        """Return bounded local Observer and blueprint state for one pipeline view."""
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= _MAX_PIPELINE_ROWS
        ):
            raise ValueError("invalid pipeline limit")
        if attempt_id is not None and (
            not isinstance(attempt_id, str) or not 1 <= len(attempt_id) <= 256
        ):
            raise ValueError("invalid attempt id")

        identity, lifecycle_state = self._governance_read_preflight()
        if identity is None:
            return self._empty_pipeline("missing")
        if lifecycle_state != "ready":
            return self._empty_pipeline(lifecycle_state)

        def query(ledger: Any) -> dict[str, Any]:
            chain_state = self._bounded_lifecycle_chain_state(ledger)
            if chain_state != "ready":
                return self._empty_pipeline(chain_state)
            try:
                suggestion_repository = (
                    SuggestionRepository.from_verified_read_connection(
                        ledger.connection
                    )
                )
                blueprint_repository = BlueprintRepository(ledger)
                if attempt_id is None:
                    attempt_total = int(
                        ledger.connection.execute(
                            "SELECT COUNT(*) FROM attempts"
                        ).fetchone()[0]
                    )
                    attempts = ledger.connection.execute(
                        """
                        SELECT attempt_id, source_kind, state, created_at
                        FROM attempts
                        ORDER BY created_at DESC, attempt_id DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                    selected_attempt = (
                        attempts[0]["attempt_id"] if attempts else None
                    )
                    blueprint_total = int(
                        ledger.connection.execute(
                            "SELECT COUNT(*) FROM blueprint_documents"
                        ).fetchone()[0]
                    )
                    stored_blueprints = blueprint_repository.list(limit=limit)
                    suggestion_total = suggestion_repository.count_suggestions()
                    selected_suggestions = suggestion_repository.list_suggestions(
                        limit=limit
                    )
                else:
                    selected_row = ledger.connection.execute(
                        """
                        SELECT attempt_id, source_kind, state, created_at
                        FROM attempts
                        WHERE attempt_id = ?
                        """,
                        (attempt_id,),
                    ).fetchone()
                    if selected_row is None:
                        return self._empty_pipeline("missing")
                    attempts = [selected_row]
                    attempt_total = 1
                    selected_attempt = attempt_id
                    blueprint_total = int(
                        ledger.connection.execute(
                            """
                            SELECT COUNT(*) FROM blueprint_documents
                            WHERE attempt_id = ?
                            """,
                            (attempt_id,),
                        ).fetchone()[0]
                    )
                    blueprint_rows = ledger.connection.execute(
                        """
                        SELECT blueprint_id
                        FROM blueprint_documents
                        WHERE attempt_id = ?
                        ORDER BY created_at DESC, blueprint_id DESC
                        LIMIT ?
                        """,
                        (attempt_id, limit),
                    ).fetchall()
                    stored_blueprints = []
                    for row in blueprint_rows:
                        blueprint = blueprint_repository.get(row["blueprint_id"])
                        if blueprint is None:
                            raise RuntimeError("blueprint missing from snapshot")
                        stored_blueprints.append(blueprint)
                    suggestion_total = int(
                        ledger.connection.execute(
                            """
                            SELECT COUNT(DISTINCT d.suggestion_id)
                            FROM blueprint_documents d
                            JOIN opportunity_suggestions s
                              ON s.suggestion_id = d.suggestion_id
                            WHERE d.attempt_id = ?
                            """,
                            (attempt_id,),
                        ).fetchone()[0]
                    )
                    selected_suggestions = []
                    seen_suggestion_ids: set[str] = set()
                    for blueprint in stored_blueprints:
                        suggestion_id = blueprint.document.suggestion_id
                        if suggestion_id in seen_suggestion_ids:
                            continue
                        seen_suggestion_ids.add(suggestion_id)
                        suggestion = suggestion_repository.get_suggestion_by_id(
                            suggestion_id
                        )
                        if suggestion is None:
                            raise RuntimeError("blueprint suggestion missing from snapshot")
                        selected_suggestions.append(suggestion)
            except Exception:
                return self._empty_pipeline("blocked")

            public_attempts = [
                self._public_attempt(row, identity) for row in attempts
            ]
            public_suggestions = [
                self._public_suggestion(record, identity)
                for record in selected_suggestions[:limit]
            ]
            suggestion_counts: dict[str, int] = {}
            for suggestion in public_suggestions:
                state = suggestion["state"]
                suggestion_counts[state] = suggestion_counts.get(state, 0) + 1
            public_blueprints = [
                self._public_blueprint(blueprint, identity)
                for blueprint in stored_blueprints
            ]
            return {
                "schema_version": 1,
                "state": "ready",
                "attempt_id": selected_attempt,
                "attempts": public_attempts,
                "total_attempts": attempt_total,
                "attempts_truncated": attempt_total > len(public_attempts),
                "suggestions": public_suggestions,
                "suggestion_counts": suggestion_counts,
                "total_suggestions": suggestion_total,
                "suggestions_truncated": suggestion_total
                > len(public_suggestions),
                "blueprints": public_blueprints,
                "total_blueprints": blueprint_total,
                "blueprints_truncated": blueprint_total > len(public_blueprints),
                "stages": self._pipeline_stages(),
                "mutable_actions": [],
            }

        try:
            return read_evolution_snapshot(query, self.root / "evolution")
        except Exception:
            return self._empty_pipeline("blocked")

    def audit(
        self, *, after: int = 0, limit: int = _MAX_AUDIT_EVENTS
    ) -> dict[str, Any]:
        """Return a bounded, sequence-ordered public lifecycle audit read."""
        if (
            isinstance(after, bool)
            or not isinstance(after, int)
            or after < 0
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= _MAX_AUDIT_EVENTS
        ):
            raise ValueError("invalid audit bounds")

        identity, lifecycle_state = self._governance_read_preflight()
        if identity is None:
            return self._empty_audit("missing", after=after)
        if lifecycle_state != "ready":
            return self._empty_audit(lifecycle_state, after=after)

        def query(ledger: Any) -> dict[str, Any]:
            chain_state = self._bounded_lifecycle_chain_state(ledger)
            if chain_state != "ready":
                return self._empty_audit(chain_state, after=after)
            try:
                total_events = int(
                    ledger.connection.execute(
                        (
                            "SELECT COUNT(*) FROM lifecycle_events "
                            "WHERE event_sequence > ?"
                        ),
                        (after,),
                    ).fetchone()[0]
                )
                events = ledger.history(after=after, limit=limit)
            except Exception:
                return self._empty_audit("blocked", after=after)
            public_events = [
                self._public_audit_event(event, identity) for event in events
            ]
            return {
                "schema_version": 1,
                "state": "ready",
                "events": public_events,
                "total_events": total_events,
                "truncated": total_events > len(public_events),
                "next_after": (
                    public_events[-1]["sequence"] if public_events else after
                ),
                "mutable_actions": [],
            }

        try:
            return read_evolution_snapshot(query, self.root / "evolution")
        except Exception:
            return self._empty_audit("blocked", after=after)

    def _governance_read_preflight(
        self,
    ) -> tuple[OrganismIdentity | None, str]:
        """Classify a local read before any constructor can initialize state."""
        try:
            identity = probe_organism_identity(self.root)
        except Exception:
            return None, "corrupt"
        if identity is None:
            return None, "missing"
        if not self._root_is_directory():
            return identity, "blocked"
        evolution_root = self.root / "evolution"
        state_kind = evolution_state_kind(
            evolution_root,
            max_members=_MAX_DASHBOARD_EVOLUTION_DIRECTORY_MEMBERS,
        )
        if state_kind == "uninitialized":
            return identity, "not_ready"
        if state_kind != "existing":
            return identity, "blocked"
        if not _regular_file_exists(evolution_root / "evolution.db"):
            return identity, "not_ready"
        return identity, "ready"

    @staticmethod
    def _safe_directory(path: Path) -> bool:
        try:
            info = path.lstat()
        except OSError:
            return False
        return not _is_link_or_reparse_point(info) and stat.S_ISDIR(info.st_mode)

    @staticmethod
    def _bounded_lifecycle_chain_state(ledger: Any) -> SnapshotState:
        """Classify a complete lifecycle proof within the dashboard read cap."""
        try:
            return (
                "corrupt"
                if ledger.verify_chain_bounded(
                    max_events=_MAX_DASHBOARD_LIFECYCLE_EVENTS
                )
                else "ready"
            )
        except EvolutionLedgerError:
            return "blocked"

    @staticmethod
    def _pipeline_stages() -> list[dict[str, Any]]:
        return [
            {"id": stage_id, "available": available}
            for stage_id, available in PIPELINE_STAGES
        ]

    def _empty_telos_read(self, state: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "state": state,
            "active_digest": None,
            "active_revision": None,
            "history": [],
            "total_revisions": 0 if state == "missing" else None,
            "truncated": False,
        }

    def _empty_pipeline(self, state: str) -> dict[str, Any]:
        total: int | None = 0 if state in {"missing", "not_ready"} else None
        return {
            "schema_version": 1,
            "state": state,
            "attempt_id": None,
            "attempts": [],
            "total_attempts": total,
            "attempts_truncated": False,
            "suggestions": [],
            "suggestion_counts": {},
            "total_suggestions": total,
            "suggestions_truncated": False,
            "blueprints": [],
            "total_blueprints": total,
            "blueprints_truncated": False,
            "stages": self._pipeline_stages(),
            "mutable_actions": [],
        }

    def _empty_audit(self, state: str, *, after: int) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "state": state,
            "events": [],
            "total_events": 0 if state in {"missing", "not_ready"} else None,
            "truncated": False,
            "next_after": after,
            "mutable_actions": [],
        }

    def _public_telos_item(
        self, item: object, identity: OrganismIdentity
    ) -> dict[str, Any]:
        return {
            "id": self._public_text(getattr(item, "id", None), identity, limit=64),
            "statement": self._public_text(
                getattr(item, "statement", None), identity, limit=500
            ),
            "tags": [
                self._public_text(tag, identity, limit=128)
                for tag in tuple(getattr(item, "tags", ()))[:16]
                if isinstance(tag, str)
            ],
            "priority": (
                getattr(item, "priority", 0)
                if isinstance(getattr(item, "priority", None), int)
                else 0
            ),
        }

    def _public_telos_revision(
        self, revision: object, identity: OrganismIdentity
    ) -> dict[str, Any]:
        fields = (
            "desired_traits",
            "capability_directions",
            "priorities",
            "tradeoffs",
            "prohibitions",
            "success_indicators",
        )
        result = {
            "digest": self._public_text(
                getattr(revision, "canonical_digest", None), identity, limit=64
            ),
            "parent_digest": self._public_text(
                getattr(revision, "parent_digest", None), identity, limit=64
            )
            or None,
            "purpose": self._public_text(
                getattr(revision, "purpose", None), identity, limit=1000
            ),
            "proactivity_policy": self._public_telos_item(
                getattr(revision, "proactivity_policy", None), identity
            ),
        }
        for name in fields:
            collection = getattr(revision, name, ())
            result[name] = [
                self._public_telos_item(item, identity)
                for item in tuple(collection)[:32]
            ]
        return result

    def _public_attempt(
        self, row: Any, identity: OrganismIdentity
    ) -> dict[str, Any]:
        return {
            "attempt_id": self._public_text(row["attempt_id"], identity, limit=256),
            "source_kind": self._public_text(row["source_kind"], identity, limit=64),
            "state": self._public_text(row["state"], identity, limit=64),
            "created_at": self._public_text(row["created_at"], identity, limit=64),
        }

    @staticmethod
    def _public_number(value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0.0
        numeric = float(value)
        return numeric if math.isfinite(numeric) else 0.0

    def _public_suggestion(
        self, record: SuggestionRecord, identity: OrganismIdentity
    ) -> dict[str, Any]:
        return {
            "suggestion_id": self._public_text(
                record.suggestion_id, identity, limit=64
            ),
            "state": self._public_text(record.state, identity, limit=64),
            "score": self._public_number(record.score),
            "telos_alignment": self._public_number(record.telos_alignment),
            "observation_count": max(0, int(record.observation_count)),
            "distinct_session_count": max(0, int(record.distinct_session_count)),
            "summary": self._public_text(record.summary_reason, identity, limit=512),
            "created_at": self._public_text(record.created_at, identity, limit=64),
            "updated_at": self._public_text(record.updated_at, identity, limit=64),
        }

    def _public_blueprint(
        self, blueprint: object, identity: OrganismIdentity
    ) -> dict[str, Any]:
        document = getattr(blueprint, "document", None)
        snapshot = getattr(document, "observer_snapshot", None)
        component_classes = getattr(document, "proposed_component_classes", ())
        return {
            "blueprint_id": self._public_text(
                getattr(blueprint, "blueprint_id", None), identity, limit=128
            ),
            "attempt_id": self._public_text(
                getattr(blueprint, "attempt_id", None), identity, limit=256
            ),
            "canonical_digest": self._public_text(
                getattr(blueprint, "canonical_digest", None), identity, limit=64
            ),
            "state": self._public_text(
                getattr(blueprint, "state", None), identity, limit=64
            ),
            "created_at": self._public_text(
                getattr(blueprint, "created_at", None), identity, limit=64
            ),
            "suggestion_id": self._public_text(
                getattr(document, "suggestion_id", None), identity, limit=64
            ),
            "active_telos_digest": self._public_text(
                getattr(document, "active_telos_digest", None), identity, limit=64
            ),
            "summary": self._public_text(
                getattr(snapshot, "summary_reason", None), identity, limit=512
            ),
            "capability_hypothesis": self._public_text(
                getattr(document, "capability_hypothesis", None), identity, limit=768
            ),
            "proposed_component_classes": [
                self._public_text(item, identity, limit=64)
                for item in tuple(component_classes)[:16]
                if isinstance(item, str)
            ],
        }

    def _public_audit_event(
        self, event: StoredEvent, identity: OrganismIdentity
    ) -> dict[str, Any]:
        return {
            "sequence": event.event_sequence,
            "event_id": self._public_text(event.event_id, identity, limit=256),
            "attempt_id": self._public_text(event.attempt_id, identity, limit=256)
            or None,
            "generation_id": self._public_text(event.generation_id, identity, limit=64)
            or None,
            "event_type": self._public_text(event.event_type, identity, limit=128),
            "prior_state": self._public_text(event.prior_state, identity, limit=64)
            or None,
            "next_state": self._public_text(event.next_state, identity, limit=64)
            or None,
            "actor": self._public_text(event.actor, identity, limit=128),
            "reason_code": self._public_text(event.reason_code, identity, limit=128),
            "summary": self._public_text(event.reason_summary, identity, limit=512),
            "created_at": self._public_text(event.created_at, identity, limit=64),
            "event_digest": self._public_text(event.event_digest, identity, limit=64),
        }

    @staticmethod
    def _validate_graph_bounds(*, depth: int, limit: int) -> None:
        if type(depth) is not int or not 0 <= depth <= _MAX_GRAPH_DEPTH:
            raise ValueError("invalid graph depth")
        if type(limit) is not int or not 1 <= limit <= _MAX_GRAPH_LIMIT:
            raise ValueError("invalid graph limit")

    @staticmethod
    def _empty_graph() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "revision_id": None,
            "revision_digest": None,
            "nodes": [],
            "edges": [],
            "blockers": [],
            "total_nodes": 0,
            "total_edges": 0,
            "truncated": False,
        }

    def _gnothi_store_for_read(
        self,
    ) -> tuple[OrganismIdentity | None, OrganismRevisionStore | None]:
        try:
            identity = probe_organism_identity(self.root)
        except Exception:
            raise EvolutionDashboardError("organism_identity_corrupt") from None
        if identity is None or not self._root_is_directory():
            return identity, None
        return identity, OrganismRevisionStore(self.root / "gnothi_seauton")

    def _current_gnothi_for_read(
        self,
    ) -> tuple[
        OrganismIdentity | None,
        OrganismRevisionStore | None,
        dict[str, Any] | None,
    ]:
        identity, store = self._gnothi_store_for_read()
        if identity is None or store is None:
            return identity, store, None
        try:
            artifact = store.current()
        except Exception:
            raise EvolutionDashboardError("gnothi_unavailable") from None
        if artifact is None:
            return identity, store, None
        self._validate_gnothi_artifact(artifact)
        return identity, store, artifact

    @staticmethod
    def _validate_gnothi_artifact(artifact: dict[str, Any]) -> None:
        if validate_artifact(artifact):
            raise EvolutionDashboardError("gnothi_unavailable")
        contract = artifact.get("organism_contract")
        if not isinstance(contract, dict) or not isinstance(
            contract.get("revision_id"), str
        ):
            raise EvolutionDashboardError("gnothi_unavailable")

    @staticmethod
    def _public_text(
        value: object,
        identity: OrganismIdentity,
        *,
        limit: int,
    ) -> str:
        safe, _ = redact_value(str(value if value is not None else ""))
        text = redact_sensitive_text(str(safe), force=True, file_read=True)
        for private_value in (
            identity.organism_id,
            identity.lineage_root_digest,
        ):
            text = text.replace(private_value, "[REDACTED]")
        return text[:limit]

    def _public_node(
        self, node: object, identity: OrganismIdentity
    ) -> dict[str, Any]:
        row = node if isinstance(node, dict) else {}
        state = row.get("state")
        public_state = (
            {
                self._public_text(key, identity, limit=128): value
                for key, value in state.items()
                if isinstance(value, bool)
            }
            if isinstance(state, dict)
            else {}
        )
        refs = row.get("evidence_refs")
        owner_class = row.get("owner_class")
        if not isinstance(owner_class, (str, int, float)):
            owner = row.get("owner")
            owner_class = owner.get("class") if isinstance(owner, dict) else None
        return {
            "id": self._public_text(row.get("id"), identity, limit=256),
            "kind": self._public_text(row.get("kind"), identity, limit=128),
            "label": self._public_text(row.get("label"), identity, limit=500),
            "owner_class": self._public_text(
                owner_class, identity, limit=128
            ),
            "generation_scope": self._public_text(
                row.get("generation_scope"), identity, limit=64
            ),
            "state": public_state,
            "evidence_refs": [
                self._public_text(ref, identity, limit=256)
                for ref in (refs[:20] if isinstance(refs, list) else [])
                if isinstance(ref, (str, int, float))
            ],
        }

    def _public_graph_result(
        self, result: dict[str, Any], identity: OrganismIdentity
    ) -> dict[str, Any]:
        raw_nodes = result.get("nodes")
        nodes = [
            self._public_node(node, identity)
            for node in (
                raw_nodes[:_MAX_GRAPH_LIMIT] if isinstance(raw_nodes, list) else []
            )
        ]
        node_ids = {node["id"] for node in nodes}
        edges: list[dict[str, Any]] = []
        raw_edges = result.get("edges")
        for edge in raw_edges[:_MAX_GRAPH_LIMIT] if isinstance(raw_edges, list) else []:
            if not isinstance(edge, dict):
                continue
            refs = edge.get("evidence_refs")
            public_edge = {
                "id": self._public_text(edge.get("id"), identity, limit=256),
                "kind": self._public_text(edge.get("kind"), identity, limit=128),
                "from": self._public_text(edge.get("from"), identity, limit=256),
                "to": self._public_text(edge.get("to"), identity, limit=256),
                "evidence_refs": [
                    self._public_text(ref, identity, limit=256)
                    for ref in (refs[:20] if isinstance(refs, list) else [])
                    if isinstance(ref, (str, int, float))
                ],
            }
            if public_edge["from"] in node_ids and public_edge["to"] in node_ids:
                edges.append(public_edge)
        blockers = [
            node
            for node in nodes
            if node["state"].get("available") is False
            or node["state"].get("degraded") is True
        ]
        return {
            "nodes": nodes,
            "edges": edges,
            "blockers": blockers,
            "total_nodes": int(result.get("total_nodes", 0)),
            "total_edges": int(result.get("total_edges", 0)),
            "truncated": bool(result.get("truncated", False)),
        }

    def _public_diff_result(
        self, result: dict[str, Any], identity: OrganismIdentity
    ) -> dict[str, Any]:
        def nodes(name: str) -> list[dict[str, Any]]:
            value = result.get(name)
            return [
                self._public_node(node, identity)
                for node in (value[:_MAX_GRAPH_LIMIT] if isinstance(value, list) else [])
            ]

        def state(value: object) -> dict[str, bool]:
            return (
                {
                    self._public_text(key, identity, limit=128): item
                    for key, item in value.items()
                    if isinstance(item, bool)
                }
                if isinstance(value, dict)
                else {}
            )

        changed_rows = result.get("changed_state")
        changed_state = [
            {
                "id": self._public_text(row.get("id"), identity, limit=256),
                "before": state(row.get("before")),
                "after": state(row.get("after")),
            }
            for row in (
                changed_rows[:_MAX_GRAPH_LIMIT]
                if isinstance(changed_rows, list)
                else []
            )
            if isinstance(row, dict)
        ]
        dependencies = result.get("dependency_changes")
        dependency_changes = [
            {
                "kind": self._public_text(row[0], identity, limit=128),
                "from": self._public_text(row[1], identity, limit=256),
                "to": self._public_text(row[2], identity, limit=256),
            }
            for row in (
                dependencies[:_MAX_GRAPH_LIMIT]
                if isinstance(dependencies, list)
                else []
            )
            if isinstance(row, tuple)
            and len(row) == 3
        ]
        coverage = result.get("coverage_changes")
        coverage_changes = [
            {
                "domain": self._public_text(row.get("domain"), identity, limit=64),
                "before": self._public_text(row.get("before"), identity, limit=32),
                "after": self._public_text(row.get("after"), identity, limit=32),
            }
            for row in (
                coverage[:_MAX_GRAPH_LIMIT] if isinstance(coverage, list) else []
            )
            if isinstance(row, dict)
        ]
        quality = result.get("quality_changes")
        quality_changes = [
            {
                "before": self._public_text(row.get("before"), identity, limit=32),
                "after": self._public_text(row.get("after"), identity, limit=32),
            }
            for row in (
                quality[:_MAX_GRAPH_LIMIT] if isinstance(quality, list) else []
            )
            if isinstance(row, dict)
        ]
        return {
            "added_capabilities": nodes("added_capabilities"),
            "removed_capabilities": nodes("removed_capabilities"),
            "changed_state": changed_state,
            "dependency_changes": dependency_changes,
            "invariant_impact": nodes("invariant_impact"),
            "runtime_changes": nodes("runtime_changes"),
            "quality_changes": quality_changes,
            "coverage_changes": coverage_changes,
            "truncated": bool(result.get("truncated", False)),
        }

    @staticmethod
    def _missing_gnothi() -> dict[str, Any]:
        return {
            "state": "missing",
            "revision_id": None,
            "revision_digest": None,
            "node_count": 0,
            "edge_count": 0,
            "coverage": {
                "current_domains": 0,
                "total_domains": 0,
                "unknown_domains": [],
                "truncated": False,
            },
        }

    @staticmethod
    def _missing_telos() -> dict[str, Any]:
        return {"state": "missing", "active_digest_prefix": None}

    @staticmethod
    def _missing_generations() -> dict[str, Any]:
        return {
            "state": "missing",
            "active_generation_prefix": None,
            "last_known_good_generation_prefix": None,
            "overlay_enabled": False,
        }

    def _root_is_directory(self) -> bool:
        try:
            return self.root.is_dir() and stat.S_ISDIR(self.root.lstat().st_mode)
        except OSError:
            return False

    def _probe_gnothi(self, diagnostics: set[str]) -> dict[str, Any]:
        if not self._root_is_directory():
            return self._missing_gnothi()

        # The store's constructor is inert, but only use it after the root preflight.
        store = OrganismRevisionStore(self.root / "gnothi_seauton")
        try:
            artifact = store.current()
        except Exception:
            diagnostics.add("gnothi_pointer_invalid")
            result = self._missing_gnothi()
            result["state"] = "corrupt"
            return result
        if artifact is None:
            diagnostics.add("gnothi_pointer_missing")
            return self._missing_gnothi()

        try:
            if validate_artifact(artifact):
                raise ValueError("invalid_artifact")
            contract = artifact["organism_contract"]
            if not isinstance(contract, dict):
                raise ValueError("invalid_contract")
            revision_id = contract.get("revision_id")
            if not isinstance(revision_id, str):
                raise ValueError("invalid_revision")
            coverage = self._coverage_summary(contract.get("coverage"))
            status = self._gnothi_state(contract.get("status"), coverage)
            return {
                "state": status,
                "revision_id": revision_id,
                "revision_digest": hashlib.sha256(_canonical_bytes(artifact)).hexdigest(),
                "node_count": self._row_count(artifact.get("nodes")),
                "edge_count": self._row_count(artifact.get("edges")),
                "coverage": coverage,
            }
        except Exception:
            diagnostics.add("gnothi_pointer_invalid")
            result = self._missing_gnothi()
            result["state"] = "corrupt"
            return result

    @staticmethod
    def _row_count(value: object) -> int:
        return len(value) if isinstance(value, list) else 0

    @staticmethod
    def _coverage_summary(value: object) -> dict[str, Any]:
        rows = value if isinstance(value, dict) else {}
        names = set(_REQUIRED_GNOTHI_DOMAINS)
        names.update(name for name in rows if isinstance(name, str) and _DOMAIN.fullmatch(name))
        statuses: dict[str, str] = {}
        for name in names:
            row = rows.get(name)
            status = row.get("status") if isinstance(row, dict) else "missing"
            statuses[name] = status if isinstance(status, str) else "missing"
        unknown = sorted(name for name, status in statuses.items() if status != "current")
        return {
            "current_domains": sum(status == "current" for status in statuses.values()),
            "total_domains": len(statuses),
            "unknown_domains": unknown[:_MAX_UNKNOWN_DOMAINS],
            "truncated": len(unknown) > _MAX_UNKNOWN_DOMAINS,
        }

    @staticmethod
    def _gnothi_state(contract_status: object, coverage: dict[str, Any]) -> SnapshotState:
        unknown = coverage["unknown_domains"]
        if contract_status == "stale":
            return "stale"
        if contract_status == "partial" or unknown or coverage["truncated"]:
            return "partial"
        return "ready"

    def _probe_telos(
        self,
        identity: OrganismIdentity,
        diagnostics: set[str],
    ) -> dict[str, Any]:
        pointer_path = self.root / "telos" / "active.json"
        try:
            pointer = _read_regular_json(pointer_path, root=self.root)
            if pointer is None:
                return self._missing_telos()
            digest = pointer.get("digest")
            if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
                raise _PublicReadError("invalid_digest")
            revision = _read_regular_json(
                self.root / "telos" / "revisions" / f"{digest}.json",
                root=self.root,
            )
            if revision is None:
                raise _PublicReadError("missing_revision")
            parsed = telos_revision_from_dict(revision)
            if (
                parsed.canonical_digest != digest
                or parsed.organism_id != identity.organism_id
            ):
                raise _PublicReadError("mismatched_revision")
            return {"state": "ready", "active_digest_prefix": digest[:12]}
        except _PublicReadError:
            diagnostics.add("telos_pointer_invalid")
        except Exception:
            diagnostics.add("telos_pointer_invalid")
        return {"state": "corrupt", "active_digest_prefix": None}

    def _probe_generations(self, diagnostics: set[str]) -> dict[str, Any]:
        evolution_root = self.root / "evolution"
        state_kind = evolution_state_kind(
            evolution_root,
            max_members=_MAX_DASHBOARD_EVOLUTION_DIRECTORY_MEMBERS,
        )
        if state_kind == "uninitialized":
            diagnostics.add("lifecycle_unavailable")
            return self._missing_generations()
        if state_kind != "existing":
            diagnostics.add("lifecycle_unavailable")
            result = self._missing_generations()
            result["state"] = "blocked"
            return result
        if not _regular_file_exists(evolution_root / "evolution.db"):
            diagnostics.add("lifecycle_unavailable")
            return self._missing_generations()

        def query(ledger: Any) -> tuple[str, Any]:
            chain_state = self._bounded_lifecycle_chain_state(ledger)
            if chain_state == "corrupt":
                return "event_chain_invalid", None
            if chain_state != "ready":
                return "lifecycle_unavailable", None
            return "ok", _evaluate_open_ledger(evolution_root, ledger, repair=False)

        try:
            outcome, reconciliation = read_evolution_snapshot(query, evolution_root)
        except Exception:
            diagnostics.add("lifecycle_unavailable")
            result = self._missing_generations()
            result["state"] = "blocked"
            return result
        if outcome == "event_chain_invalid":
            diagnostics.add("event_chain_invalid")
            result = self._missing_generations()
            result["state"] = "corrupt"
            return result
        if outcome == "lifecycle_unavailable":
            diagnostics.add("lifecycle_unavailable")
            result = self._missing_generations()
            result["state"] = "blocked"
            return result

        if reconciliation.status == "coherent":
            state: SnapshotState = "ready"
        elif reconciliation.status == "restored_lkg":
            state = "stale"
        else:
            state = "blocked"
            diagnostics.add("lifecycle_unavailable")
        return {
            "state": state,
            "active_generation_prefix": _prefix(
                None if reconciliation.active is None else reconciliation.active.generation_id,
                12,
            ),
            "last_known_good_generation_prefix": _prefix(
                None
                if reconciliation.last_known_good is None
                else reconciliation.last_known_good.generation_id,
                12,
            ),
            "overlay_enabled": bool(reconciliation.overlay_enabled),
        }

    def _probe_observer(
        self,
        generations: dict[str, Any],
        telos: dict[str, Any],
        diagnostics: set[str],
    ) -> dict[str, Any]:
        if generations["state"] != "ready" or telos["state"] != "ready":
            return {"state": "not_ready", "circuit_open": False}
        try:
            state = _read_regular_json(self.root / "observer_state.json")
        except _PublicReadError:
            diagnostics.add("observer_state_invalid")
            return {"state": "degraded", "circuit_open": True}
        if state is None:
            return {"state": "ready", "circuit_open": False}
        circuit_open = state.get("circuit_open")
        if not isinstance(circuit_open, bool):
            diagnostics.add("observer_state_invalid")
            return {"state": "degraded", "circuit_open": True}
        return {
            "state": "degraded" if circuit_open else "ready",
            "circuit_open": circuit_open,
        }

    @staticmethod
    def _pipeline_state(generations: dict[str, Any]) -> str:
        return "ready" if generations["state"] == "ready" else "not_ready"

    @staticmethod
    def _state_from_components(
        gnothi: dict[str, Any],
        telos: dict[str, Any],
        observer: dict[str, Any],
        generations: dict[str, Any],
    ) -> SnapshotState:
        states: list[SnapshotState] = []
        for component in (gnothi, generations):
            value = component["state"]
            if value in STATE_PRIORITY:
                states.append(cast(SnapshotState, value))
        if telos["state"] == "corrupt":
            states.append("corrupt")
        if observer["state"] == "degraded":
            states.append("stale")
        selected = max(states, key=STATE_PRIORITY.__getitem__, default="ready")
        # Once an identity exists, a missing required domain is a partial
        # organism rather than an entirely missing organism.
        return "partial" if selected == "missing" else selected

    @staticmethod
    def _finalize(
        *,
        state: SnapshotState,
        organism: PublicOrganism | None,
        gnothi: dict[str, Any],
        telos: dict[str, Any],
        observer: dict[str, Any],
        generations: dict[str, Any],
        pipeline: dict[str, Any],
        diagnostics: set[str],
    ) -> EvolutionSnapshot:
        body = {
            "schema_version": 1,
            "state": state,
            "organism": organism,
            "gnothi": gnothi,
            "telos": telos,
            "observer": observer,
            "generations": generations,
            "pipeline": pipeline,
            "diagnostics": sorted(diagnostics),
        }
        snapshot = {
            **body,
            "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "snapshot_digest": hashlib.sha256(_canonical_bytes(body)).hexdigest(),
        }
        return cast(EvolutionSnapshot, snapshot)
