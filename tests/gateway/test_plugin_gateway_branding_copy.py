from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_platform_plugin_manifests_use_hades_visible_branding() -> None:
    forbidden = (
        "Hermes Agent",
        "Hermes agent",
        "NousResearch",
        "Nous Research",
        "Hermes wake words",
    )
    offenders: list[str] = []

    for path in sorted((ROOT / "plugins/platforms").glob("*/plugin.yaml")):
        text = path.read_text(encoding="utf-8")
        for snippet in forbidden:
            if snippet in text:
                offenders.append(f"{path.relative_to(ROOT)} contains {snippet!r}")

    assert offenders == [], "\n".join(offenders)


def test_platform_gateway_visible_copy_uses_hades_branding() -> None:
    forbidden_by_file = {
        "plugins/platforms/discord/adapter.py": (
            "Reset your Hermes session",
            "Show Hermes session status",
            "Stop the running Hermes agent",
            "Update Hermes Agent",
            "Gracefully restart the Hermes gateway",
            "start a Hermes session",
            "send to Hermes",
            "Thread created by Hermes",
            "Hermes session handoff",
            "Hermes handoff",
            "Hermes needs your input",
            "where Hermes delivers",
        ),
        "plugins/platforms/dingtalk/adapter.py": ('"title": "Hermes"',),
        "plugins/platforms/email/adapter.py": (
            '"vendor" "NousResearch"',
            'subject", "Hermes Agent"',
            'msg["Subject"] = "Hermes Agent"',
        ),
        "plugins/platforms/feishu/adapter.py": ("`hermes pairing approve`",),
        "plugins/platforms/google_chat/adapter.py": ("Restart the gateway: hermes gateway restart",),
        "plugins/platforms/homeassistant/adapter.py": ('"title": "Hermes Agent"',),
        "plugins/platforms/irc/adapter.py": (
            ":Hermes Agent",
            "Hermes Agent shutting down",
            "Connect Hermes to an IRC network",
            "hermes gateway restart",
        ),
        "plugins/platforms/mattermost/adapter.py": (
            "already a Hermes dependency",
            "where Hermes delivers",
            "hermes config edit",
        ),
        "plugins/platforms/matrix/adapter.py": (
            'device_name="Hermes Agent"',
            "where Hermes delivers",
        ),
        "plugins/platforms/photon/auth.py": (
            'DEFAULT_PROJECT_NAME = "Hermes Agent"',
            "`hermes photon setup`",
        ),
        "plugins/platforms/photon/adapter.py": (
            "Run: hermes photon setup",
            "`hermes photon setup`",
        ),
        "plugins/platforms/photon/cli.py": (
            "default: 'Hermes Agent'",
            "Hermes Agent",
            "hermes gateway start",
            "`hermes photon install-sidecar`",
            "`hermes photon telemetry on|off`",
            "`hermes photon telemetry on`",
            "`hermes photon telemetry off`",
            "hermes gateway restart",
        ),
        "plugins/platforms/slack/adapter.py": (
            "Regenerating the app from `hermes slack`",
            "`hermes slack manifest --write`",
            "Your Hermes agent on Slack",
            "Hermes adds new commands",
            "where Hermes delivers",
        ),
        "plugins/platforms/teams/adapter.py": (
            'teams app create --name "Hermes"',
            "Restart the gateway:       hermes gateway restart",
        ),
        "plugins/platforms/telegram/adapter.py": (
            "Hermes or OpenClaw",
            "'hermes gateway restart'",
        ),
        "plugins/platforms/wecom/adapter.py": (
            "hermes pairing approve",
            "'hermes gateway setup'",
        ),
        "plugins/platforms/whatsapp/adapter.py": (
            "re-run `hermes gateway`",
            "run `hermes whatsapp`",
        ),
        "plugins/platforms/ntfy/plugin.yaml": (
            "already a Hermes dependency",
            "hermes-in",
        ),
        "plugins/platforms/ntfy/adapter.py": (
            "Hermes plugin",
            "Hermes dependency",
            "Hermes platform plugin",
            "Hermes plugin loader",
            'topic: "hermes-in"',
            'publish_topic: "hermes-out"',
            "already a Hermes dependency",
        ),
        "plugins/platforms/raft/plugin.yaml": ("Hermes gateway session pipeline",),
        "gateway/platforms/whatsapp_common.py": ("*Hermes Agent*",),
        "gateway/run.py": (
            "`hermes doctor`",
            "`hermes skills config`",
            "`hermes skills install",
            "new Hermes chat",
            "Hermes session",
            "Started a new Hermes session",
            "Hermes is at the active session limit",
            "Hermes —",
            "where Hermes delivers",
            "System topic for Hermes commands",
            "Hermes Chat",
            "Hermes checks BotFather Threads",
            "normal Hermes chat",
            "Last Hermes message",
            "Hermes update finished",
            "Hermes update failed",
            "Hermes update timed out",
            "Gateway online — Hermes is back",
            "Starting Hermes Gateway",
            "configure Hermes features",
        ),
        "gateway/slash_commands.py": ("update Hermes Agent",),
        "locales/en.yaml": (
            "Starting Hermes update",
            "hermes debug share",
            "hermes kanban",
        ),
        "scripts/hermes-gateway": ("Starting Hermes Gateway",),
    }

    offenders: list[str] = []
    for relative, snippets in forbidden_by_file.items():
        text = _read(relative)
        for snippet in snippets:
            if snippet in text:
                offenders.append(f"{relative} contains {snippet!r}")

    assert offenders == [], "\n".join(offenders)


def test_plugin_gateway_compatibility_identifiers_stay_hermes_named() -> None:
    compatibility_snippets = {
        "plugins/platforms/slack/adapter.py": (
            "/hermes",
            "hermes_approve_once",
            "hermes-agent[slack]",
        ),
        "plugins/platforms/irc/adapter.py": (
            'extra.get("nickname", "hermes-bot")',
            'or "hermes-bot"',
        ),
        "plugins/platforms/irc/plugin.yaml": ("default: hermes-bot",),
        "plugins/platforms/photon/plugin.yaml": (
            "hermes photon",
            "legacy Hermes-compatible wake words",
        ),
        "plugins/platforms/photon/adapter.py": (
            r"@?hermes\s+agent",
            r"@?hermes\b",
        ),
        "gateway/platforms/api_server.py": ("X-Hermes-Session-Key",),
    }

    for relative, snippets in compatibility_snippets.items():
        text = _read(relative)
        for snippet in snippets:
            assert snippet in text
