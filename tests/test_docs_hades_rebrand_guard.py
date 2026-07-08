import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

REMOVED_LOCALIZED_DOC_PATHS = [
    "README.es.md",
    "README.zh-CN.md",
    "README.ur-pk.md",
    "CONTRIBUTING.es.md",
    "SECURITY.es.md",
    "website/i18n",
]

HIGH_SIGNAL_DOCS = [
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "agent/prompt_builder.py",
    "docker/SOUL.md",
    "docs/ARCHITECTURE.md",
    "docs/CODING_STYLE.md",
    "docs/RUNTIME.md",
    "docs/indexes/DATA_MODEL.md",
    "docs/indexes/DEPENDENCIES.md",
    "docs/indexes/SECURITY.md",
    "hades_agent.egg-info/PKG-INFO",
    "scripts/build_model_catalog.py",
    "scripts/install.ps1",
    "scripts/install.sh",
    "website/README.md",
    "website/docusaurus.config.ts",
    "website/sidebars.ts",
    "website/docs/index.mdx",
    "website/docs/getting-started/quickstart.md",
    "website/docs/getting-started/installation.md",
    "website/docs/getting-started/learning-path.md",
    "website/docs/getting-started/nix-setup.md",
    "website/docs/getting-started/platform-support.md",
    "website/docs/getting-started/termux.md",
    "website/docs/getting-started/updating.md",
    "website/docs/getting-started/_category_.json",
    "website/docs/user-guide/windows-wsl-quickstart.md",
    "website/docs/user-guide/windows-native.md",
    "website/docs/user-guide/configuration.md",
    "website/docs/user-guide/profiles.md",
    "website/docs/user-guide/profile-distributions.md",
    "website/docs/user-guide/multi-profile-gateways.md",
    "website/docs/user-guide/sessions.md",
    "website/docs/reference/environment-variables.md",
    "website/docs/reference/profile-commands.md",
    "website/docs/guides/build-a-hermes-plugin.md",
    "website/docs/developer-guide/contributing.md",
    "website/scripts/generate-llms-txt.py",
    "website/scripts/generate-skill-docs.py",
    "website/scripts/prebuild.mjs",
    "website/static/api/model-catalog.json",
    "website/static/llms.txt",
    "website/static/llms-full.txt",
]

FORBIDDEN_UPSTREAM_VISIBLE_RESIDUES = [
    "Hermes Agent",
    "NousResearch/hermes-agent",
    "raw.githubusercontent.com/NousResearch/hermes-agent",
    "github:NousResearch/hermes-agent",
    "hermes-agent.nousresearch.com",
    "README.es.md",
    "README.zh-CN.md",
    "README.ur-pk.md",
    "created by Nous Research",
    "security@nousresearch.com",
]

FORBIDDEN_HADES_STORAGE_WITHOUT_MIGRATION = [
    "~/.hades",
    "$HOME/.hades",
    "%LOCALAPPDATA%\\hades",
    "%LOCALAPPDATA%\\Hades",
    "$env:LOCALAPPDATA\\hades",
    "$env:LOCALAPPDATA\\Hades",
]

ALLOWED_NOUSRESEARCH_REPOS = {
    "atropos",
    "hermes-example-plugins",
    "nous-account-service",
}
FORBIDDEN_NOUSRESEARCH_REPO_RE = re.compile(r"NousResearch/([A-Za-z0-9_.-]+)")
HADES_HOME_DEFAULT_CONTRACT_FORWARD_RE = re.compile(
    r"HADES_HOME.{0,120}(default|primary|canonical|data directory|home directory)",
    re.IGNORECASE,
)
HADES_HOME_DEFAULT_CONTRACT_REVERSE_RE = re.compile(
    r"(default|primary|canonical|data directory|home directory).{0,120}HADES_HOME",
    re.IGNORECASE,
)
HADES_HOME_SECOND_ROOT_FORWARD_RE = re.compile(
    r"HADES_HOME.{0,200}("
    r"~/.hades|\$HOME/.hades|%LOCALAPPDATA%\\hades|%LOCALAPPDATA%\\Hades|"
    r"\$env:LOCALAPPDATA\\hades|\$env:LOCALAPPDATA\\Hades"
    r")",
    re.IGNORECASE | re.DOTALL,
)
HADES_HOME_SECOND_ROOT_REVERSE_RE = re.compile(
    r"("
    r"~/.hades|\$HOME/.hades|%LOCALAPPDATA%\\hades|%LOCALAPPDATA%\\Hades|"
    r"\$env:LOCALAPPDATA\\hades|\$env:LOCALAPPDATA\\Hades"
    r").{0,200}HADES_HOME",
    re.IGNORECASE | re.DOTALL,
)


