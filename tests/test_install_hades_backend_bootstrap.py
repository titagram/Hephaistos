from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"


def test_install_sh_accepts_backend_bootstrap_flags_and_invokes_cli():
    source = INSTALL_SH.read_text(encoding="utf-8")

    assert 'BRANCH="${HADES_INSTALL_BRANCH:-main}"' in source
    assert "default: main; env: HADES_INSTALL_BRANCH" in source
    assert "--backend-url" in source
    assert "--backend-project-id" in source
    assert "--backend-project-token" in source
    assert "run_backend_bootstrap" in source
    assert "backend bootstrap" in source
    assert "--project-token" in source


def test_install_ps1_accepts_backend_bootstrap_flags_and_invokes_cli():
    source = INSTALL_PS1.read_text(encoding="utf-8")

    assert '[string]$Branch = $(if ($env:HADES_INSTALL_BRANCH)' in source
    assert 'else { "main" }' in source
    assert "[string]$BackendUrl" in source
    assert "[string]$BackendProjectId" in source
    assert "[string]$BackendProjectToken" in source
    assert "Invoke-BackendBootstrap" in source
    assert "backend bootstrap" in source
    assert "--project-token" in source
