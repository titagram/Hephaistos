"""Installed-wheel acceptance for the engineering review authority and proxy."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ENGINE_BUNDLE = "hermes_cli/engineering_dist/hermes-engineering.mjs"
ENGINE_NOTICE = "hermes_cli/engineering_dist/NOTICE.qwen-code"
ENGINE_PROVENANCE = "hermes_cli/engineering_dist/UPSTREAM.qwen-code.json"
PACKAGE_DIRECTORIES = (
    "acp_adapter",
    "agent",
    "cron",
    "gateway",
    "hermes_cli",
    "locales",
    "optional-skills",
    "plugins",
    "providers",
    "skills",
    "third_party",
    "tools",
    "tui_gateway",
)
PACKAGE_MODULES = (
    "batch_runner.py",
    "cli.py",
    "hermes_bootstrap.py",
    "hermes_constants.py",
    "hermes_logging.py",
    "hermes_state.py",
    "hermes_time.py",
    "mcp_serve.py",
    "model_tools.py",
    "run_agent.py",
    "toolset_distributions.py",
    "toolsets.py",
    "trajectory_compressor.py",
    "utils.py",
)


def _build_wheel(output: Path) -> Path:
    source = output.parent / "source"
    source.mkdir()
    for name in ("LICENSE", "MANIFEST.in", "README.md", "pyproject.toml"):
        shutil.copy2(REPOSITORY_ROOT / name, source / name)
    for name in PACKAGE_MODULES:
        shutil.copy2(REPOSITORY_ROOT / name, source / name)
    for name in PACKAGE_DIRECTORIES:
        shutil.copytree(
            REPOSITORY_ROOT / name,
            source / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(output),
        ],
        cwd=source,
        capture_output=True,
        text=True,
        check=True,
    )
    wheels = list(output.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def _venv_executable(root: Path, name: str) -> Path:
    scripts = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return root / scripts / f"{name}{suffix}"


def _git(workspace: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Installed Wheel Test",
        "GIT_AUTHOR_EMAIL": "wheel@example.invalid",
        "GIT_COMMITTER_NAME": "Installed Wheel Test",
        "GIT_COMMITTER_EMAIL": "wheel@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    subprocess.run(["git", *args], cwd=workspace, env=env, check=True)


@pytest.mark.integration
@pytest.mark.skipif(
    os.name == "nt",
    reason="review authority requires kernel-authenticated Unix peers",
)
def test_wheel_review_engine_runs_without_source_tree_or_node_modules(
    tmp_path: Path,
) -> None:
    wheel = _build_wheel(tmp_path / "dist")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert ENGINE_BUNDLE in names
        assert ENGINE_NOTICE in names
        assert ENGINE_PROVENANCE in names
        assert any(
            name.endswith(".dist-info/licenses/third_party/qwen-code/LICENSE")
            for name in names
        )
        provenance = json.loads(archive.read(ENGINE_PROVENANCE))
        assert provenance["schemaVersion"] == 1
        assert provenance["repository"] == ("https://github.com/QwenLM/qwen-code.git")

    venv = tmp_path / "installed"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    python = _venv_executable(venv, "python")
    proxy = _venv_executable(venv, "hermes-review-engine")
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
        check=True,
    )
    assert not (venv / "node_modules").exists()

    workspace = tmp_path / "fixture-repository"
    workspace.mkdir()
    _git(workspace, "init", "-q", "-b", "main")
    (workspace / "feature.py").write_text(
        "def selected_value():\n    return 1\n",
        encoding="utf-8",
    )
    _git(workspace, "add", "feature.py")
    _git(workspace, "commit", "-qm", "base fixture")
    (workspace / "untracked.py").write_text(
        "UNTRACKED_VALUE = 2\n",
        encoding="utf-8",
    )

    probe = tmp_path / "installed-wheel-probe.py"
    probe.write_text(
        """
import json
import os
import subprocess
import sys
from pathlib import Path

from hermes_cli.engineering_review.authority import ReviewAuthority

workspace = Path(sys.argv[1]).resolve()
proxy = Path(sys.argv[2]).resolve()
session_id = "installed-wheel-acceptance"
env = {**os.environ, "HERMES_SESSION_ID": session_id}

with ReviewAuthority(
    workspace=workspace,
    target="local",
    effort="medium",
    session_id=session_id,
) as authority:
    start = subprocess.run(
        [str(proxy), "start", "--session-id", session_id],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    start_payload = json.loads(start.stdout)
    request = {
        "protocolVersion": 1,
        "requestId": "installed-capture",
        "command": "capture-target",
        "workspace": str(authority.run.workspace),
        "artifactRoot": str(authority.run.root),
        "input": {"kind": "ignored-by-authority"},
    }
    request_path = authority.run.atomic_artifact(
        "installed-request.json",
        json.dumps(request, separators=(",", ":")).encode(),
    )
    capture = subprocess.run(
        [
            str(proxy),
            "capture-target",
            str(request_path),
            "--session-id",
            session_id,
        ],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(capture.stdout)
    diff = (authority.run.root / "target.diff").read_text(encoding="utf-8")
    print(json.dumps({
        "start": start_payload,
        "capture": payload,
        "containsUntracked": "untracked.py" in diff,
    }, separators=(",", ":")))
""".lstrip(),
        encoding="utf-8",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "NODE_PATH", "NODE_OPTIONS"}
    }
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir(mode=0o700)
    env["HERMES_HOME"] = str(hermes_home)
    result = subprocess.run(
        [str(python), str(probe), str(workspace), str(proxy)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["capture"]["protocolVersion"] == 1
    assert payload["capture"]["status"] == "passed"
    assert payload["containsUntracked"] is True
    assert str(REPOSITORY_ROOT) not in result.stdout