def _website_src_files() -> list[Path]:
    src_root = REPO_ROOT / "website" / "src"
    return sorted(
        path
        for path in src_root.rglob("*")
        if path.suffix in {".js", ".jsx", ".ts", ".tsx", ".md", ".mdx"}
    )


def test_localized_docs_are_removed() -> None:
    remaining = [
        rel for rel in REMOVED_LOCALIZED_DOC_PATHS if (REPO_ROOT / rel).exists()
    ]
    assert remaining == []


def test_docusaurus_is_english_only() -> None:
    config = (REPO_ROOT / "website" / "docusaurus.config.ts").read_text(
        encoding="utf-8"
    )

    assert "localeDropdown" not in config
    assert "zh-Hans" not in config
    assert "language: ['en']" in config


def test_high_signal_docs_have_hades_visible_chrome() -> None:
    offenders: list[str] = []
    paths = [REPO_ROOT / rel for rel in HIGH_SIGNAL_DOCS] + _website_src_files()

    for path in paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for residue in FORBIDDEN_UPSTREAM_VISIBLE_RESIDUES:
            if residue in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {residue}")
        for match in FORBIDDEN_NOUSRESEARCH_REPO_RE.finditer(text):
            repo_name = match.group(1).removesuffix(".git")
            if repo_name not in ALLOWED_NOUSRESEARCH_REPOS:
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}: {match.group(0)}"
                )
        for residue in FORBIDDEN_HADES_STORAGE_WITHOUT_MIGRATION:
            if residue in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {residue}")
        if HADES_HOME_DEFAULT_CONTRACT_FORWARD_RE.search(
            text
        ) or HADES_HOME_DEFAULT_CONTRACT_REVERSE_RE.search(text):
            offenders.append(
                f"{path.relative_to(REPO_ROOT)}: HADES_HOME documented as default contract"
            )
        if HADES_HOME_SECOND_ROOT_FORWARD_RE.search(
            text
        ) or HADES_HOME_SECOND_ROOT_REVERSE_RE.search(text):
            offenders.append(
                f"{path.relative_to(REPO_ROOT)}: HADES_HOME paired with Hades storage root"
            )

    assert offenders == []


def test_legacy_policy_documents_hades_home_alias_only() -> None:
    text = (REPO_ROOT / "docs/LEGACY_COMPATIBILITY.md").read_text(encoding="utf-8")

    assert "HERMES_HOME" in text
    assert "HADES_HOME" in text
    assert "alias" in text.lower()
    assert "must not override `HERMES_HOME`" in text
    assert "must not create a second root" in text


def test_installer_defaults_preserve_hermes_storage_contracts() -> None:
    install_sh = (REPO_ROOT / "scripts/install.sh").read_text(encoding="utf-8")
    install_ps1 = (REPO_ROOT / "scripts/install.ps1").read_text(encoding="utf-8")

    assert 'HERMES_HOME="${HERMES_HOME:-${HADES_HOME:-$HOME/.hermes}}"' in install_sh
    assert "$HOME/.hermes" in install_sh
    assert "HADES_HOME:-$HOME/.hermes" in install_sh
    assert 'if ($env:HERMES_HOME)' in install_ps1
    assert 'elseif ($env:HADES_HOME)' in install_ps1
    assert '$PSBoundParameters.ContainsKey("InstallDir")' in install_ps1
    assert 'Join-Path $HermesHome "hermes-agent"' in install_ps1
    assert "$env:LOCALAPPDATA\\hermes" in install_ps1
    assert "$env:HERMES_HOME" in install_ps1
    assert "$env:HADES_HOME" in install_ps1

    forbidden_defaults = [
        "~/.hades",
        "$HOME/.hades",
        "%LOCALAPPDATA%\\hades",
        "%LOCALAPPDATA%\\Hades",
        "$env:LOCALAPPDATA\\hades",
        "$env:LOCALAPPDATA\\Hades",
    ]
    for text in (install_sh, install_ps1):
        for forbidden in forbidden_defaults:
            assert forbidden not in text


def test_readme_windows_git_copy_matches_installer() -> None:
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "PortableGit" in text
    assert "MinGit download" not in text
