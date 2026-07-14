from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agent import skill_utils
from hermes_constants import get_hermes_home
from hermes_cli import commands, config, plugins
from hermes_cli.gnothi.collectors.base import (
    CollectorContext,
    CollectorResult,
    fingerprint_payload,
)
from hermes_cli.gnothi.contract import stable_id
from hermes_cli.gnothi.redaction import safe_exception_class
from tools import registry as tool_registry

_BUNDLED_SKILLS_ROOT = Path(__file__).resolve().parents[3] / "skills"


def _state(
    *,
    declared: bool = True,
    installed: bool = True,
    available: bool,
    active: bool,
    degraded: bool = False,
) -> dict[str, bool]:
    return {
        "declared": declared,
        "installed": installed,
        "available": available,
        "active": active,
        "verified": False,
        "degraded": degraded,
        "candidate": False,
    }


def _evidence(kind: str, identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": stable_id("evidence", {"kind": kind, **identity}),
        "kind": kind,
        **identity,
    }


def _node(
    *,
    node_id: str,
    kind: str,
    label: str,
    owner_class: str,
    owner_id: str,
    context: CollectorContext,
    state: dict[str, bool],
    evidence_ref: str,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "kind": kind,
        "label": label,
        "owner": {"class": owner_class, "id": owner_id},
        "generation_scope": context.generation_scope,
        "state": state,
        "evidence_refs": [evidence_ref],
        "properties": {"collector": "capabilities", **(properties or {})},
        "verified_at": None,
    }


def _edge(
    kind: str,
    source: str,
    target: str,
    evidence_ref: str,
) -> dict[str, Any]:
    return {
        "id": stable_id(
            "edge",
            {"kind": kind, "source": source, "target": target},
        ),
        "kind": kind,
        "from": source,
        "to": target,
        "evidence_refs": [evidence_ref],
        "properties": {"collector": "capabilities"},
    }


