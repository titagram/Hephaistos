from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"


def test_install_sh_has_no_backend_bootstrap_interface():
    source = INSTALL_SH.read_text(encoding="utf-8")

    assert 'BRANCH="${HADES_INSTALL_BRANCH:-main}"' in source
    assert "default: main; env: HADES_INSTALL_BRANCH" in source
    for forbidden in (
        "--backend-url",
        "--backend-project-id",
        "--backend-project-token",
        "--project-token",
        "backend bootstrap",
        "run_backend_bootstrap",
    ):
        assert forbidden not in source


def test_install_ps1_has_no_backend_bootstrap_interface():
    source = INSTALL_PS1.read_text(encoding="utf-8")

    assert "[string]$Branch = $(if ($env:HADES_INSTALL_BRANCH)" in source
    assert 'else { "main" }' in source
    for forbidden in (
        "[string]$BackendUrl",
        "[string]$BackendProjectId",
        "[string]$BackendProjectToken",
        "--project-token",
        "backend bootstrap",
        "Invoke-BackendBootstrap",
    ):
        assert forbidden not in source
