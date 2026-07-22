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
    with pytest.raises(SyncError, match="SPDX-License-Identifier: Apache-2.0"):
        validate_typescript_header(
            "export const license = 'SPDX-License-Identifier: Apache-2.0';\n"
        )
    with pytest.raises(SyncError, match="SPDX-License-Identifier: Apache-2.0"):
        validate_typescript_header(
            "export const x = 1;\n// SPDX-License-Identifier: Apache-2.0\n"
        )


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


def test_sync_rejects_undeclared_literal_dynamic_relative_import(tmp_path: Path) -> None:
    upstream = make_git_upstream(
        tmp_path,
        {
            "LICENSE": "Apache License Version 2.0\n",
            "src/review.ts": HEADER
            + "export const load = () => import('./outside.js');\n",
        },
    )
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(
        '{"repository":"https://github.com/QwenLM/qwen-code.git",'
        '"files":["LICENSE","src/review.ts"]}'
    )

    with pytest.raises(SyncError, match="unallowlisted upstream file src/outside.ts"):
        sync(upstream, tmp_path / "out", git_head(upstream), allowlist)


def test_sync_accepts_declared_package_import_type(tmp_path: Path) -> None:
    upstream = make_git_upstream(
        tmp_path,
        {
            "LICENSE": "Apache License Version 2.0\n",
            "src/review.ts": HEADER
            + "export type Parsed = import('yargs').Argv;\n",
        },
    )
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(
        """{
  "repository": "https://github.com/QwenLM/qwen-code.git",
  "files": ["LICENSE", "src/review.ts"],
  "importExceptions": {
    "relative": [],
    "packages": [{"specifier": "yargs", "dependency": "yargs@17.7.2"}]
  }
}
"""
    )

    sync(upstream, tmp_path / "out", git_head(upstream), allowlist)


@pytest.mark.parametrize(
    ("statement", "error"),
    [
        (
            "export const load = () => import /* comment */ ('./outside.js');\n",
            "unallowlisted upstream file src/outside.ts",
        ),
        (
            "export const message = `${import('./outside.js')}`;\n",
            "unallowlisted upstream file src/outside.ts",
        ),
        (
            "export const load = () => import(`./outside.js`);\n",
            "unallowlisted upstream file src/outside.ts",
        ),
        (
            "export const load = () => import('./outside.js', { with: { type: 'json' } });\n",
            "unallowlisted upstream file src/outside.ts",
        ),
        (
            "import outside = require('./outside.js');\n",
            "unallowlisted upstream file src/outside.ts",
        ),
        (
            "import { outside } from /* comment */ './outside.js';\n",
            "unallowlisted upstream file src/outside.ts",
        ),
        (
            "export { outside } from /* gap */ './outside.js';\n",
            "unallowlisted upstream file src/outside.ts",
        ),
        (
            "export * from /* gap */ './outside.js';\n",
            "unallowlisted upstream file src/outside.ts",
        ),
        (
            "import type outside = require('./outside.js');\n",
            "unallowlisted upstream file src/outside.ts",
        ),
        (
            "type Outside = import('./outside.js').Outside;\n",
            "unallowlisted upstream file src/outside.ts",
        ),
    ],
    ids=(
        "dynamic-comments",
        "template-expression",
        "dynamic-template-literal",
        "dynamic-import-attributes",
        "import-require",
        "static-comments",
        "export-named-comments",
        "export-star-comments",
        "import-type-require",
        "import-type-relative",
    ),
)
def test_sync_rejects_undeclared_relative_import_syntax(
    tmp_path: Path, statement: str, error: str
) -> None:
    upstream = make_git_upstream(
        tmp_path,
        {
            "LICENSE": "Apache License Version 2.0\n",
            "src/review.ts": HEADER + statement,
        },
    )
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(
        '{"repository":"https://github.com/QwenLM/qwen-code.git",'
        '"files":["LICENSE","src/review.ts"]}'
    )

    with pytest.raises(SyncError, match=error):
        sync(upstream, tmp_path / "out", git_head(upstream), allowlist)


