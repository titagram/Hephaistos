"""Bounded, non-mutating public reads for the Evolution dashboard."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from hermes_constants import get_organism_home
from hermes_cli.gnothi.contract import validate_artifact
from hermes_cli.gnothi.store import OrganismRevisionStore

from .bootstrap import evolution_state_kind
from .organism_identity import OrganismIdentity, probe_organism_identity
from .reconcile import _evaluate_open_ledger, read_evolution_snapshot
from .telos_contract import telos_revision_from_dict


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


def _directory_read_flags() -> int:
    try:
        return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    except AttributeError as exc:
        raise _PublicReadError("unsafe") from exc


def _file_read_flags() -> int:
    try:
        return os.O_RDONLY | os.O_NOFOLLOW
    except AttributeError as exc:
        raise _PublicReadError("unsafe") from exc


def _open_regular_file_beneath_root(root: Path, path: Path) -> int | None:
    """Open a regular file through retained, non-symlink directory descriptors."""
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise _PublicReadError("unsafe") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise _PublicReadError("unsafe")

    descriptor: int | None = None
    try:
        expected_root = root.lstat()
        if stat.S_ISLNK(expected_root.st_mode) or not stat.S_ISDIR(
            expected_root.st_mode
        ):
            raise _PublicReadError("unsafe")
        descriptor = os.open(root, _directory_read_flags())
        opened_root = os.fstat(descriptor)
        if not stat.S_ISDIR(opened_root.st_mode) or not _same_inode(
            expected_root, opened_root
        ):
            raise _PublicReadError("unsafe")

        for part in relative.parts[:-1]:
            expected = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
                raise _PublicReadError("unsafe")
            child = os.open(part, _directory_read_flags(), dir_fd=descriptor)
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

        leaf = relative.name
        expected = os.stat(leaf, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISLNK(expected.st_mode) or not stat.S_ISREG(expected.st_mode):
            raise _PublicReadError("unsafe")
        file_descriptor = os.open(leaf, _file_read_flags(), dir_fd=descriptor)
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


def _open_regular_file(path: Path, root: Path | None) -> int | None:
    if root is not None:
        return _open_regular_file_beneath_root(root, path)
    try:
        expected = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _PublicReadError("unreadable") from exc
    if stat.S_ISLNK(expected.st_mode) or not stat.S_ISREG(expected.st_mode):
        raise _PublicReadError("unsafe")

    try:
        descriptor = os.open(path, _file_read_flags())
    except OSError as exc:
        raise _PublicReadError("unreadable") from exc
    try:
        current = os.fstat(descriptor)
        if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (
            expected.st_dev,
            expected.st_ino,
        ):
            raise _PublicReadError("unsafe")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


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
        state_kind = evolution_state_kind(evolution_root)
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
            if ledger.verify_chain():
                return "event_chain_invalid", None
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
