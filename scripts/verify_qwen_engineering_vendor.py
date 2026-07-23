#!/usr/bin/env python3
"""Offline integrity and mutation-surface checks for the Qwen review slice."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = ROOT / "third_party" / "qwen-code"
MANIFEST_PATH = VENDOR_ROOT / "UPSTREAM.json"
BUNDLE_PATH = ROOT / "hermes_cli" / "engineering_dist" / "hermes-engineering.mjs"
BUNDLE_NOTICE_PATH = ROOT / "hermes_cli" / "engineering_dist" / "NOTICE.qwen-code"
PACKAGED_PROVENANCE_PATH = (
    ROOT / "hermes_cli" / "engineering_dist" / "UPSTREAM.qwen-code.json"
)
PRODUCTION_ROOTS = (
    ROOT / "packages" / "hermes-engineering" / "src",
    VENDOR_ROOT / "packages" / "cli" / "src" / "commands" / "review",
    VENDOR_ROOT / "packages" / "cli" / "src" / "services",
)
IMPORT_RE = re.compile(
    r"""(?:from\s*|import\s*\()\s*['"](?P<specifier>[^'"]+)['"]""",
    re.MULTILINE,
)


def _manifest() -> dict[str, object]:
    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
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


def verify_manifest_hashes() -> None:
    for record in _manifest_files(_manifest()):
        path = VENDOR_ROOT / record["path"]
        if not path.is_file():
            raise ValueError(f"manifested vendor file is missing: {record['path']}")
        actual = _sha256(path)
        if actual != record["sha256"]:
            raise ValueError(
                f"vendor hash mismatch for {record['path']}: "
                f"expected {record['sha256']}, got {actual}"
            )


def verify_apache_headers() -> None:
    for record in _manifest_files(_manifest()):
        if not record["path"].endswith((".ts", ".js")):
            continue
        prefix = (VENDOR_ROOT / record["path"]).read_text(encoding="utf-8")[:512]
        if (
            "Copyright 2026 Qwen Team" not in prefix
            or "SPDX-License-Identifier: Apache-2.0" not in prefix
        ):
            raise ValueError(
                f"vendor source has no Apache-2.0 header: {record['path']}"
            )


def verify_no_unmanifested_vendor_files() -> None:
    manifest = _manifest()
    expected = {record["path"] for record in _manifest_files(manifest)}
    expected.update({"NOTICE", "UPSTREAM.json"})
    actual = {
        path.relative_to(VENDOR_ROOT).as_posix()
        for path in VENDOR_ROOT.rglob("*")
        if path.is_file()
    }
    extra = sorted(actual - expected)
    missing = sorted(expected - actual)
    if extra or missing:
        raise ValueError(
            f"vendor tree differs from manifest: extra={extra}, missing={missing}"
        )


def verify_bundle_notice_and_hash() -> None:
    manifest = _manifest()
    bundle = manifest.get("hermesBundle")
    if (
        not isinstance(bundle, dict)
        or bundle.get("path") != "hermes_cli/engineering_dist/hermes-engineering.mjs"
        or not isinstance(bundle.get("sha256"), str)
    ):
        raise ValueError("UPSTREAM.json has no valid hermesBundle record")
    if not BUNDLE_PATH.is_file() or _sha256(BUNDLE_PATH) != bundle["sha256"]:
        raise ValueError("packaged engineering-review bundle hash mismatch")
    notice = BUNDLE_NOTICE_PATH.read_text(encoding="utf-8")
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
    if PACKAGED_PROVENANCE_PATH.read_bytes() != MANIFEST_PATH.read_bytes():
        raise ValueError("packaged Qwen provenance differs from UPSTREAM.json")


def _production_files() -> Iterable[Path]:
    for root in PRODUCTION_ROOTS:
        for path in root.rglob("*"):
            if (
                path.is_file()
                and path.suffix in {".ts", ".js", ".mjs"}
                and not path.name.endswith((".test.ts", ".spec.ts"))
            ):
                yield path


def verify_no_forbidden_imports(
    forbidden: tuple[str, ...] = ("provider", "telemetry", "submit"),
) -> None:
    needles = tuple(item.casefold() for item in forbidden)
    for path in _production_files():
        source = path.read_text(encoding="utf-8")
        for match in IMPORT_RE.finditer(source):
            specifier = match.group("specifier").casefold()
            segments = re.split(r"[/_.-]+", specifier)
            if any(needle in segments for needle in needles):
                raise ValueError(
                    f"forbidden import {match.group('specifier')!r} in "
                    f"{path.relative_to(ROOT)}"
                )


def verify_no_remote_mutation_strings(
    forbidden: tuple[str, ...] = ("gh pr review", "git push", "git merge"),
) -> None:
    paths = [BUNDLE_PATH, *_production_files()]
    for path in paths:
        folded = path.read_text(encoding="utf-8").casefold()
        for command in forbidden:
            if command.casefold() in folded:
                raise ValueError(
                    f"remote mutation command {command!r} found in "
                    f"{path.relative_to(ROOT)}"
                )


def verify() -> None:
    verify_manifest_hashes()
    verify_apache_headers()
    verify_no_unmanifested_vendor_files()
    verify_bundle_notice_and_hash()
    verify_no_forbidden_imports()
    verify_no_remote_mutation_strings()


def main() -> int:
    verify()
    print("Qwen engineering vendor verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
