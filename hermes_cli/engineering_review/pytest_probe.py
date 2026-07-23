"""Structured pytest subprocess used by the engineering review engine.

This module is intentionally a subprocess boundary.  It keeps pytest and any
project plugins out of the long-lived Hermes process and writes exactly one
JSON document to stdout for the Node adapter to consume.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Sequence

import pytest


class ProbePlugin:
    """Collect structured pytest events without interpreting terminal prose."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files: set[str] = set()
        self.assertion_failures: list[str] = []
        self.non_call_failures: list[str] = []
        self.collection_failures: list[str] = []
        self.collection_error_files: set[str] = set()
        self.passed = 0
        self.skipped = 0
        self.internal_error = False
        self.interrupted = False

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        for item in session.items:
            path = Path(str(item.path)).resolve()
            try:
                relative = path.relative_to(self.root)
            except ValueError:
                continue
            self.files.add(relative.as_posix())

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed:
            self.collection_failures.append(report.nodeid)
            path = Path(report.nodeid.split("::", 1)[0])
            if not path.is_absolute():
                path = self.root / path
            try:
                relative = path.resolve().relative_to(self.root)
            except ValueError:
                return
            if relative.suffix == ".py":
                self.collection_error_files.add(relative.as_posix())

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.passed and report.when == "call":
            self.passed += 1
            return
        if report.skipped and report.when == "call":
            self.skipped += 1
            return
        if not report.failed:
            return
        if report.when != "call":
            self.non_call_failures.append(report.nodeid)
            return
        self.assertion_failures.append(report.nodeid)

    def pytest_internalerror(self, *args: object, **kwargs: object) -> None:
        self.internal_error = True

    def pytest_keyboard_interrupt(self, *args: object, **kwargs: object) -> None:
        self.interrupted = True


def _safe_file(root: Path, raw_file: str) -> Path:
    candidate = Path(raw_file)
    if candidate.is_absolute():
        raise ValueError("test file must be repository-relative")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("test file escapes the workspace") from exc
    return resolved


def _outcome(plugin: ProbePlugin) -> str:
    if plugin.internal_error:
        return "internal_error"
    if plugin.collection_failures:
        return "collection_or_import_error"
    if plugin.interrupted:
        return "interrupted"
    if plugin.non_call_failures:
        return "setup_or_teardown_error"
    if plugin.assertion_failures:
        return "assertion_failed"
    if plugin.passed > 0:
        return "passed"
    return "no_tests_executed"


def _run_pytest(root: Path, args: list[str], plugin: ProbePlugin) -> int:
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    previous_cwd = Path.cwd()
    try:
        os.chdir(root)
        sys.path.insert(0, str(root))
        with (
            contextlib.redirect_stdout(captured_stdout),
            contextlib.redirect_stderr(captured_stderr),
        ):
            return int(pytest.main(args, plugins=[plugin]))
    finally:
        if sys.path and sys.path[0] == str(root):
            sys.path.pop(0)
        os.chdir(previous_cwd)


def probe(command: str, root: Path, raw_file: str | None = None) -> dict[str, object]:
    canonical_root = root.resolve(strict=True)
    if not canonical_root.is_dir():
        raise ValueError("root must be a directory")
    plugin = ProbePlugin(canonical_root)
    args = [
        "--rootdir",
        str(canonical_root),
        "-q",
        "--capture=fd",
        "-p",
        "no:cacheprovider",
    ]
    if command == "collect":
        args.append("--collect-only")
    else:
        if raw_file is None:
            raise ValueError("run requires --file")
        args.append(str(_safe_file(canonical_root, raw_file)))
    exit_code = _run_pytest(canonical_root, args, plugin)
    outcome = _outcome(plugin)
    if command == "collect" and outcome == "no_tests_executed":
        outcome = "collected"
    result: dict[str, object] = {
        "command": command,
        "outcome": outcome,
        "pytestExitCode": exit_code,
        "files": sorted(plugin.files),
        "collectionErrors": sorted(plugin.collection_error_files),
    }
    if command == "run":
        result.update({
            "passed": plugin.passed,
            "failedAssertions": len(plugin.assertion_failures),
            "skipped": plugin.skipped,
        })
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("collect", "run"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = probe(args.command, args.root, args.file)
    except (OSError, ValueError) as exc:
        result = {
            "command": args.command,
            "outcome": "probe_error",
            "error": str(exc),
            "files": [],
        }
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
