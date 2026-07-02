from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SRC = REPO_ROOT / "apps" / "bootstrap-installer" / "src"


def test_bootstrap_installer_uses_hades_public_copy():
    welcome = (BOOTSTRAP_SRC / "routes" / "welcome.tsx").read_text(encoding="utf-8")
    success = (BOOTSTRAP_SRC / "routes" / "success.tsx").read_text(encoding="utf-8")
    app_shell = (BOOTSTRAP_SRC / "app.tsx").read_text(encoding="utf-8")

    assert "HADES AGENT" in welcome
    assert "Install Hades" in welcome
    assert "Hades is ready" in success
    assert "hades desktop" in success
    assert "Launch Hades" in success
    assert "Hades Setup" in app_shell

    forbidden_visible_copy = {
        "HERMES AGENT",
        "Install Hermes",
        "Hermes is ready",
        "hermes desktop",
        "Launch Hermes",
        "Hermes Setup",
    }
    public_sources = "\n".join([welcome, success, app_shell])

    for phrase in forbidden_visible_copy:
        assert phrase not in public_sources
