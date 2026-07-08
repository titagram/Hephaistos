"""Regression: install.sh must recover managed checkouts with diverged history."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None,
    reason="needs git and bash",
)


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def _extract_install_sh_pull_block() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    match = re.search(
        r"# Fetch only the target branch\..*?\n"
        r"            if \[ -n \"\$autostash_ref\" \]; then",
        text,
        re.DOTALL,
    )
    assert match is not None, "installer pull block not found"
    block = match.group(0)
    return block.rsplit('            if [ -n "$autostash_ref" ]; then', 1)[0]


def _make_diverged_checkout(tmp_path: Path) -> tuple[Path, str, str]:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    install_dir = tmp_path / "hermes-agent"

    _git(tmp_path, "init", "--bare", str(origin))
    _git(tmp_path, "clone", str(origin), str(seed))
    (seed / "f.txt").write_text("base\n", encoding="utf-8")
    _git(seed, "add", "f.txt")
    _git(seed, "commit", "-m", "base")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-u", "origin", "main")

    _git(tmp_path, "clone", "--branch", "main", str(origin), str(install_dir))

    (seed / "f.txt").write_text("remote\n", encoding="utf-8")
    _git(seed, "add", "f.txt")
    _git(seed, "commit", "-m", "remote")
    _git(seed, "push", "origin", "main")
    remote_head = _git(seed, "rev-parse", "HEAD").stdout.strip()

    (install_dir / "f.txt").write_text("local\n", encoding="utf-8")
    _git(install_dir, "add", "f.txt")
    _git(install_dir, "commit", "-m", "local")
    local_head = _git(install_dir, "rev-parse", "HEAD").stdout.strip()

    assert _git(install_dir, "pull", "--ff-only", "origin", "main", check=False).returncode != 0
    return install_dir, local_head, remote_head


@pytest.mark.live_system_guard_bypass
def test_install_sh_resets_diverged_managed_checkout_after_backup(tmp_path: Path) -> None:
    install_dir, local_head, remote_head = _make_diverged_checkout(tmp_path)
    block = _extract_install_sh_pull_block()
    script = (
        "set -e\n"
        'log_warn() { echo "WARN: $*"; }\n'
        f'INSTALL_DIR="{install_dir}"\n'
        'BRANCH="main"\n'
        'autostash_ref=""\n'
        f"cd \"$INSTALL_DIR\"\n"
        "run_pull() {\n"
        f"{block}"
        "}\n"
        "run_pull\n"
    )

    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "Fast-forward update failed" in result.stdout
    assert _git(install_dir, "rev-parse", "HEAD").stdout.strip() == remote_head

    backup_refs = _git(
        install_dir,
        "for-each-ref",
        "--format=%(refname:short) %(objectname)",
        "refs/heads/hades-install-backup",
    ).stdout.splitlines()
    assert len(backup_refs) == 1
    assert backup_refs[0].split()[1] == local_head


def test_install_ps1_resets_diverged_managed_checkout_after_backup() -> None:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "Fast-forward update failed; this managed checkout has diverged" in text
    assert "hades-install-backup/$backupLabel-" in text
    assert 'reset --hard "origin/$Branch"' in text
