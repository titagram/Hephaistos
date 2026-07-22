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
    """Return module specifiers from a conservative TypeScript token stream."""

    tokens = _typescript_tokens(source)
    specifiers: list[str] = []
    for index, token in enumerate(tokens):
        kind, value = token
        if kind != "identifier":
            continue
        if value == "import":
            specifiers.extend(_import_specifier_from_tokens(tokens, index + 1))
        elif value == "export":
            specifiers.extend(_export_specifier_from_tokens(tokens, index + 1))
    return specifiers


def _typescript_tokens(source: str) -> list[tuple[str, str]]:
    tokens, _ = _tokenize_typescript(source, 0)
    return tokens


def _tokenize_typescript(
    source: str, index: int, *, stop_at_closing_brace: bool = False
) -> tuple[list[tuple[str, str]], int]:
    """Tokenize code, skipping comments and recursively lexing `${...}` code."""

    tokens: list[tuple[str, str]] = []
    nested_braces = 0
    while index < len(source):
        if source.startswith("//", index) or source.startswith("/*", index):
            index = _skip_typescript_ignored(source, index)
            continue
        if source[index] == "/" and _regular_expression_can_start(tokens):
            _, index, closed = _read_typescript_regular_expression(source, index)
            tokens.append(("regular_expression" if closed else "unterminated_regexp", ""))
            continue
        skipped = _skip_typescript_ignored(source, index)
        if skipped != index:
            index = skipped
            continue
        if index >= len(source):
            break
        character = source[index]
        if stop_at_closing_brace and character == "}":
            if nested_braces == 0:
                return tokens, index + 1
            nested_braces -= 1
            tokens.append(("punctuation", character))
            index += 1
            continue
        if stop_at_closing_brace and character == "{":
            nested_braces += 1
        if character in {"'", '"'}:
            value, index, closed = _read_typescript_string(source, index)
            tokens.append(("string" if closed else "unterminated_string", value))
            continue
        if character == "`":
            template_tokens, index = _tokenize_typescript_template(source, index)
            tokens.extend(template_tokens)
            continue
        if _typescript_identifier_character(character):
            end = index + 1
            while end < len(source) and _typescript_identifier_character(source[end]):
                end += 1
            tokens.append(("identifier", source[index:end]))
            index = end
            continue
        tokens.append(("punctuation", character))
        index += 1
    return tokens, index


def _tokenize_typescript_template(source: str, index: int) -> tuple[list[tuple[str, str]], int]:
    """Emit a template token and recurse into every interpolation expression."""

    characters: list[str] = []
    nested_tokens: list[tuple[str, str]] = []
    literal = True
    index += 1
    while index < len(source):
        if source[index] == "\\" and index + 1 < len(source):
            characters.append(source[index + 1])
            index += 2
            continue
        if source[index] == "`":
            kind = "template" if literal else "template_expression"
            return [(kind, "".join(characters)), *nested_tokens], index + 1
        if source.startswith("${", index):
            literal = False
            expression_tokens, index = _tokenize_typescript(
                source, index + 2, stop_at_closing_brace=True
            )
            nested_tokens.extend(expression_tokens)
            continue
        characters.append(source[index])
        index += 1
    return [("template_expression", "".join(characters)), *nested_tokens], index


def _import_specifier_from_tokens(tokens: list[tuple[str, str]], start: int) -> list[str]:
    if start >= len(tokens):
        return []
    kind, value = tokens[start]
    if kind in {"string", "unterminated_string", "template", "template_expression"}:
        return _literal_module_specifier(tokens, start, start + 1)
    if kind == "punctuation" and value == "(":
        return _literal_module_specifier(
            tokens, start + 1, _matching_token(tokens, start, "(", ")")
        )

    end = _statement_end(tokens, start)
    for index in range(start, end - 2):
        if tokens[index] != ("punctuation", "="):
            continue
        if tokens[index + 1] != ("identifier", "require"):
            continue
        if tokens[index + 2] != ("punctuation", "("):
            continue
        return _literal_module_specifier(
            tokens, index + 3, _matching_token(tokens, index + 2, "(", ")")
        )
    for index in range(start, end - 1):
        if tokens[index] == ("identifier", "from"):
            return _literal_module_specifier(tokens, index + 1, end)
    return []


def _export_specifier_from_tokens(tokens: list[tuple[str, str]], start: int) -> list[str]:
    end = _statement_end(tokens, start)
    for index in range(start, end - 1):
        if tokens[index] == ("identifier", "from"):
            return _literal_module_specifier(tokens, index + 1, end)
    return []


def _literal_module_specifier(
    tokens: list[tuple[str, str]], start: int, end: int
) -> list[str]:
    if start < end:
        kind, value = tokens[start]
        if kind in {"string", "template"}:
            return [value]
        if kind in {"unterminated_string", "template_expression"} and value.startswith("."):
            raise SyncError("unable to safely classify relative import expression")
    if any(
        kind in {"string", "unterminated_string", "template", "template_expression"}
        and value.startswith(".")
        for kind, value in tokens[start:end]
    ):
        raise SyncError("unable to safely classify relative import expression")
    return []


def _matching_token(
    tokens: list[tuple[str, str]], start: int, opening: str, closing: str
) -> int:
    depth = 0
    for index in range(start, len(tokens)):
        if tokens[index] == ("punctuation", opening):
            depth += 1
        elif tokens[index] == ("punctuation", closing):
            depth -= 1
            if depth == 0:
                return index
    return len(tokens)


def _statement_end(tokens: list[tuple[str, str]], start: int) -> int:
    for index in range(start, len(tokens)):
        if tokens[index] == ("punctuation", ";"):
            return index
    return len(tokens)


def _skip_typescript_ignored(source: str, index: int) -> int:
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
            return len(source) if end == -1 else _skip_typescript_ignored(source, end + 2)
        return index
    return index


def _typescript_identifier_character(character: str) -> bool:
    return character.isalnum() or character in {"_", "$"}


def _regular_expression_can_start(tokens: list[tuple[str, str]]) -> bool:
    if not tokens:
        return True
    kind, value = tokens[-1]
    return kind == "punctuation" and value in {"=", "(", "[", "{", ",", ":", "?", ";"}


def _read_typescript_string(source: str, start: int) -> tuple[str, int, bool]:
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


def _read_typescript_regular_expression(source: str, start: int) -> tuple[str, int, bool]:
    """Read a regex literal, preserving escapes and character-class boundaries."""

    index = start + 1
    character_class = False
    while index < len(source):
        character = source[index]
        if character == "\\" and index + 1 < len(source):
            index += 2
            continue
        if character == "[":
            character_class = True
            index += 1
            continue
        if character == "]":
            character_class = False
            index += 1
            continue
        if character == "/" and not character_class:
            index += 1
            while index < len(source) and _typescript_identifier_character(source[index]):
                index += 1
            return "", index, True
        if character in {"\n", "\r"}:
            return "", index, False
        index += 1
    return "", index, False


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
