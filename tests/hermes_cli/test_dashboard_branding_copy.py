"""Focused dashboard/web branding regression checks.

These tests intentionally check exact user-visible strings instead of banning
all Hermes identifiers. API routes, env vars, storage keys, and compatibility
headers still use Hermes names by contract.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_dashboard_web_visible_copy_uses_hades_branding():
    forbidden_by_file = {
        "web/src/App.tsx": [
            ">Hermes<",
        ],
        "web/src/pages/SystemPage.tsx": [
            "Hermes updates are managed outside this dashboard.",
            "Update Hermes?",
            "'hermes update'",
            "hermes update",
            ">Hermes<",
            "hermes portal",
            "hermes memory setup",
            "Restore full Hermes backup?",
            "Hermes configuration",
            "Hermes team",
        ],
        "web/src/pages/ChannelsPage.tsx": [
            "\"hermes gateway start\"",
        ],
        "web/src/pages/SkillsPage.tsx": [
            "Point Hermes",
            "Hermes index",
            "hermes skills search",
        ],
        "web/src/pages/McpPage.tsx": [
            "Nous-approved MCP servers",
        ],
        "web/src/pages/ChatPage.tsx": [
            "`hermes dashboard`",
        ],
        "web/src/pages/ConfigPage.tsx": [
            "hermes-config.json",
        ],
        "web/src/lib/api.ts": [
            "Hermes dashboard server",
        ],
        "web/src/lib/gatewayClient.ts": [
            "Hermes dashboard server",
        ],
        "web/src/themes/presets.ts": [
            "\"Hermes Teal\"",
            "\"Hermes Teal (Large)\"",
            "canonical Hermes look",
            "Hermes Teal with bigger fonts",
        ],
        "hermes_cli/web_server.py": [
            'FastAPI(title="Hermes Agent"',
            '"Hermes updates are managed outside this dashboard',
            'return " ".join(["hermes", *_gateway_subcommand(profile, verb)])',
            '"description": "Run Hermes from Telegram',
            '"description": "Connect Hermes to Discord',
            '"description": "Use Hermes from Slack',
            '"description": "Control your smart home from Hermes',
            '"description": "Expose Hermes as an OpenAI-compatible',
            '"description": "iLink Bot account ID obtained through QR login in hermes gateway setup"',
            'body.bot_name or "Hermes Agent"',
            '"source_label": f"Hermes PKCE',
            '"cli_command": "hermes auth add',
            'f"hermes auth add {d.slug}"',
            "Run `hermes memory setup` to configure a new one.",
            '"hermes-index": "Hermes Index"',
            '"label": "Hermes Teal"',
            '"label": "Hermes Teal (Large)"',
            "  Hermes Web UI",
        ],
    }

    failures: list[str] = []
    for rel_path, forbidden_snippets in forbidden_by_file.items():
        text = _read(rel_path)
        for snippet in forbidden_snippets:
            if snippet in text:
                failures.append(f"{rel_path}: {snippet!r}")

    assert failures == []


def test_dashboard_plugin_headlines_use_hades_command_examples():
    failures: list[str] = []
    for path in sorted((ROOT / "web/src/i18n").glob("*.ts")):
        text = path.read_text(encoding="utf-8")
        if "hermes plugins" in text:
            failures.append(str(path.relative_to(ROOT)))

    assert failures == []


def test_dashboard_i18n_visible_copy_uses_hades_branding():
    forbidden_visible_phrases = [
        "futtasd többet a Hermest",
    ]
    failures: list[str] = []
    for path in sorted((ROOT / "web/src/i18n").glob("*.ts")):
        text = path.read_text(encoding="utf-8")
        for phrase in forbidden_visible_phrases:
            if phrase in text:
                failures.append(f"{path.relative_to(ROOT)}: {phrase!r}")

    assert failures == []


def test_dashboard_compatibility_identifiers_stay_hermes_named():
    """The visible rebrand must not rename stable wire/storage identifiers."""

    assert '"/api/hermes/update"' in _read("web/src/lib/api.ts")
    assert '"X-Hermes-Session-Token"' in _read("web/src/lib/api.ts")
    assert '"hermes-sidebar-collapsed"' in _read("web/src/App.tsx")
    assert '"hermes-update"' in _read("web/src/pages/SystemPage.tsx")
