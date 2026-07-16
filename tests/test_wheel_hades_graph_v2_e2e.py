"""Installed-artifact coverage for the packaged graph-v2 schema registry."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = REPO_ROOT / "contracts" / "hades" / "graph-v2"
SCHEMA_NAMES = frozenset({
    "artifact.schema.json",
    "bundle.schema.json",
    "chunk.schema.json",
    "dashboard-query.schema.json",
    "dashboard-response.schema.json",
    "verification-work.schema.json",
    "verification-result.schema.json",
    "graph-overlay.schema.json",
})
RESOURCE_PREFIX = "hermes_cli/hades_graph_v2/contracts/"


def _clean_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME"}
    }


@pytest.mark.integration
def test_installed_wheel_validates_v2_payload_without_source_checkout(
    tmp_path: Path,
) -> None:
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            str(REPO_ROOT),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=_clean_environment(),
        timeout=240,
    )
    assert build.returncode == 0, build.stderr
    wheels = tuple(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1

    with zipfile.ZipFile(wheels[0]) as archive:
        shipped = {
            name.removeprefix(RESOURCE_PREFIX)
            for name in archive.namelist()
            if name.startswith(RESOURCE_PREFIX) and name.endswith(".json")
        }
    assert shipped == SCHEMA_NAMES

    target = tmp_path / "installed"
    install = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--no-deps",
            "--target",
            str(target),
            str(wheels[0]),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=_clean_environment(),
        timeout=120,
    )
    assert install.returncode == 0, install.stderr

    payload_path = tmp_path / "bundle.json"
    canonicalization = json.loads(
        (CONTRACT_ROOT / "golden" / "canonicalization.json").read_text(encoding="utf-8")
    )
    payload_path.write_text(
        json.dumps(canonicalization["contract_examples"]["bundle"]),
        encoding="utf-8",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    probe = """
import json
import sys
from pathlib import Path

target = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(target))

import hermes_cli.hades_graph_v2 as graph_v2
from hermes_cli.hades_graph_contract import validate_schema

module_path = Path(graph_v2.__file__).resolve()
assert module_path.is_relative_to(target), (module_path, target)
payload = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
validate_schema("bundle.schema.json", payload)
"""
    runtime_env = _clean_environment()
    runtime_env["PYTHONPATH"] = str(target)
    run = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(target), str(payload_path)],
        cwd=outside,
        capture_output=True,
        text=True,
        env=runtime_env,
        timeout=120,
    )
    assert run.returncode == 0, f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"


@pytest.mark.integration
def test_built_sdist_contains_all_graph_v2_schema_resources(tmp_path: Path) -> None:
    sdist_dir = tmp_path / "sdist"
    build = subprocess.run(
        [
            sys.executable,
            "setup.py",
            "sdist",
            "--dist-dir",
            str(sdist_dir),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=_clean_environment(),
        timeout=240,
    )
    assert build.returncode == 0, build.stderr
    archives = tuple(sdist_dir.glob("*.tar.gz"))
    assert len(archives) == 1
    with tarfile.open(archives[0]) as archive:
        shipped = {
            Path(name).name
            for name in archive.getnames()
            if f"/{RESOURCE_PREFIX}" in name and name.endswith(".json")
        }
    assert shipped == SCHEMA_NAMES
