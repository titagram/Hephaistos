"""Safe handling for interrupted user-plugin directory replacements."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import yaml


_RESERVED_TRANSACTION_DIR = re.compile(r"^\.[^/]+\.(?:staging|backup)-[0-9a-f]+$")


class PluginTransactionError(RuntimeError):
    """A plugin replacement residue cannot be safely reconciled."""


def is_reserved_plugin_transaction_directory(name: str) -> bool:
    return bool(_RESERVED_TRANSACTION_DIR.fullmatch(name))


def _is_complete_plugin(path: Path, name: str) -> bool:
    if not path.is_dir() or path.is_symlink():
        return False
    manifest_path = path / "plugin.yaml"
    if not manifest_path.exists():
        manifest_path = path / "plugin.yml"
    if not manifest_path.is_file():
        return False
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    return isinstance(manifest, dict) and manifest.get("name") == name


def reconcile_plugin_transaction(plugins_dir: Path, name: str) -> Path:
    """Recover or safely discard only transaction residue for *name*."""
    target = plugins_dir / name
    if target.name != name or "/" in name or "\\" in name or name in {"", ".", ".."}:
        raise PluginTransactionError("invalid plugin transaction target")
    prefixes = (f".{name}.backup-", f".{name}.staging-")
    residues = [
        child
        for child in plugins_dir.iterdir()
        if child.name.startswith(prefixes)
        and is_reserved_plugin_transaction_directory(child.name)
    ]
    if not residues:
        return target
    invalid = [path for path in residues if not _is_complete_plugin(path, name)]
    if invalid:
        raise PluginTransactionError("incomplete plugin transaction residue preserved")
    backups = [path for path in residues if path.name.startswith(f".{name}.backup-")]
    stagings = [path for path in residues if path.name.startswith(f".{name}.staging-")]
    if len(backups) > 1 or len(stagings) > 1:
        raise PluginTransactionError("ambiguous plugin transaction residue preserved")
    if target.exists():
        if not _is_complete_plugin(target, name):
            raise PluginTransactionError("invalid canonical plugin preserved")
        for path in residues:
            shutil.rmtree(path)
        return target
    if len(backups) != 1:
        raise PluginTransactionError("ambiguous plugin backup residue preserved")
    backups[0].replace(target)
    for path in residues:
        if path.exists():
            shutil.rmtree(path)
    return target
