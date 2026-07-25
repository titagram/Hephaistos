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

    Walks up from the target path checking each ancestor with lstat().
    Rejects symlinks at any level. Rejects non-directory at the target.
    Stops at the first non-existing ancestor (directories will be created
    by ensure_organism_directories). Never creates directories.
    """
    raw = (root or get_organism_home()).absolute()
    check: Path = raw
    while True:
        try:
            st = check.lstat()
        except FileNotFoundError:
            # Ancestor does not exist yet — stop checking further up
            break
        except OSError:
            raise OrganismHomeError("observer_database_unsafe") from None
        if stat_module.S_ISLNK(st.st_mode):
            raise OrganismHomeError("observer_database_unsafe") from None
        if check == raw and not stat_module.S_ISDIR(st.st_mode):
            raise OrganismHomeError("observer_database_unsafe") from None
        parent = check.parent
        if parent == check:
            break  # reached filesystem root
        check = parent
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
        d = organism_root / sub
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
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
