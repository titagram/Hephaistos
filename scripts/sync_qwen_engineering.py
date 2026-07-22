#!/usr/bin/env python3
"""Synchronize the deliberately narrow Qwen review-engine source slice."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath


REPOSITORY = "https://github.com/QwenLM/qwen-code.git"
MANIFEST_NAME = "UPSTREAM.json"
APACHE_SPDX = "SPDX-License-Identifier: Apache-2.0"
IMPORT_HELPER = Path(__file__).with_name("qwen_engineering_imports.mjs")
HEADER_COMMENT = re.compile(
    r"\A(?:\ufeff)?(?:[ \t\r\n]+|//[^\n]*(?:\n|$)|/\*.*?\*/)*",
    re.DOTALL,
)


class SyncError(ValueError):
    """The selected source cannot be safely vendored as the declared slice."""


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def run_git(source: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(source), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or error.stdout.strip()
        raise SyncError(f"git {' '.join(arguments)} failed: {detail}") from error
    return completed.stdout.strip()


def validate_relative_path(value: object) -> str:
    if not isinstance(value, str):
        raise SyncError("allowlist entry must be a relative POSIX path")
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise SyncError(f"{value!r} is not a relative POSIX path")
    return value


def validate_allowlist(entries: object) -> list[str]:
    if not isinstance(entries, list):
        raise SyncError("allowlist files must be a list")
    paths = [validate_relative_path(entry) for entry in entries]
    if len(paths) != len(set(paths)):
        raise SyncError("allowlist contains duplicate paths")
    return paths


def validate_typescript_header(source: str) -> None:
    header = HEADER_COMMENT.match(source)
    if header is None or APACHE_SPDX not in header.group(0):
        raise SyncError(f"TypeScript source is missing {APACHE_SPDX}")


def _read_allowlist(path: Path) -> tuple[list[str], dict[str, list[dict[str, str]]]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SyncError(f"unable to read allowlist {path}: {error}") from error
    if not isinstance(raw, dict) or raw.get("repository") != REPOSITORY:
        raise SyncError(f"allowlist repository must be {REPOSITORY}")
    paths = validate_allowlist(raw.get("files"))
    declared = raw.get("importExceptions", {})
    if not isinstance(declared, dict):
        raise SyncError("importExceptions must be an object")
    result: dict[str, list[dict[str, str]]] = {}
    for category in ("relative", "packages"):
        entries = declared.get(category, [])
        if not isinstance(entries, list):
            raise SyncError(f"importExceptions.{category} must be a list")
        result[category] = []
        for entry in entries:
            if not isinstance(entry, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in entry.items()
            ):
                raise SyncError(f"importExceptions.{category} entries must be string objects")
            result[category].append(dict(sorted(entry.items())))
    return paths, result


def _import_specifiers(source: str, relative: str) -> list[str]:
    """Extract literal module specifiers with the TypeScript compiler AST."""

    try:
        completed = subprocess.run(
            ["node", str(IMPORT_HELPER), relative],
            input=source,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise SyncError(f"unable to run TypeScript import helper: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise SyncError(f"TypeScript import helper failed for {relative}: {detail}")
    lines = completed.stdout.splitlines()
    if len(lines) != 1:
        raise SyncError(f"TypeScript import helper emitted invalid output for {relative}")
    try:
        response = json.loads(lines[0])
    except json.JSONDecodeError as error:
        raise SyncError(f"TypeScript import helper emitted invalid JSON for {relative}") from error
    if (
        not isinstance(response, dict)
        or set(response) != {"specifiers"}
        or not isinstance(response["specifiers"], list)
        or not all(isinstance(specifier, str) for specifier in response["specifiers"])
    ):
        raise SyncError(f"TypeScript import helper emitted invalid schema for {relative}")
    return response["specifiers"]


def _relative_target(origin: str, specifier: str) -> str:
    target = posixpath.normpath(posixpath.join(posixpath.dirname(origin), specifier))
    if target.endswith(".js"):
        target = f"{target[:-3]}.ts"
    elif not target.endswith(".ts"):
        target = f"{target}.ts"
    return validate_relative_path(target)


def _validate_imports(
    sources: dict[str, str], paths: list[str], declarations: dict[str, list[dict[str, str]]]
) -> None:
    allowed = set(paths)
    relative = {
        entry.get("source"): entry
        for entry in declarations["relative"]
        if "source" in entry and "destination" in entry
    }
    packages = {
        entry.get("specifier"): entry
        for entry in declarations["packages"]
        if "specifier" in entry and ("dependency" in entry or "destination" in entry)
    }
    if len(relative) != len(declarations["relative"]):
        raise SyncError("relative import exceptions require source and destination")
    if len(packages) != len(declarations["packages"]):
        raise SyncError("package import exceptions require specifier and dependency or destination")

    for origin, source in sources.items():
        if not origin.endswith(".ts"):
            continue
        for specifier in _import_specifiers(source, origin):
            if specifier.startswith("."):
                target = _relative_target(origin, specifier)
                if target not in allowed and target not in relative:
                    raise SyncError(
                        f"relative import {specifier!r} in {origin} targets "
                        f"unallowlisted upstream file {target}"
                    )
            elif specifier not in packages:
                raise SyncError(
                    f"package import {specifier!r} in {origin} has no declared dependency or shim"
                )


def _previous_manifested_files(destination: Path) -> list[str]:
    manifest_path = destination / MANIFEST_NAME
    if not manifest_path.exists():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        records = manifest["files"]
        if not isinstance(records, list):
            raise TypeError("files is not a list")
        return [validate_relative_path(record["path"]) for record in records]
    except (KeyError, TypeError, json.JSONDecodeError, OSError, SyncError) as error:
        raise SyncError(f"unable to safely read existing {MANIFEST_NAME}: {error}") from error


def sync(source: Path, destination: Path, ref: str, allowlist: Path) -> dict[str, object]:
    """Copy exactly the declared committed blobs and return their provenance."""

    commit = run_git(source, "rev-parse", f"{ref}^{{commit}}")
    paths, declarations = _read_allowlist(allowlist)
    blobs: dict[str, bytes] = {}
    decoded_sources: dict[str, str] = {}
    for relative in paths:
        try:
            blob = subprocess.run(
                ["git", "-C", str(source), "show", f"{commit}:{relative}"],
                check=True,
                capture_output=True,
            ).stdout
        except subprocess.CalledProcessError as error:
            raise SyncError(f"allowlisted source is absent at {commit}: {relative}") from error
        blobs[relative] = blob
        if relative.endswith(".ts"):
            try:
                decoded_sources[relative] = blob.decode("utf-8")
            except UnicodeDecodeError as error:
                raise SyncError(f"TypeScript source is not UTF-8: {relative}") from error
            validate_typescript_header(decoded_sources[relative])
    _validate_imports(decoded_sources, paths, declarations)

    previous = _previous_manifested_files(destination)
    records = []
    for relative in paths:
        blob = blobs[relative]
        atomic_write(destination / relative, blob)
        records.append({"path": relative, "sha256": hashlib.sha256(blob).hexdigest()})
    for relative in previous:
        if relative not in blobs:
            target = destination / relative
            if target.is_file() or target.is_symlink():
                target.unlink()

    manifest: dict[str, object] = {
        "schemaVersion": 1,
        "repository": REPOSITORY,
        "upstreamCommit": commit,
        "files": records,
        "hermesShims": declarations["relative"]
        + [entry for entry in declarations["packages"] if "destination" in entry],
        "dependencies": [entry for entry in declarations["packages"] if "dependency" in entry],
        "patches": [],
    }
    atomic_write(destination / MANIFEST_NAME, canonical_json(manifest))
    return manifest


def verify(destination: Path) -> tuple[str, int]:
    """Verify the destination against its own deterministic manifest."""

    manifest_path = destination / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        commit = manifest["upstreamCommit"]
        records = manifest["files"]
    except (KeyError, TypeError, json.JSONDecodeError, OSError) as error:
        raise SyncError(f"unable to read {manifest_path}: {error}") from error
    if not isinstance(commit, str) or not isinstance(records, list):
        raise SyncError(f"invalid {manifest_path}")
    mismatches = 0
    for record in records:
        if not isinstance(record, dict):
            raise SyncError(f"invalid file record in {manifest_path}")
        relative = validate_relative_path(record.get("path"))
        expected = record.get("sha256")
        if not isinstance(expected, str):
            raise SyncError(f"invalid hash record for {relative}")
        path = destination / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        mismatches += actual != expected
    return commit, mismatches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--ref")
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=Path(__file__).with_name("qwen_engineering_allowlist.json"),
    )
    parser.add_argument("--verify", action="store_true")
    arguments = parser.parse_args()
    try:
        if arguments.verify:
            commit, mismatches = verify(arguments.destination)
            print(f"verified {commit}: {mismatches} hash mismatches")
            return int(bool(mismatches))
        if arguments.source is None or arguments.ref is None:
            parser.error("--source and --ref are required unless --verify is used")
        manifest = sync(arguments.source, arguments.destination, arguments.ref, arguments.allowlist)
        print(f"synced {manifest['upstreamCommit']}: {len(manifest['files'])} files")
        return 0
    except SyncError as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
