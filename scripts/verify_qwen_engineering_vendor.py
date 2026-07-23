#!/usr/bin/env python3
"""Offline integrity and mutation-surface checks for the Qwen review slice."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
TYPESCRIPT_ROOTS = (
    Path("packages/hermes-engineering/src"),
    Path("third_party/qwen-code/packages/cli/src/commands/review"),
    Path("third_party/qwen-code/packages/cli/src/services"),
)
PYTHON_REVIEW_SURFACE = (
    Path("hermes_cli/engineering_review"),
    Path("hermes_cli/subcommands/review.py"),
    Path("agent/review_evidence.py"),
)


def _vendor_root(root: Path) -> Path:
    return root / "third_party" / "qwen-code"


def _manifest(root: Path = ROOT) -> dict[str, object]:
    value = json.loads(
        (_vendor_root(root) / "UPSTREAM.json").read_text(encoding="utf-8")
    )
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise ValueError("UPSTREAM.json is not a supported provenance manifest")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_files(manifest: dict[str, object]) -> list[dict[str, str]]:
    records = manifest.get("files")
    if not isinstance(records, list):
        raise ValueError("UPSTREAM.json files must be an array")
    result: list[dict[str, str]] = []
    for record in records:
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("path"), str)
            or not isinstance(record.get("sha256"), str)
        ):
            raise ValueError("UPSTREAM.json contains an invalid file record")
        result.append({"path": record["path"], "sha256": record["sha256"]})
    return result


def verify_manifest_hashes(root: Path = ROOT) -> None:
    vendor_root = _vendor_root(root)
    for record in _manifest_files(_manifest(root)):
        path = vendor_root / record["path"]
        if not path.is_file():
            raise ValueError(f"manifested vendor file is missing: {record['path']}")
        actual = _sha256(path)
        if actual != record["sha256"]:
            raise ValueError(
                f"vendor hash mismatch for {record['path']}: "
                f"expected {record['sha256']}, got {actual}"
            )


def verify_apache_headers(root: Path = ROOT) -> None:
    vendor_root = _vendor_root(root)
    for record in _manifest_files(_manifest(root)):
        if not record["path"].endswith((".ts", ".js")):
            continue
        prefix = (vendor_root / record["path"]).read_text(encoding="utf-8")[:512]
        if (
            "Copyright 2026 Qwen Team" not in prefix
            or "SPDX-License-Identifier: Apache-2.0" not in prefix
        ):
            raise ValueError(
                f"vendor source has no Apache-2.0 header: {record['path']}"
            )


def verify_no_unmanifested_vendor_files(root: Path = ROOT) -> None:
    vendor_root = _vendor_root(root)
    manifest = _manifest(root)
    expected = {record["path"] for record in _manifest_files(manifest)}
    expected.update({"NOTICE", "UPSTREAM.json"})
    actual = {
        path.relative_to(vendor_root).as_posix()
        for path in vendor_root.rglob("*")
        if path.is_file()
    }
    extra = sorted(actual - expected)
    missing = sorted(expected - actual)
    if extra or missing:
        raise ValueError(
            f"vendor tree differs from manifest: extra={extra}, missing={missing}"
        )


def verify_bundle_notice_and_hash(root: Path = ROOT) -> None:
    manifest = _manifest(root)
    bundle_path = root / "hermes_cli/engineering_dist/hermes-engineering.mjs"
    notice_path = root / "hermes_cli/engineering_dist/NOTICE.qwen-code"
    provenance_path = root / "hermes_cli/engineering_dist/UPSTREAM.qwen-code.json"
    bundle = manifest.get("hermesBundle")
    if (
        not isinstance(bundle, dict)
        or bundle.get("path") != "hermes_cli/engineering_dist/hermes-engineering.mjs"
        or not isinstance(bundle.get("sha256"), str)
    ):
        raise ValueError("UPSTREAM.json has no valid hermesBundle record")
    if not bundle_path.is_file() or _sha256(bundle_path) != bundle["sha256"]:
        raise ValueError("packaged engineering-review bundle hash mismatch")
    notice = notice_path.read_text(encoding="utf-8")
    commit = manifest.get("upstreamCommit")
    repository = manifest.get("repository")
    if (
        not isinstance(commit, str)
        or commit not in notice
        or not isinstance(repository, str)
        or repository not in notice
        or "Apache-2.0" not in notice
    ):
        raise ValueError("packaged bundle notice does not match Qwen provenance")
    if (
        provenance_path.read_bytes()
        != (_vendor_root(root) / "UPSTREAM.json").read_bytes()
    ):
        raise ValueError("packaged Qwen provenance differs from UPSTREAM.json")


def _typescript_files(root: Path) -> Iterable[Path]:
    for relative_root in TYPESCRIPT_ROOTS:
        source_root = root / relative_root
        for path in source_root.rglob("*"):
            if (
                path.is_file()
                and path.suffix
                in {".ts", ".tsx", ".mts", ".cts", ".js", ".mjs", ".cjs"}
                and ".test." not in path.name
                and ".spec." not in path.name
            ):
                yield path


def _python_review_files(root: Path) -> Iterable[Path]:
    for relative in PYTHON_REVIEW_SURFACE:
        path = root / relative
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from (
                candidate for candidate in path.rglob("*.py") if candidate.is_file()
            )


def _typescript_specifiers(root: Path, path: Path) -> tuple[str, ...]:
    node = shutil.which("node")
    if node is None:
        raise ValueError("Node.js is required for TypeScript AST verification")
    # The parser is part of this verifier, not part of the repository copy
    # being inspected.  Keeping it anchored beside this script also lets the
    # negative-copy tests use the already installed TypeScript compiler.
    helper = Path(__file__).with_name("qwen_engineering_imports.mjs")
    completed = subprocess.run(
        [node, str(helper), path.relative_to(root).as_posix()],
        input=path.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise ValueError(
            f"TypeScript AST verification failed for {path.relative_to(root)}: "
            f"{completed.stderr.strip()}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("TypeScript AST helper returned invalid JSON") from exc
    specifiers = payload.get("specifiers") if isinstance(payload, dict) else None
    if not isinstance(specifiers, list) or not all(
        isinstance(item, str) for item in specifiers
    ):
        raise ValueError("TypeScript AST helper returned invalid specifiers")
    return tuple(specifiers)


def verify_no_forbidden_imports(
    forbidden: tuple[str, ...] = ("provider", "telemetry", "submit"),
    root: Path = ROOT,
) -> None:
    needles = tuple(item.casefold() for item in forbidden)
    separators = "/_.-"
    for path in _typescript_files(root):
        for raw_specifier in _typescript_specifiers(root, path):
            specifier = raw_specifier.casefold()
            segments = [specifier]
            for separator in separators:
                segments = [
                    part for segment in segments for part in segment.split(separator)
                ]
            if any(needle in segments for needle in needles):
                raise ValueError(
                    f"forbidden import {raw_specifier!r} in {path.relative_to(root)}"
                )


def _python_string_literals(path: Path) -> Iterable[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise ValueError(f"could not parse Python review surface: {path}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value
        elif isinstance(node, (ast.List, ast.Tuple)) and all(
            isinstance(item, ast.Constant) and isinstance(item.value, str)
            for item in node.elts
        ):
            yield " ".join(str(item.value) for item in node.elts)


def verify_no_remote_mutation_strings(
    forbidden: tuple[str, ...] = (
        "gh pr review",
        "gh pr comment",
        "git push",
        "git merge",
    ),
    root: Path = ROOT,
) -> None:
    text_paths = [
        root / "hermes_cli/engineering_dist/hermes-engineering.mjs",
        *_typescript_files(root),
    ]
    for path in text_paths:
        values = (path.read_text(encoding="utf-8"),)
        for value in values:
            folded = value.casefold()
            for command in forbidden:
                if command.casefold() in folded:
                    raise ValueError(
                        f"remote mutation command {command!r} found in "
                        f"{path.relative_to(root)}"
                    )
    for path in _python_review_files(root):
        for value in _python_string_literals(path):
            folded = value.casefold()
            for command in forbidden:
                if command.casefold() in folded:
                    raise ValueError(
                        f"remote mutation command {command!r} found in "
                        f"{path.relative_to(root)}"
                    )


def verify(root: Path = ROOT) -> None:
    root = root.resolve()
    verify_manifest_hashes(root)
    verify_apache_headers(root)
    verify_no_unmanifested_vendor_files(root)
    verify_bundle_notice_and_hash(root)
    verify_no_forbidden_imports(root=root)
    verify_no_remote_mutation_strings(root=root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository copy to verify (used by negative acceptance tests)",
    )
    args = parser.parse_args(argv)
    verify(args.root)
    print("Qwen engineering vendor verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