@pytest.mark.parametrize(
    "statement",
    [
        "const matcher = /[/*]/; import('./outside.js');\n",
        'export const text = `${/[}]/.test(x) ? import("./outside.js") : ""}`;\n',
        "const matcher = () => /[/*]/; import('./outside.js');\n",
        "const matcher = !/[/*]/.test(value); import('./outside.js');\n",
        "function load() { return /[/*]/.test(value) ? import('./outside.js') : null; }\n",
        "function fail() { throw /[/*]/.test(value) ? import('./outside.js') : Error(); }\n",
        "const matcher = /['\"]/; import('./outside.js');\n",
    ],
    ids=(
        "regex-character-class",
        "regex-in-template-expression",
        "regex-after-arrow",
        "regex-after-unary",
        "regex-after-return",
        "regex-after-throw",
        "quote-containing-regex",
    ),
)
def test_sync_rejects_import_after_regular_expression(tmp_path: Path, statement: str) -> None:
    upstream = make_git_upstream(
        tmp_path,
        {
            "LICENSE": "Apache License Version 2.0\n",
            "src/review.ts": HEADER + statement,
        },
    )
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(
        '{"repository":"https://github.com/QwenLM/qwen-code.git",'
        '"files":["LICENSE","src/review.ts"]}'
    )

    with pytest.raises(SyncError, match="unallowlisted upstream file src/outside.ts"):
        sync(upstream, tmp_path / "out", git_head(upstream), allowlist)


def test_sync_accepts_division_before_allowlisted_dynamic_import(tmp_path: Path) -> None:
    upstream = make_git_upstream(
        tmp_path,
        {
            "LICENSE": "Apache License Version 2.0\n",
            "src/review.ts": HEADER
            + "const ratio = left / right;\n"
            + "export const load = () => import('./allowed.js');\n",
            "src/allowed.ts": HEADER + "export const allowed = true;\n",
        },
    )
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(
        '{"repository":"https://github.com/QwenLM/qwen-code.git",'
        '"files":["LICENSE","src/review.ts","src/allowed.ts"]}'
    )

    sync(upstream, tmp_path / "out", git_head(upstream), allowlist)


def test_sync_ignores_import_text_in_ordinary_strings_and_comments(tmp_path: Path) -> None:
    upstream = make_git_upstream(
        tmp_path,
        {
            "LICENSE": "Apache License Version 2.0\n",
            "src/review.ts": HEADER
            + 'const fake = "import(\'./outside.js\')";\n'
            + "// import('./outside.js')\n"
            + "/* export * from './outside.js'; */\n",
        },
    )
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(
        '{"repository":"https://github.com/QwenLM/qwen-code.git",'
        '"files":["LICENSE","src/review.ts"]}'
    )

    sync(upstream, tmp_path / "out", git_head(upstream), allowlist)


def test_sync_fails_closed_when_typescript_cannot_parse_source(tmp_path: Path) -> None:
    upstream = make_git_upstream(
        tmp_path,
        {
            "LICENSE": "Apache License Version 2.0\n",
            "src/review.ts": HEADER + "const = ;\n",
        },
    )
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(
        '{"repository":"https://github.com/QwenLM/qwen-code.git",'
        '"files":["LICENSE","src/review.ts"]}'
    )

    with pytest.raises(SyncError, match="TypeScript import helper failed"):
        sync(upstream, tmp_path / "out", git_head(upstream), allowlist)


def test_resync_removes_only_previously_manifested_files(tmp_path: Path) -> None:
    upstream = make_git_upstream(
        tmp_path,
        {
            "LICENSE": "Apache License Version 2.0\n",
            "src/keep.ts": HEADER + "export const keep = true;\n",
            "src/remove.ts": HEADER + "export const remove = true;\n",
        },
    )
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(
        '{"repository":"https://github.com/QwenLM/qwen-code.git",'
        '"files":["LICENSE","src/keep.ts","src/remove.ts"]}'
    )
    destination = tmp_path / "out"
    sync(upstream, destination, git_head(upstream), allowlist)
    unmanaged = destination / "operator-note.txt"
    unmanaged.write_text("do not delete\n")
    allowlist.write_text(
        '{"repository":"https://github.com/QwenLM/qwen-code.git",'
        '"files":["LICENSE","src/keep.ts"]}'
    )

    sync(upstream, destination, git_head(upstream), allowlist)

    assert not (destination / "src/remove.ts").exists()
    assert unmanaged.read_text() == "do not delete\n"