def _fingerprint(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> str:
    encoded = json.dumps(
        {"nodes": nodes, "edges": edges, "evidence": evidence},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _owner_for_toolset(toolset: str) -> str:
    if toolset in {"core", "terminal", "file", "web", "browser"}:
        return "core"
    if toolset.startswith("mcp-"):
        return "external-service"
    return "third-party"


def _plugin_owner(source: object) -> str:
    normalized = str(source or "").lower()
    if "bundled" in normalized:
        return "bundled"
    if "user" in normalized or "project" in normalized:
        return "user-local"
    return "third-party"


def _skill_owner(skill_path: Path, roots: list[Path]) -> str:
    resolved = skill_path.resolve()
    try:
        resolved.relative_to(_BUNDLED_SKILLS_ROOT.resolve())
        return "bundled"
    except ValueError:
        pass
    if roots:
        try:
            resolved.relative_to(roots[0].resolve())
            return "user-local"
        except ValueError:
            pass
    return "third-party"


def _frontmatter_only(path: Path) -> dict[str, Any]:
    lines: list[str] = []
    total = 0
    with path.open(encoding="utf-8") as handle:
        first = handle.readline()
        if first.strip() != "---":
            return {}
        lines.append(first)
        for line in handle:
            total += len(line.encode("utf-8", errors="replace"))
            if total > 65_536:
                return {}
            lines.append(line)
            if line.strip() == "---":
                break
    frontmatter, _ = skill_utils.parse_frontmatter("".join(lines) + "\n")
    return frontmatter


class CapabilityCollector:
    name = "capabilities"

    def probe_fingerprint(self, context: CollectorContext) -> str:
        """Fingerprint registrations and small capability manifests only."""
        tool_registry.discover_builtin_tools()
        registry = tool_registry.registry
        toolsets = registry.check_toolset_requirements()
        tools = [
            (
                name,
                str(registry.get_toolset_for_tool(name) or "unknown"),
                bool(toolsets.get(str(registry.get_toolset_for_tool(name) or "unknown"))),
            )
            for name in sorted(registry.get_all_tool_names())
        ]
        command_rows = [
            (command.name, command.category) for command in commands.COMMAND_REGISTRY
        ]
        skill_rows = []
        for root in skill_utils.get_all_skills_dirs():
            if not root.is_dir():
                continue
            for path in skill_utils.iter_skill_index_files(root, "SKILL.md"):
                skill_rows.append(_frontmatter_only(path))

        plugins.discover_plugins()
        plugin_rows = [
            {
                "name": str(row.get("name") or row.get("key") or ""),
                "source_class": _plugin_owner(row.get("source")),
                "kind": str(row.get("kind") or ""),
                "enabled": bool(row.get("enabled")),
                "degraded": bool(row.get("error")),
            }
            for row in plugins.get_plugin_manager().list_plugins()
        ]
        manifest_rows = []
        plugin_roots = {
            (context.workspace_root / "plugins").resolve(),
            (context.workspace_root / ".hermes" / "plugins").resolve(),
            plugins.get_bundled_plugins_dir().resolve(),
            (get_hermes_home() / "plugins").resolve(),
        }
        for root in sorted(plugin_roots):
            if not root.is_dir():
                continue
            for pattern in ("**/plugin.json", "**/plugin.yaml", "**/plugin.yml"):
                for path in sorted(root.glob(pattern)):
                    if path.is_file():
                        content = path.read_bytes()[:262_144]
                        manifest_rows.append(
                            hashlib.sha256(content).hexdigest()
                        )
        raw_servers = config.load_config().get("mcp_servers", {})
        mcp_rows = []
        if isinstance(raw_servers, dict):
            mcp_rows = [
                (str(name), (entry if isinstance(entry, dict) else {}).get("enabled") is not False)
                for name, entry in sorted(raw_servers.items())
            ]
        return fingerprint_payload(
            {
                "tools": tools,
                "commands": command_rows,
                "skills": skill_rows,
                "disabled_skills": sorted(skill_utils.get_disabled_skill_names()),
                "plugins": sorted(plugin_rows, key=lambda row: row["name"]),
                "plugin_manifests": manifest_rows,
                "mcp": mcp_rows,
            }
        )

    def collect(self, context: CollectorContext) -> CollectorResult:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        try:
            tool_registry.discover_builtin_tools()
            registry = tool_registry.registry
            tool_names = registry.get_all_tool_names()
            toolsets = registry.check_toolset_requirements()
            capabilities_by_label: dict[str, str] = {}

            self._collect_tools(
                context,
                registry,
                tool_names,
                toolsets,
                nodes,
                edges,
                evidence,
                capabilities_by_label,
            )
            self._collect_commands(
                context,
                nodes,
                edges,
                evidence,
                capabilities_by_label,
            )
            self._collect_skills(
                context,
                tool_names,
                toolsets,
                nodes,
                edges,
                evidence,
                capabilities_by_label,
            )
            self._collect_plugins(
                context,
                nodes,
                edges,
                evidence,
                capabilities_by_label,
            )
            self._collect_mcp(
                context,
                tool_names,
                toolsets,
                nodes,
                edges,
                evidence,
                capabilities_by_label,
            )
        except Exception as exc:
            return CollectorResult(
                name=self.name,
                status="partial",
                nodes=nodes,
                edges=edges,
                evidence=evidence,
                fingerprint=_fingerprint(nodes, edges, evidence),
                verified_at=None,
                error_code=safe_exception_class(exc),
            )

        return CollectorResult(
            name=self.name,
            status="current",
            nodes=nodes,
            edges=edges,
            evidence=evidence,
            fingerprint=self.probe_fingerprint(context),
            verified_at=context.collected_at,
        )

    @staticmethod
    def _provider_and_capability(
        *,
        context: CollectorContext,
        provider_label: str,
        capability_label: str,
        owner_class: str,
        state: dict[str, bool],
        evidence_row: dict[str, Any],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        capabilities_by_label: dict[str, str],
        properties: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        evidence.append(evidence_row)
        provider_id = stable_id("provider", {"label": provider_label})
        capability_id = stable_id("capability", {"label": capability_label})
        if not any(node["id"] == provider_id for node in nodes):
            nodes.append(
                _node(
                    node_id=provider_id,
                    kind="provider",
                    label=provider_label,
                    owner_class=owner_class,
                    owner_id=provider_id,
                    context=context,
                    state=state,
                    evidence_ref=evidence_row["id"],
                    properties=properties,
                )
            )
        nodes.append(
            _node(
                node_id=capability_id,
                kind="capability",
                label=capability_label,
                owner_class=owner_class,
                owner_id=provider_id,
                context=context,
                state=state,
                evidence_ref=evidence_row["id"],
                properties=properties,
            )
        )
        edges.append(
            _edge(
                "provides",
                provider_id,
                capability_id,
                evidence_row["id"],
            )
        )
        capabilities_by_label[capability_label] = capability_id
        return provider_id, capability_id

    def _collect_tools(
        self,
        context: CollectorContext,
        registry: Any,
        tool_names: list[str],
        toolsets: dict[str, bool],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        capabilities_by_label: dict[str, str],
    ) -> None:
        for name in sorted(tool_names):
            toolset = str(registry.get_toolset_for_tool(name) or "unknown")
            available = bool(toolsets.get(toolset, False))
            evidence_row = _evidence(
                "tool_registration",
                {"name": name, "toolset": toolset},
            )
            self._provider_and_capability(
                context=context,
                provider_label=f"toolset:{toolset}",
                capability_label=f"tool:{name}",
                owner_class=_owner_for_toolset(toolset),
                state=_state(available=available, active=True),
                evidence_row=evidence_row,
                nodes=nodes,
                edges=edges,
                evidence=evidence,
                capabilities_by_label=capabilities_by_label,
                properties={"toolset": toolset},
            )

    def _collect_commands(
        self,
        context: CollectorContext,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        capabilities_by_label: dict[str, str],
    ) -> None:
        for command in commands.COMMAND_REGISTRY:
            evidence_row = _evidence(
                "command_registration",
                {"name": command.name, "category": command.category},
            )
            self._provider_and_capability(
                context=context,
                provider_label="commands",
                capability_label=f"command:{command.name}",
                owner_class="core",
                state=_state(available=True, active=True),
                evidence_row=evidence_row,
                nodes=nodes,
                edges=edges,
                evidence=evidence,
                capabilities_by_label=capabilities_by_label,
                properties={"category": command.category},
            )

    def _collect_skills(
        self,
        context: CollectorContext,
        tool_names: list[str],
        toolsets: dict[str, bool],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        capabilities_by_label: dict[str, str],
    ) -> None:
        roots = skill_utils.get_all_skills_dirs()
        disabled = skill_utils.get_disabled_skill_names()
        available_tools = set(tool_names)
        available_toolsets = {name for name, available in toolsets.items() if available}
        for root in roots:
            if not root.is_dir():
                continue
            for path in skill_utils.iter_skill_index_files(root, "SKILL.md"):
                frontmatter = _frontmatter_only(path)
                name = str(frontmatter.get("name") or path.parent.name)
                conditions = skill_utils.extract_skill_conditions(frontmatter)
                requirements_met = all(
                    tool in available_tools
                    for tool in conditions.get("requires_tools", [])
                ) and all(
                    toolset in available_toolsets
                    for toolset in conditions.get("requires_toolsets", [])
                )
                fallback_hidden = any(
                    tool in available_tools
                    for tool in conditions.get("fallback_for_tools", [])
                ) or any(
                    toolset in available_toolsets
                    for toolset in conditions.get("fallback_for_toolsets", [])
                )
                active = (
                    name not in disabled
                    and skill_utils.skill_matches_platform(frontmatter)
                    and skill_utils.skill_matches_environment(frontmatter)
                    and requirements_met
                    and not fallback_hidden
                )
                owner = _skill_owner(path, roots)
                evidence_row = _evidence(
                    "skill_frontmatter",
                    {"name": name, "package": path.parent.name},
                )
                _, capability_id = self._provider_and_capability(
                    context=context,
                    provider_label=f"skill:{name}",
                    capability_label=f"skill:{name}",
                    owner_class=owner,
                    state=_state(available=requirements_met, active=active),
                    evidence_row=evidence_row,
                    nodes=nodes,
                    edges=edges,
                    evidence=evidence,
                    capabilities_by_label=capabilities_by_label,
                )
                for required_tool in conditions.get("requires_tools", []):
                    required_id = capabilities_by_label.get(f"tool:{required_tool}")
                    if required_id:
                        edges.append(
                            _edge(
                                "requires",
                                capability_id,
                                required_id,
                                evidence_row["id"],
                            )
                        )

    def _collect_plugins(
        self,
        context: CollectorContext,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        capabilities_by_label: dict[str, str],
    ) -> None:
        plugins.discover_plugins()
        for row in plugins.get_plugin_manager().list_plugins():
            name = str(row.get("name") or row.get("key") or "")
            if not name:
                continue
            enabled = bool(row.get("enabled"))
            degraded = bool(row.get("error"))
            evidence_row = _evidence(
                "plugin_registration",
                {"name": name, "source_class": _plugin_owner(row.get("source"))},
            )
            self._provider_and_capability(
                context=context,
                provider_label=f"plugin:{name}",
                capability_label=f"plugin:{name}",
                owner_class=_plugin_owner(row.get("source")),
                state=_state(
                    available=enabled and not degraded,
                    active=enabled,
                    degraded=degraded,
                ),
                evidence_row=evidence_row,
                nodes=nodes,
                edges=edges,
                evidence=evidence,
                capabilities_by_label=capabilities_by_label,
            )

    def _collect_mcp(
        self,
        context: CollectorContext,
        tool_names: list[str],
        toolsets: dict[str, bool],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        capabilities_by_label: dict[str, str],
    ) -> None:
        raw_servers = config.load_config().get("mcp_servers", {})
        if not isinstance(raw_servers, dict):
            return
        for name, raw_entry in sorted(raw_servers.items()):
            entry = raw_entry if isinstance(raw_entry, dict) else {}
            enabled = entry.get("enabled") is not False
            toolset = f"mcp-{name}"
            live = bool(toolsets.get(toolset)) and any(
                tool_registry.registry.get_toolset_for_tool(tool) == toolset
                for tool in tool_names
            )
            evidence_row = _evidence(
                "mcp_configuration",
                {"name": str(name), "enabled": enabled},
            )
            self._provider_and_capability(
                context=context,
                provider_label=f"mcp:{name}",
                capability_label=f"mcp:{name}",
                owner_class="external-service",
                state=_state(available=enabled and live, active=enabled),
                evidence_row=evidence_row,
                nodes=nodes,
                edges=edges,
                evidence=evidence,
                capabilities_by_label=capabilities_by_label,
            )
