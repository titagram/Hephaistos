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


def _import_specifiers(source: str) -> list[str]:
    """Return static and literal dynamic module specifiers, not fixture strings."""

    declarations: list[str] = []
    pending: list[str] = []
    for line in source.splitlines():
        if not pending and re.match(r"^\s*(?:import|export)\b", line):
            pending.append(line)
        elif pending:
            pending.append(line)
        if pending and ";" in line:
            declarations.append("\n".join(pending))
            pending = []
    if pending:
        declarations.append("\n".join(pending))

    specifiers: list[str] = []
    for declaration in declarations:
        matches = re.findall(r"\bfrom\s*['\"]([^'\"]+)['\"]", declaration)
        if matches:
            specifiers.extend(matches)
            continue
        bare = re.match(r"^\s*import\s*['\"]([^'\"]+)['\"]", declaration)
        if bare:
            specifiers.append(bare.group(1))
    return specifiers + _literal_dynamic_import_specifiers(source)


def _literal_dynamic_import_specifiers(source: str) -> list[str]:
    """Find import expressions without treating fixture strings as code."""

    specifiers, _ = _scan_javascript_code(source, 0)
    return specifiers


def _scan_javascript_code(
    source: str, index: int, *, stop_at_closing_brace: bool = False
) -> tuple[list[str], int]:
    """Lex code conservatively, recursively entering template expressions."""

    specifiers: list[str] = []
    length = len(source)
    nested_braces = 0
    while index < length:
        cursor = _skip_javascript_ignored(source, index)
        if cursor != index:
            index = cursor
            continue
        character = source[index]
        if character in {"'", '"'}:
            index = _skip_javascript_string(source, index)
            continue
        if character == "`":
            nested_specifiers, index = _scan_javascript_template(source, index)
            specifiers.extend(nested_specifiers)
            continue
        if stop_at_closing_brace and character == "}":
            if nested_braces == 0:
                return specifiers, index + 1
            nested_braces -= 1
            index += 1
            continue
        if stop_at_closing_brace and character == "{":
            nested_braces += 1
            index += 1
            continue
        if (
            source.startswith("import", index)
            and (index == 0 or not _javascript_identifier_character(source[index - 1]))
            and (
                index + len("import") == length
                or not _javascript_identifier_character(source[index + len("import")])
            )
        ):
            imported, index = _scan_import_expression(source, index + len("import"))
            specifiers.extend(imported)
            continue
        index += 1
    return specifiers, index


def _scan_javascript_template(source: str, index: int) -> tuple[list[str], int]:
    """Skip template text but lex every ``${...}`` expression as code."""

    specifiers: list[str] = []
    index += 1
    while index < len(source):
        if source[index] == "\\":
            index += 2
            continue
        if source[index] == "`":
            return specifiers, index + 1
        if source.startswith("${", index):
            nested, index = _scan_javascript_code(
                source, index + 2, stop_at_closing_brace=True
            )
            specifiers.extend(nested)
            continue
        index += 1
    return specifiers, index


def _scan_import_expression(source: str, index: int) -> tuple[list[str], int]:
    """Recognize dynamic imports and TypeScript ``import x = require(...)``."""

    cursor = _skip_javascript_ignored(source, index)
    if cursor < len(source) and source[cursor] == "(":
        return _scan_literal_import_argument(source, cursor + 1)

    if cursor < len(source) and source[cursor] in {"'", '"'}:
        return _scan_literal_import_argument(source, cursor)

    identifier_end = _skip_javascript_identifier(source, cursor)
    if identifier_end == cursor:
        return _scan_static_import_specifier(source, cursor)
    cursor = _skip_javascript_ignored(source, identifier_end)
    if cursor >= len(source) or source[cursor] != "=":
        return _scan_static_import_specifier(source, cursor)
    cursor = _skip_javascript_ignored(source, cursor + 1)
    if not source.startswith("require", cursor) or (
        cursor + len("require") < len(source)
        and _javascript_identifier_character(source[cursor + len("require")])
    ):
        return _scan_static_import_specifier(source, cursor)
    cursor = _skip_javascript_ignored(source, cursor + len("require"))
    if cursor >= len(source) or source[cursor] != "(":
        return _scan_static_import_specifier(source, cursor)
    return _scan_literal_import_argument(source, cursor + 1)


