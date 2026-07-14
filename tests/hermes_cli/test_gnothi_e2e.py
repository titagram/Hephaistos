from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from hermes_cli import plugins
from hermes_cli.gnothi.collectors import capabilities
from hermes_cli.gnothi.builder import build_organism_revision, drift_status
from hermes_cli.gnothi.events import emit_experience_event
from hermes_cli.gnothi.query import OrganismQuery
from hermes_cli.gnothi.store import OrganismRevisionStore
from hermes_cli.gnothi.wiki import render_wiki


def _files(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_real_path_complete_organism_awareness(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "fixture-workspace"
    hermes_home = tmp_path / "home"
    workspace.mkdir()
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)

    source = workspace / "fixture_agent.py"
    source.write_text(
        "def fixture_capability():\n"
        "    return 'fixture-secret-must-not-leak'\n",
        encoding="utf-8",
    )
    test_source = workspace / "test_fixture_agent.py"
    test_source.write_text(
        "from fixture_agent import fixture_capability\n\n"
        "def test_fixture():\n"
        "    assert fixture_capability()\n",
        encoding="utf-8",
    )
    (workspace / "pyproject.toml").write_text(
        "[project]\n"
        "name = 'gnothi-fixture'\n"
        "version = '1.0.0'\n"
        "dependencies = ['PyYAML>=6']\n",
        encoding="utf-8",
    )

    skill_dir = hermes_home / "skills" / "fixture-skill"
    skill_dir.mkdir(parents=True)
    skill = skill_dir / "SKILL.md"
    original_skill = (
        "---\n"
        "name: fixture-skill\n"
        "description: Inspect the fixture.\n"
        "metadata:\n"
        "  hermes:\n"
        "    requires_tools: [terminal]\n"
        "---\n"
        "Body secret fixture-skill-secret-must-not-leak.\n"
    )
    skill.write_text(original_skill, encoding="utf-8")

    plugin_dir = hermes_home / "plugins" / "fixture-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        "name: fixture-plugin\n"
        "version: 1.0.0\n"
        "description: Fixture plugin.\n"
        "author: Hades\n"
        "kind: standalone\n",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        "def register(ctx):\n    return None\n",
        encoding="utf-8",
    )
    (hermes_home / "config.yaml").write_text(
        "plugins:\n"
        "  enabled: [fixture-plugin]\n"
        "mcp_servers:\n"
        "  fixture-mcp:\n"
        "    enabled: true\n"
        "    command: [fixture-mcp]\n",
        encoding="utf-8",
    )

    emit_experience_event(
        event_type="tool.failed",
        generation_id="release:test",
        component_id="tool:terminal",
        capability_id="capability:terminal",
        operation="execute",
        failure_class="ExitCode",
        severity="error",
        occurred_at="2026-07-14T10:00:00Z",
    )

    # Use a fresh real manager so global discovery state from other tests does
    # not hide the temporary user plugin, then restore it automatically.
    manager = plugins.PluginManager()
    monkeypatch.setattr(plugins, "_plugin_manager", manager)
    plugins.discover_plugins(force=True)

    class FixtureToolRegistry:
        def get_all_tool_names(self):
            return ["terminal"]

        def get_toolset_for_tool(self, name):
            return "terminal"

        def check_toolset_requirements(self):
            return {"terminal": True, "mcp-fixture-mcp": False}

    monkeypatch.setattr(capabilities.tool_registry, "discover_builtin_tools", lambda: [])
    monkeypatch.setattr(capabilities.tool_registry, "registry", FixtureToolRegistry())

    workspace_before = _files(workspace)
    home_before = _files(hermes_home)
    store = OrganismRevisionStore(root=hermes_home / "gnothi_seauton")
    first = build_organism_revision(
        workspace,
        store=store,
        now="2026-07-14T11:00:00Z",
    )
    query = OrganismQuery(store)

    labels = {str(node.get("label")): node for node in first["nodes"]}
    assert {
        "fixture_agent.py",
        "skill:fixture-skill",
        "plugin:fixture-plugin",
        "mcp:fixture-mcp",
        "python:PyYAML",
        "runtime:python",
    } <= labels.keys()
    assert any(node.get("kind") == "observation" for node in first["nodes"])
    assert any(node.get("kind") == "invariant" for node in first["nodes"])

    skill_node = labels["skill:fixture-skill"]
    assert skill_node["state"] == {
        "declared": True,
        "installed": True,
        "available": True,
        "active": True,
        "verified": False,
        "degraded": False,
        "candidate": False,
    }
    assert query.inspect(skill_node["id"])["match"]["id"] == skill_node["id"]
    explanation = query.explain(skill_node["id"])
    assert any(edge["kind"] == "requires" for edge in explanation["edges"])
    assert any(node.get("label") == "tool:terminal" for node in explanation["nodes"])
    assert all(node.get("evidence_refs") for node in explanation["nodes"])

    coverage = first["organism_contract"]["coverage"]
    assert coverage["contracts"]["status"] == "partial"
    assert first["organism_contract"]["status"] == "partial"

    first_revision = first["organism_contract"]["revision_id"]
    skill.write_text(original_skill.replace("Inspect the fixture.", "Inspect changed fixture."), encoding="utf-8")
    drift = drift_status(workspace, first)
    assert drift["invalidated_domains"] == ["capabilities"]

    second = build_organism_revision(
        workspace,
        store=store,
        collector_names=["capabilities"],
        now="2026-07-14T12:00:00Z",
    )
    second_revision = second["organism_contract"]["revision_id"]
    semantic_diff = query.diff(first_revision, second_revision)
    assert semantic_diff["coverage_changes"] == [
        {"domain": "capabilities", "before": "current", "after": "current"}
    ]

    wiki = render_wiki(second)
    assert "# Gnothi Seauton" in wiki
    assert "## Evidence index" in wiki
    assert "](#evidence-" in wiki
    assert "Overall: **Partial**" in wiki

    serialized = json.dumps(second, sort_keys=True)
    assert "fixture-secret-must-not-leak" not in serialized
    assert "fixture-skill-secret-must-not-leak" not in serialized
    assert str(tmp_path) not in serialized

    # Restore the one intentional input mutation before checking that the
    # read-only pipeline changed only its own immutable store.
    skill.write_text(original_skill, encoding="utf-8")
    assert _files(workspace) == workspace_before
    home_after = _files(hermes_home)
    changed_home = {
        path
        for path in set(home_before) | set(home_after)
        if home_before.get(path) != home_after.get(path)
    }
    assert changed_home
    assert all(path.startswith("gnothi_seauton/") for path in changed_home), changed_home
