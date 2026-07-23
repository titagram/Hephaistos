from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def write_pytest_fixture(root: Path) -> None:
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_assert.py").write_text(
        "def test_assertion():\n    assert False\n",
        encoding="utf-8",
    )
    (tests / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n",
        encoding="utf-8",
    )
    (tests / "test_import_error.py").write_text(
        "raise ImportError('fixture import failed')\n",
        encoding="utf-8",
    )


def run_probe(root: Path, command: str, file: str | None = None) -> dict[str, object]:
    args = [
        sys.executable,
        "-m",
        "hermes_cli.engineering_review.pytest_probe",
        command,
        "--root",
        str(root),
    ]
    if file is not None:
        args.extend(["--file", file])
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPOSITORY_ROOT)
    completed = subprocess.run(
        args,
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(completed.stdout)


def test_probe_distinguishes_assertion_from_import_error(tmp_path: Path) -> None:
    write_pytest_fixture(tmp_path)

    asserted = run_probe(tmp_path, "run", "tests/test_assert.py")
    imported = run_probe(tmp_path, "run", "tests/test_import_error.py")

    assert asserted["outcome"] == "assertion_failed"
    assert imported["outcome"] == "collection_or_import_error"


def test_collect_reports_canonical_relative_files(tmp_path: Path) -> None:
    write_pytest_fixture(tmp_path)

    result = run_probe(tmp_path, "collect")

    assert result["files"] == ["tests/test_assert.py", "tests/test_ok.py"]
