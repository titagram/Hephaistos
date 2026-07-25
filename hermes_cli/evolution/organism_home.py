"""Global organism directory layout resolver and permission enforcer."""

from __future__ import annotations

import os
import stat as stat_module
from pathlib import Path
from hermes_constants import get_organism_home, get_default_hermes_root


class OrganismHomeError(RuntimeError):
    """Fail-closed error for unsafe organism root paths."""


def resolve_organism_root(root: Path | None = None) -> Path:
    """Resolve the organism root path, rejecting symlinks and non-directories.

    Walks from the filesystem root down toward the target, lstat() each
    component. Rejects symlinks, non-directories, and unsafe modes at
    every existing component. Non-existing components are accepted (will be
    created by ensure_organism_directories). Never creates directories.
    """
    raw = (root or get_organism_home()).absolute()
    walked = Path(raw.parts[0])
    for part in raw.parts[1:]:
        candidate = walked / part
        try:
            st = candidate.lstat()
        except FileNotFoundError:
            return raw
        except OSError:
            raise OrganismHomeError("observer_database_unsafe") from None
        if stat_module.S_ISLNK(st.st_mode):
            raise OrganismHomeError("observer_database_unsafe") from None
        if not stat_module.S_ISDIR(st.st_mode):
            raise OrganismHomeError("observer_database_unsafe") from None
        walked = candidate
    return raw


def ensure_organism_directories(root: Path | None = None) -> Path:
    """Ensure all global organism directory trees exist with 0700 permissions."""
    organism_root = resolve_organism_root(root)
    organism_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(organism_root, 0o700)
    except OSError:
        pass

    subdirs = [
        "evolution",
        "evolution/generations",
        "telos",
        "telos/revisions",
        "telos/drafts",
        "gnothi_seauton",
        "gnothi_seauton/revisions",
        "evidence-brokers",
        "archives",
        "archives/legacy-profile-state",
        "wiki",
    ]

    for sub in subdirs:
        subpath = organism_root / sub
        # Reject symlinks at any subdirectory
        if subpath.exists():
            try:
                st = subpath.lstat()
                if stat_module.S_ISLNK(st.st_mode):
                    raise OrganismHomeError("observer_database_unsafe") from None
            except FileNotFoundError:
                pass
            except OSError:
                raise OrganismHomeError("observer_database_unsafe") from None
        subpath.mkdir(mode=0o700, exist_ok=True)
        try:
            os.chmod(subpath, 0o700)
        except OSError:
            pass

    return organism_root


def secure_file_permissions(path: Path) -> None:
    """Chmod file 0600 if it exists."""
    if path.exists():
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