def _scan_static_import_specifier(source: str, index: int) -> tuple[list[str], int]:
    """Find ``from 'literal'`` while allowing comments inside declarations."""

    cursor = index
    while cursor < len(source):
        cursor = _skip_javascript_ignored(source, cursor)
        if cursor >= len(source) or source[cursor] == ";":
            return [], cursor
        if (
            source.startswith("from", cursor)
            and (cursor == 0 or not _javascript_identifier_character(source[cursor - 1]))
            and (
                cursor + len("from") == len(source)
                or not _javascript_identifier_character(source[cursor + len("from")])
            )
        ):
            return _scan_literal_import_argument(source, cursor + len("from"))
        if source[cursor] in {"'", '"'}:
            cursor = _skip_javascript_string(source, cursor)
            continue
        if source[cursor] == "`":
            _, cursor = _scan_javascript_template(source, cursor)
            continue
        cursor += 1
    return [], cursor


def _scan_literal_import_argument(source: str, index: int) -> tuple[list[str], int]:
    cursor = _skip_javascript_ignored(source, index)
    if cursor < len(source) and source[cursor] in {"'", '"'}:
        specifier, end, closed = _read_javascript_string(source, cursor)
        if not closed and specifier.startswith("."):
            raise SyncError("unable to safely classify relative import expression")
        return [specifier] if closed else [], end
    if cursor < len(source) and source[cursor] == "`":
        specifier, end, literal = _read_template_import_argument(source, cursor)
        if specifier.startswith(".") and not literal:
            raise SyncError("unable to safely classify relative import expression")
        return [specifier] if literal else [], end
    if _relative_looking(source, cursor):
        raise SyncError("unable to safely classify relative import expression")
    return [], cursor


def _read_template_import_argument(source: str, index: int) -> tuple[str, int, bool]:
    """Read a template argument only when it contains no interpolation."""

    characters: list[str] = []
    index += 1
    while index < len(source):
        if source[index] == "\\" and index + 1 < len(source):
            characters.append(source[index + 1])
            index += 2
            continue
        if source[index] == "`":
            return "".join(characters), index + 1, True
        if source.startswith("${", index):
            return "".join(characters), index + 2, False
        characters.append(source[index])
        index += 1
    return "".join(characters), index, False


def _relative_looking(source: str, index: int) -> bool:
    closing_parenthesis = source.find(")", index)
    expression = source[index:] if closing_parenthesis == -1 else source[index:closing_parenthesis]
    return "./" in expression or "../" in expression


def _skip_javascript_ignored(source: str, index: int) -> int:
    while index < len(source):
        if source[index].isspace():
            index += 1
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = len(source) if newline == -1 else newline + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            return len(source) if end == -1 else _skip_javascript_ignored(source, end + 2)
        return index
    return index


def _skip_javascript_identifier(source: str, index: int) -> int:
    if index >= len(source) or not _javascript_identifier_character(source[index]):
        return index
    index += 1
    while index < len(source) and _javascript_identifier_character(source[index]):
        index += 1
    return index


def _javascript_identifier_character(character: str) -> bool:
    return character.isalnum() or character in {"_", "$"}


def _read_javascript_string(source: str, start: int) -> tuple[str, int, bool]:
    quote = source[start]
    characters: list[str] = []
    index = start + 1
    while index < len(source):
        character = source[index]
        if character == "\\" and index + 1 < len(source):
            characters.append(source[index + 1])
            index += 2
            continue
        if character == quote:
            return "".join(characters), index + 1, True
        characters.append(character)
        index += 1
    return "".join(characters), index, False


def _skip_javascript_string(source: str, start: int) -> int:
    _, end, _ = _read_javascript_string(source, start)
    return end


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
        for specifier in _import_specifiers(source):
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
