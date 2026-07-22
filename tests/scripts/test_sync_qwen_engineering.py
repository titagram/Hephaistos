"""Regression tests for the deterministic Qwen review-engine extractor."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from scripts.sync_qwen_engineering import (
    SyncError,
    sync,
    validate_allowlist,
    validate_typescript_header,
)


HEADER = "// SPDX-License-Identifier: Apache-2.0\n"
ALLOWLIST = Path(__file__).parents[2] / "scripts" / "qwen_engineering_allowlist.json"


def run_git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def git_head(repository: Path) -> str:
    return run_git(repository, "rev-parse", "HEAD")


def make_git_upstream(tmp_path: Path, files: dict[str, str]) -> Path:
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    run_git(upstream, "init")
    run_git(upstream, "config", "user.email", "test@example.invalid")
    run_git(upstream, "config", "user.name", "Test")
    for name, contents in files.items():
        path = upstream / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    run_git(upstream, "add", ".")
    run_git(upstream, "commit", "-m", "fixture")
    return upstream


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sync_copies_only_allowlisted_files_and_hashes_them(
    tmp_path: Path,
) -> None:
    upstream = make_git_upstream(
        tmp_path,
        {
            "packages/cli/src/commands/review/lib/diff-plan.ts": HEADER
            + "export const x = 1;\n",
            "packages/cli/src/commands/review/submit.ts": HEADER
            + "export const forbidden = 1;\n",
            "LICENSE": "Apache License Version 2.0\n",
        },
    )
    out = tmp_path / "out"
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(
        '{"repository":"https://github.com/QwenLM/qwen-code.git",'
        '"files":["LICENSE",'
        '"packages/cli/src/commands/review/lib/diff-plan.ts"]}'
    )

    manifest = sync(upstream, out, git_head(upstream), allowlist)

    copied = out / "packages/cli/src/commands/review/lib/diff-plan.ts"
    assert copied.is_file()
    assert not (out / "packages/cli/src/commands/review/submit.ts").exists()
    assert manifest["upstreamCommit"] == git_head(upstream)
    assert manifest["files"][0]["sha256"] == sha256_file(out / "LICENSE")
    assert manifest["files"][1]["sha256"] == sha256_file(copied)


def test_sync_rejects_path_traversal_and_missing_apache_header(tmp_path: Path) -> None:
    with pytest.raises(SyncError, match="relative POSIX path"):
        validate_allowlist(["../outside.ts"])
    with pytest.raises(SyncError, match="SPDX-License-Identifier: Apache-2.0"):
        validate_typescript_header("export const x = 1")


def test_sync_requires_declared_import_shims_and_dependencies(tmp_path: Path) -> None:
    upstream = make_git_upstream(
        tmp_path,
        {
            "LICENSE": "Apache License Version 2.0\n",
            "src/review.ts": HEADER
            + "import { write } from './outside.js';\n"
            + "import type { Argv } from 'yargs';\n"
            + "export const review = write as unknown as Argv;\n",
        },
    )
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(
        """{
  "repository": "https://github.com/QwenLM/qwen-code.git",
  "files": ["LICENSE", "src/review.ts"],
  "importExceptions": {
    "relative": [{
      "source": "src/outside.ts",
      "destination": "packages/hermes-engineering/src/shims/outside.ts"
    }],
    "packages": [{"specifier": "yargs", "dependency": "yargs@17.7.2"}]
  }
}
"""
    )

    manifest = sync(upstream, tmp_path / "out", git_head(upstream), allowlist)

    assert manifest["hermesShims"] == [
        {
            "destination": "packages/hermes-engineering/src/shims/outside.ts",
            "source": "src/outside.ts",
        }
    ]
    allowlist.write_text(
        allowlist.read_text().replace(
            '{"specifier": "yargs", "dependency": "yargs@17.7.2"}', ""
        ).replace('    "packages": []', '    "packages": []')
    )
    with pytest.raises(SyncError, match="package import 'yargs'"):
        sync(upstream, tmp_path / "rejected", git_head(upstream), allowlist)
