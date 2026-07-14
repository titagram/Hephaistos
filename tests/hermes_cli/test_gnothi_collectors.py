from pathlib import Path

import pytest

from hermes_cli.gnothi.collectors.base import CollectorContext, CollectorResult
from hermes_cli.gnothi.collectors.capabilities import CapabilityCollector
from hermes_cli.gnothi.collectors.contracts import ContractCollector
from hermes_cli.gnothi.collectors.dependencies import DependencyCollector
from hermes_cli.gnothi.collectors.runtime import RuntimeCollector
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


def test_runtime_collector_emits_effective_non_secret_runtime_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from hermes_cli.gnothi.collectors import runtime

    monkeypatch.setattr(runtime, "_git_generation", lambda root: "git:deadbeef")
    monkeypatch.setattr(runtime.profiles, "get_active_profile", lambda: "research")
    monkeypatch.setattr(
        runtime.config,
        "load_config",
        lambda: {
            "model": {"default": "gpt-test", "temperature": 0.2},
            "backend": {"token": "backend-secret"},
            "api_key": "top-secret",
        },
    )
    monkeypatch.setattr(
        runtime.backend_status,
        "load_backend_status_payload",
        lambda: {
            "configured": True,
            "degraded": False,
            "awareness": {"status": "ready", "private": "awareness-secret"},
            "bindings": [{"token": "binding-secret"}, {}],
            "base_url": "https://user:password@example.test/private",
        },
    )

    result = RuntimeCollector().collect(_context(tmp_path))

    assert result.status == "current"
    labels = {node["label"] for node in result.nodes}
    assert {
        "runtime:python",
        "runtime:platform",
        "runtime:generation",
        "runtime:profile",
        "runtime:process",
        "runtime:backend",
        "config:model.default",
        "config:model.temperature",
        "config:backend.token",
        "config:api_key",
    } <= labels
    generation = next(node for node in result.nodes if node["label"] == "runtime:generation")
    assert generation["properties"]["generation_id"] == "git:deadbeef"
    backend = next(node for node in result.nodes if node["label"] == "runtime:backend")
    assert backend["properties"] == {
        "collector": "runtime",
        "configured": True,
        "degraded": False,
        "awareness_status": "ready",
        "binding_count": 2,
    }
    serialized = str(result)
    assert "backend-secret" not in serialized
    assert "top-secret" not in serialized
    assert "awareness-secret" not in serialized
    assert "binding-secret" not in serialized
    assert "user:password" not in serialized


def test_dependency_collector_probes_only_declared_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        "name = 'fixture'\n"
        "dependencies = ['demo-pkg==1.2']\n"
        "[project.scripts]\n"
        "demo-cli = 'fixture:main'\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        '{"dependencies":{'
        '"left-pad":"https://user:node-secret@example.test/pkg.tgz?token=hidden",'
        '"../escape":"1.0.0"'
        "}}",
        encoding="utf-8",
    )

    from hermes_cli.gnothi.collectors import dependencies

    probed_packages = []

    def package_version(name):
        probed_packages.append(name)
        return "1.2"

    monkeypatch.setattr(dependencies.metadata, "version", package_version)
    monkeypatch.setattr(
        dependencies.shutil,
        "which",
        lambda name: "/private/bin/demo-cli" if name == "demo-cli" else None,
    )
    monkeypatch.setattr(
        dependencies.config,
        "load_config",
        lambda: {
            "mcp_servers": {
                "github": {
                    "enabled": True,
                    "url": "https://user:secret@example.test/mcp?token=hidden",
                }
            }
        },
    )
    monkeypatch.setattr(dependencies.plugins, "discover_plugins", lambda: None)
    monkeypatch.setattr(
        dependencies.plugins,
        "get_plugin_manager",
        lambda: type(
            "PluginManagerFixture",
            (),
            {
                "list_plugins": lambda self: [
                    {
                        "name": "remote-memory",
                        "kind": "service",
                        "enabled": True,
                        "source": "pip",
                    }
                ]
            },
        )(),
    )

    result = DependencyCollector().collect(_context(tmp_path))

    assert result.status == "current"
    labels = {node["label"] for node in result.nodes}
    assert {
        "python:demo-pkg",
        "node:left-pad",
        "binary:demo-cli",
        "service:mcp:github",
        "service:plugin:remote-memory",
    } <= labels
    assert probed_packages == ["demo-pkg"]
    assert any(edge["kind"] == "requires" for edge in result.edges)
    assert "/private/bin" not in str(result)
    assert "user:secret" not in str(result)
    assert "token=hidden" not in str(result)
    assert "node-secret" not in str(result)
    assert "node:../escape" not in labels


def test_contract_collector_emits_versioned_invariants_with_real_evidence():
    repo_root = Path(__file__).resolve().parents[2]

    result = ContractCollector().collect(_context(repo_root))

    assert result.status == "current"
    invariant_ids = {
        node["id"] for node in result.nodes if node["kind"] == "invariant"
    }
    assert invariant_ids == {
        "invariant:prompt-cache-stability",
        "invariant:message-role-alternation",
        "invariant:approval-boundaries",
        "invariant:profile-isolation",
        "invariant:artifact-backward-compatibility",
        "invariant:no-secret-artifacts",
    }
    for invariant_id in invariant_ids:
        node = next(item for item in result.nodes if item["id"] == invariant_id)
        assert node["evidence_refs"]
        assert any(
            edge["kind"] == "protected_by" and edge["from"] == invariant_id
            for edge in result.edges
        )
