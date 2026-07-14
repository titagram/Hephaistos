from pathlib import Path

import pytest

from hermes_cli.gnothi.collectors.base import CollectorContext, CollectorResult
from hermes_cli.gnothi.collectors.capabilities import CapabilityCollector
from hermes_cli.gnothi.collectors.source import SourceCollector


def _context(workspace: Path) -> CollectorContext:
    return CollectorContext(
        workspace_root=workspace,
        generation_id="git:abc123",
        generation_scope="stable",
        head_commit="abc123",
        collected_at="2026-07-14T12:00:00Z",
    )


def test_collector_result_exposes_the_complete_boundary_contract():
    result = CollectorResult(
        name="fixture",
        status="current",
        nodes=[],
        edges=[],
        evidence=[],
        fingerprint="sha256:abc",
        verified_at="2026-07-14T12:00:00Z",
        error_code=None,
    )

    assert result.name == "fixture"
    assert result.status == "current"
    assert result.nodes == []
    assert result.edges == []
    assert result.evidence == []
    assert result.fingerprint == "sha256:abc"
    assert result.verified_at == "2026-07-14T12:00:00Z"
    assert result.error_code is None


def test_source_collector_emits_source_anatomy_without_absolute_paths(tmp_path: Path):
    workspace = tmp_path / "demo"
    source_dir = workspace / "src"
    source_dir.mkdir(parents=True)
    (workspace / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\nversion = '1.0.0'\n",
        encoding="utf-8",
    )
    (source_dir / "app.py").write_text(
        "class Greeter:\n    def greet(self):\n        return 'hello'\n",
        encoding="utf-8",
    )

    result = SourceCollector().collect(_context(workspace))

    assert result.status == "current"
    assert {node["kind"] for node in result.nodes} >= {
        "workspace",
        "source_file",
        "symbol",
    }
    assert any(edge["kind"] == "contains" for edge in result.edges)
    assert result.evidence
    assert all(node["evidence_refs"] for node in result.nodes)
    assert str(tmp_path) not in str(result.nodes)
    assert str(tmp_path) not in str(result.edges)
    assert str(tmp_path) not in str(result.evidence)

    repeated = SourceCollector().collect(_context(workspace))
    assert repeated.fingerprint == result.fingerprint


def test_source_collector_degrades_without_exposing_parser_error(
    tmp_path: Path,
    monkeypatch,
):
    workspace = tmp_path / "demo"
    workspace.mkdir()
    (workspace / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    from hermes_cli.gnothi.collectors import source

    real_execute_job = source.execute_job

    def failing_parser(job, *, workspace_root):
        if job["capability"] == "populate_backend_ast":
            raise RuntimeError(f"parser failed at {workspace_root}; token=secret")
        return real_execute_job(job, workspace_root=workspace_root)

    monkeypatch.setattr(source, "execute_job", failing_parser)

    result = SourceCollector().collect(_context(workspace))

    assert result.status == "partial"
    assert result.error_code == "RuntimeError"
    assert "parser failed" not in str(result)
    assert "token=secret" not in str(result)
    assert any(node["kind"] == "source_file" for node in result.nodes)


class _FakeToolRegistry:
    def get_all_tool_names(self):
        return ["github_search", "terminal"]

    def get_toolset_for_tool(self, name):
        return {"github_search": "mcp-github", "terminal": "core"}[name]

    def check_toolset_requirements(self):
        return {"core": True, "mcp-github": True}


def test_capability_collector_inventories_providers_and_state_dimensions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    skills_root = tmp_path / "skills"
    skill_root = skills_root / "demo-skill"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\n"
        "name: demo-skill\n"
        "description: Demonstration skill\n"
        "metadata:\n"
        "  hermes:\n"
        "    requires_tools: [terminal]\n"
        "---\n"
        "Never inventory this body token=skill-body-secret.\n",
        encoding="utf-8",
    )

    from hermes_cli.gnothi.collectors import capabilities
    from hermes_cli.commands import CommandDef

    monkeypatch.setattr(capabilities.tool_registry, "discover_builtin_tools", lambda: [])
    monkeypatch.setattr(capabilities.tool_registry, "registry", _FakeToolRegistry())
    monkeypatch.setattr(
        capabilities.commands,
        "COMMAND_REGISTRY",
        [CommandDef("doctor", "Inspect installation", "Info")],
    )
    monkeypatch.setattr(
        capabilities.skill_utils,
        "get_all_skills_dirs",
        lambda: [skills_root],
    )
    monkeypatch.setattr(
        capabilities.skill_utils,
        "get_disabled_skill_names",
        lambda: set(),
    )
    monkeypatch.setattr(capabilities.plugins, "discover_plugins", lambda: None)
    monkeypatch.setattr(
        capabilities.plugins,
        "get_plugin_manager",
        lambda: type(
            "PluginManagerFixture",
            (),
            {
                "list_plugins": lambda self: [
                    {
                        "name": "enabled-plugin",
                        "source": "bundled",
                        "enabled": True,
                        "error": "load failed token=plugin-secret",
                    },
                    {
                        "name": "disabled-plugin",
                        "source": "user",
                        "enabled": False,
                        "error": None,
                    },
                ]
            },
        )(),
    )
    monkeypatch.setattr(
        capabilities.config,
        "load_config",
        lambda: {
            "mcp_servers": {
                "github": {
                    "enabled": True,
                    "env": {"TOKEN": "mcp-env-secret"},
                    "headers": {"Authorization": "Bearer header-secret"},
                },
                "archive": {
                    "enabled": False,
                    "api_key": "mcp-api-secret",
                },
            }
        },
    )

    result = CapabilityCollector().collect(_context(tmp_path))

    assert result.status == "current"
    providers = {node["label"]: node for node in result.nodes if node["kind"] == "provider"}
    capability_nodes = {
        node["label"]: node for node in result.nodes if node["kind"] == "capability"
    }
    assert {
        "toolset:core",
        "commands",
        "skill:demo-skill",
        "plugin:enabled-plugin",
        "plugin:disabled-plugin",
        "mcp:github",
        "mcp:archive",
    } <= providers.keys()
    assert {
        "tool:terminal",
        "command:doctor",
        "skill:demo-skill",
        "plugin:enabled-plugin",
        "mcp:github",
    } <= capability_nodes.keys()
    assert capability_nodes["tool:terminal"]["state"] == {
        "declared": True,
        "installed": True,
        "available": True,
        "active": True,
        "verified": False,
        "degraded": False,
        "candidate": False,
    }
    assert capability_nodes["plugin:enabled-plugin"]["state"]["active"] is True
    assert capability_nodes["plugin:enabled-plugin"]["state"]["degraded"] is True
    assert capability_nodes["plugin:disabled-plugin"]["state"]["active"] is False
    assert capability_nodes["mcp:github"]["state"]["available"] is True
    assert capability_nodes["mcp:archive"]["state"]["available"] is False
    assert any(edge["kind"] == "provides" for edge in result.edges)

    skill_id = capability_nodes["skill:demo-skill"]["id"]
    terminal_id = capability_nodes["tool:terminal"]["id"]
    assert any(
        edge["kind"] == "requires"
        and edge["from"] == skill_id
        and edge["to"] == terminal_id
        for edge in result.edges
    )
    assert {node["owner"]["class"] for node in result.nodes} >= {
        "core",
        "user-local",
        "bundled",
        "external-service",
    }
    serialized = str(result)
    assert "skill-body-secret" not in serialized
    assert "plugin-secret" not in serialized
    assert "mcp-env-secret" not in serialized
    assert "header-secret" not in serialized
    assert "mcp-api-secret" not in serialized
